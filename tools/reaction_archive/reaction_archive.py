#!/usr/bin/env python3
"""Resumable, self-contained reaction SQLite from the pinned SaddleMill registry.

Immutable source registry, native .traj/.npz read-only. No scientific recalculation.
Existing lean JSONL can be incrementally imported, even while being written.
New frames are grouped by source path and parity checked against public ASE.
Completed SQLite supports repeated standalone JSONL exports with zero ASE reads.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback
import zlib
from . import legacy_lean as old
from .fast_frames import process_path_group

SCHEMA = 'saddlebench-reaction-archive-v1'
MAP = {'saddles':('saddle_registry','canonical_saddle_id'),
       'triplets':('reaction_observations','observation_id'),
       'reactions':('reaction_registry','canonical_reaction_id'),
       'minima':('canonical_minima','minimum_id')}
COUNT_KEYS = tuple(MAP)
DEFAULT_POINTER=old.DEFAULT_POINTER


def log(*a): print(*a,flush=True)


def packed(o):
    return zlib.compress(old.jsonline(o).encode('utf-8'),level=1)


def unpacked(b):
    return json.loads(zlib.decompress(b))


def registry_signature(path):
    s=path.stat()
    # Cheap identity lock, not a whole-file integrity certificate.
    h=hashlib.sha256()
    with path.open('rb') as f:
        h.update(f.read(262144))
        if s.st_size>262144:
            f.seek(max(0,s.st_size-262144))
            h.update(f.read(262144))
    payload={'registry_path':str(path),'size':s.st_size,'mtime_ns':s.st_mtime_ns,
             'edge_sha256':h.hexdigest()}
    payload['identity']=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return payload


def connection(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(path),timeout=90)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=90000')
    # One authoritative writer. DELETE+FULL avoids relying on WAL shared-memory
    # semantics on a network filesystem. All batches are atomic transactions.
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA temp_store=MEMORY')
    return db


def init(db,registry):
    db.executescript('''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS saddles(id TEXT PRIMARY KEY, structure_id TEXT, energy_eV REAL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS minima(id TEXT PRIMARY KEY, structure_id TEXT, energy_eV REAL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS reactions(id TEXT PRIMARY KEY, structure_id TEXT, barrier_eV REAL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS observations(
  id TEXT PRIMARY KEY,canonical_saddle_id TEXT,canonical_reaction_id TEXT,
  structure_id TEXT,saddle_group_id TEXT,mode_id TEXT,mode_status TEXT,
  minus_energy_eV REAL,plus_energy_eV REAL,
  minus_status TEXT,plus_status TEXT,data BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS idx_observation_saddle ON observations(canonical_saddle_id);
CREATE INDEX IF NOT EXISTS idx_observation_reaction ON observations(canonical_reaction_id);
CREATE INDEX IF NOT EXISTS idx_observation_mode ON observations(mode_id);
CREATE TABLE IF NOT EXISTS modes(id TEXT PRIMARY KEY,source_path TEXT,source_frame INTEGER,status TEXT,data BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS idx_modes_path ON modes(source_path,source_frame);
CREATE TABLE IF NOT EXISTS source_frames(
  kind TEXT NOT NULL,path TEXT NOT NULL,frame INTEGER NOT NULL,
  status TEXT,data BLOB,
  PRIMARY KEY(kind,path,frame));
CREATE INDEX IF NOT EXISTS idx_pending_frame ON source_frames(status,path,frame);
CREATE TABLE IF NOT EXISTS import_offsets(
  source_path TEXT PRIMARY KEY,device INTEGER NOT NULL,inode INTEGER NOT NULL,
  committed_offset INTEGER NOT NULL,head_bytes INTEGER NOT NULL,head_sha256 TEXT NOT NULL);
''')
    sig=registry_signature(registry)
    existing=db.execute("SELECT value FROM meta WHERE key='source_registry_signature'").fetchone()
    if existing and json.loads(existing[0])!=sig:
        raise RuntimeError('REGISTRY_IDENTITY_MISMATCH: existing archive belongs to different registry')
    if not existing:
        with closing(old.db_open(registry)) as r:
            counts={name:int(r.execute('SELECT COUNT(*) FROM '+old.sql_ident(table)).fetchone()[0])
                    for name,(table,_) in MAP.items()}
        db.execute("INSERT INTO meta VALUES('source_registry_signature',?)",(json.dumps(sig,sort_keys=True),))
        db.execute("INSERT INTO meta VALUES('expected_counts',?)",(json.dumps(counts,sort_keys=True),))
        db.execute("INSERT INTO meta VALUES('schema_version',?)",(SCHEMA,))
        db.execute("INSERT INTO meta VALUES('completed',?)",('NO',))
    db.commit()
    return sig


def check_registry_rows(db_ro,kind,batch):
    """Check every imported row belongs to pinned source registry, not another run."""
    table,key=MAP[kind]
    for record in batch:
        rid=str(record.get(key,''))
        if not rid: raise ValueError('IMPORT_MISSING_ID:'+kind)
        sql='SELECT '+old.sql_ident(key)+', structure_id FROM '+old.sql_ident(table)+' WHERE '+old.sql_ident(key)+'=?'
        # minima uses parent_structure_id rather than structure_id.
        if kind=='minima':
            sql='SELECT minimum_id, parent_structure_id AS structure_id FROM canonical_minima WHERE minimum_id=?'
        row=db_ro.execute(sql,(rid,)).fetchone()
        if row is None:raise ValueError('IMPORT_ID_NOT_IN_PINNED_REGISTRY:'+kind+':'+rid)
        if kind=='triplets':
            q=db_ro.execute('SELECT canonical_saddle_id,canonical_reaction_id FROM reaction_observations WHERE observation_id=?',(rid,)).fetchone()
            if (q['canonical_saddle_id']!=record.get('canonical_saddle_id') or
                    q['canonical_reaction_id']!=record.get('canonical_reaction_id')):
                raise ValueError('IMPORT_REACTION_LINK_MISMATCH:'+rid)
        elif kind!='minima' and record.get('structure_id')!=row['structure_id']:
            raise ValueError('IMPORT_STRUCTURE_ID_MISMATCH:'+rid)


def add_row(db,kind,rec,source='extracted'):
    if kind=='saddles':
        args=(str(rec['canonical_saddle_id']),rec.get('structure_id'),rec.get('energy_eV'),packed(rec))
        db.execute('INSERT OR IGNORE INTO saddles VALUES(?,?,?,?)',args)
    elif kind=='minima':
        args=(str(rec['minimum_id']),rec.get('parent_structure_id'),rec.get('energy_eV'),packed(rec))
        db.execute('INSERT OR IGNORE INTO minima VALUES(?,?,?,?)',args)
    elif kind=='reactions':
        args=(str(rec['canonical_reaction_id']),rec.get('structure_id'),rec.get('barrier_eV'),packed(rec))
        db.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?,?)',args)
    elif kind=='triplets':
        args=(str(rec['observation_id']),rec.get('canonical_saddle_id'),rec.get('canonical_reaction_id'),
              rec.get('structure_id'),rec.get('saddle_group_id'),rec.get('mode_id'),rec.get('mode_status'),
              (rec.get('minus') or {}).get('energy_eV'),(rec.get('plus') or {}).get('energy_eV'),
              (rec.get('minus') or {}).get('status'),(rec.get('plus') or {}).get('status'),packed(rec))
        db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',args)
    elif kind=='modes':
        db.execute('INSERT OR IGNORE INTO modes VALUES(?,?,?,?,?)',
                   (str(rec['mode_id']),rec.get('source_path'),rec.get('source_frame'),rec.get('status'),packed(rec)))
    else:raise ValueError('UNKNOWN_OUTPUT:'+kind)


def file_id_to_storage(value):
    """Store arbitrary-width POSIX file IDs as tagged text, never signed SQLite int.

    SQLite INTEGER is limited to signed 64-bit; Lustre can expose unsigned or
    otherwise larger inode/device identifiers. A nonnumeric prefix prevents
    SQLite's INTEGER column affinity from coercing the text back to a number.
    """
    return 'fid:' + str(int(value))


def file_id_matches(saved, actual):
    """Also accept checkpoints written by older versions with signed file IDs."""
    return str(saved) in (str(int(actual)), file_id_to_storage(actual))


def import_lean(db,registry,source_dir,batch_size=2000):
    """Incrementally import only newline-complete JSONL records; never truncate input."""
    if not source_dir: return
    src=Path(source_dir)
    if not src.is_dir(): raise FileNotFoundError(src)
    man=src/'manifest.json'
    if man.exists():
        manifest=json.loads(man.read_text())
        if Path(manifest.get('source_registry',''))!=registry:
            raise ValueError('IMPORT_SOURCE_MANIFEST_REGISTRY_MISMATCH')
    ro=old.db_open(registry)
    try:
        # Import mode records first; observation rows can then reference them.
        for kind in ('modes','saddles','reactions','minima','triplets'):
            for p in sorted((src/kind).glob('part-*.jsonl')):
                p=p.absolute()
                st=p.stat()
                with p.open('rb') as f:
                    old_offset=db.execute('SELECT * FROM import_offsets WHERE source_path=?',(str(p),)).fetchone()
                    head_bytes=int(old_offset['head_bytes']) if old_offset else min(st.st_size,8192)
                    head=hashlib.sha256(f.read(head_bytes)).hexdigest()
                    off=0
                    if old_offset:
                        if (not file_id_matches(old_offset['device'],st.st_dev) or
                            not file_id_matches(old_offset['inode'],st.st_ino) or
                            st.st_size<old_offset['committed_offset'] or
                            old_offset['head_sha256']!=head):
                            raise RuntimeError('CHANGED_IMPORT_FILE_REFUSE:'+str(p))
                        off=old_offset['committed_offset']
                    f.seek(off)
                    n=0
                    while True:
                        lines=[]
                        for _ in range(batch_size):
                            pos=f.tell(); raw=f.readline()
                            if not raw or not raw.endswith(b'\n'):
                                f.seek(pos)
                                break
                            lines.append(raw)
                        if not lines:break
                        batch=[json.loads(s) for s in lines]
                        if kind!='modes':check_registry_rows(ro,kind,batch)
                        with db:
                            for rec in batch: add_row(db,kind,rec,source='imported')
                            db.execute('INSERT INTO import_offsets VALUES(?,?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET committed_offset=excluded.committed_offset',
                                       (str(p),file_id_to_storage(st.st_dev),file_id_to_storage(st.st_ino),f.tell(),head_bytes,head))
                        n+=len(batch)
                        if len(lines)<batch_size:break
                    log('IMPORTED kind=%s file=%s new_rows=%d' %(kind,p.name,n))
    finally:ro.close()


def ensure_registry_metadata(db,registry):
    ro=old.db_open(registry)
    try:
        expected=json.loads(db.execute("SELECT value FROM meta WHERE key='expected_counts'").fetchone()[0])
        need_hints=db.execute('SELECT COUNT(*) FROM saddles').fetchone()[0]<expected['saddles']
        hints=old.load_reference_mask_hints(registry) if need_hints else {}
        for out,table,key in (('saddles','saddle_registry','canonical_saddle_id'),
                              ('reactions','reaction_registry','canonical_reaction_id'),
                              ('minima','canonical_minima','minimum_id')):
            n=0
            task={'table':table,'key':key,'start':None,'stop':None}
            sql,args=old.select_sql(ro,task)
            for row in ro.execute(sql,args):
                rid=str(row[key])
                if db.execute('SELECT 1 FROM '+out+' WHERE id=?',(rid,)).fetchone():continue
                # Original scientifically conservative geometry/mask rules.
                record=old.process_row(table,row,None,{},hints)
                add_row(db,out,record)
                n+=1
                if n%1000==0:db.commit()
            db.commit()
            log('REGISTRY_TABLE_READY table=%s newly_extracted=%d'%(out,n))
    finally:ro.close()


def missing_obs(db,oid):
    return db.execute('SELECT 1 FROM observations WHERE id=?',(str(oid),)).fetchone() is None


def refs_of_observation(row):
    refs=[]
    for side in ('minus','plus'):
        path,frame=old.source_path_and_frame(old.take(row,side+'_path'),old.take(row,side+'_frame'))
        if path:refs.append(('endpoint',path,frame))
    path,fr=old.source_path_and_frame(old.take(row,'hessian_source_path'),old.take(row,'hessian_source_frame'))
    if old.take(row,'hessian_source_frame') is None:fr=0
    if path:refs.append(('mode',path,fr))
    return refs


def populate_frame_queue(db,registry):
    ro=old.db_open(registry)
    inserted=0;missing=0
    try:
        task={'table':'reaction_observations','key':'observation_id','start':None,'stop':None}
        sql,args=old.select_sql(ro,task)
        prev=None
        for row in ro.execute(sql,args):
            oid=str(row['observation_id'])
            if oid==prev:raise RuntimeError('DUPLICATE_PROVENANCE_JOIN:'+oid)
            prev=oid
            if not missing_obs(db,oid):continue
            missing+=1
            for kind,path,frame in refs_of_observation(row):
                if kind=='mode':
                    mid=old.mode_id(path,frame)
                    if db.execute('SELECT 1 FROM modes WHERE id=?',(mid,)).fetchone():continue
                db.execute('INSERT OR IGNORE INTO source_frames(kind,path,frame) VALUES(?,?,?)',(kind,path,frame))
                inserted+=1
            if missing%1000==0:
                db.commit()
                log('QUEUED_OBSERVATIONS=%d sources_considered=%d'%(missing,inserted))
        db.commit()
    finally:ro.close()
    log('FRAME_QUEUE_READY missing_observations=%d requested_source_references=%d'%(missing,inserted))


def path_groups(db):
    # Snapshot pending keys in a TEMP table. Writing statuses while iterating
    # an indexed live query can otherwise skip/revisit source rows.
    db.execute('DROP TABLE IF EXISTS temp.todo_frames')
    db.execute('CREATE TEMP TABLE todo_frames AS SELECT kind,path,frame FROM source_frames WHERE status IS NULL')
    cursor=db.execute('SELECT kind,path,frame FROM temp.todo_frames ORDER BY path,kind,frame')
    prev=None;frames=[]
    for row in cursor:
        path=row['path']
        if prev is not None and path!=prev:
            yield (prev,frames)
            frames=[]
        prev=path
        frames.append((row['kind'],row['frame']))
    if prev is not None:yield(prev,frames)


def persist_frame_results(db,path,results):
    # Caller commits batches to avoid 90k DELETE-journal transactions on Lustre.
    for kind,fr,rec in results:
        db.execute('UPDATE source_frames SET status=?,data=? WHERE kind=? AND path=? AND frame=? AND status IS NULL',
                   (rec['status'],packed(rec),kind,path,fr))
        if kind=='mode':
            mid=old.mode_id(path,fr)
            add_row(db,'modes',{'mode_id':mid,**rec})


def extract_frames(db,workers=3,strict_ase=False,report_every=200):
    from collections import Counter
    if workers<1 or workers>16:raise ValueError('WORKERS_OUT_OF_RANGE')
    start=time.monotonic();files=0;frames=0;methods=Counter();last_commit_files=0;last_commit_frames=0
    def progress_commit():
        nonlocal last_commit_files,last_commit_frames
        if files-last_commit_files>=100 or frames-last_commit_frames>=2000:
            db.commit();last_commit_files=files;last_commit_frames=frames
    if workers==1:
        for path,refs in path_groups(db):
            p,rows,method=process_path_group((path,refs,strict_ase))
            persist_frame_results(db,p,rows)
            files+=1;frames+=len(rows);methods[method]+=1
            progress_commit()
            if files%report_every==0:log('SOURCE_PROGRESS files=%d frames=%d elapsed_s=%.1f methods=%s'%(files,frames,time.monotonic()-start,dict(methods)))
    else:
        # Bounded in-flight futures: preserve memory while keeping CPUs occupied.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            groups=iter(path_groups(db))
            pending={}
            finished=False
            while pending or not finished:
                while not finished and len(pending)<workers*3:
                    try:path,refs=next(groups)
                    except StopIteration:
                        finished=True
                        break
                    future=pool.submit(process_path_group,(path,refs,strict_ase))
                    pending[future]=path
                if not pending:continue
                done,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    path,results,method=future.result()
                    persist_frame_results(db,path,results)
                    files+=1;frames+=len(results);methods[method]+=1
                    progress_commit()
                    if files%report_every==0:log('SOURCE_PROGRESS files=%d frames=%d elapsed_s=%.1f methods=%s'%(files,frames,time.monotonic()-start,dict(methods)))
    db.commit()
    log('SOURCE_DONE files=%d frames=%d methods=%s'%(files,frames,dict(methods)))


def frame_data(db,kind,path,fr):
    if not path:return {'status':'SOURCE_PATH_ABSENT_OR_OUTSIDE_ALLOWED_ROOT'}
    row=db.execute('SELECT status,data FROM source_frames WHERE kind=? AND path=? AND frame=?',(kind,path,fr)).fetchone()
    if row is None or row['data'] is None:
        raise RuntimeError('MISSING_SOURCE_FRAME:'+kind+':'+str(path)+'#'+str(fr))
    return unpacked(row['data'])


def complete_observations(db,registry,progress_every=1000):
    ro=old.db_open(registry)
    new=0
    try:
        task={'table':'reaction_observations','key':'observation_id','start':None,'stop':None}
        sql,args=old.select_sql(ro,task)
        prev=None
        for row in ro.execute(sql,args):
            oid=str(row['observation_id'])
            if oid==prev:raise RuntimeError('DUPLICATE_PROVENANCE_JOIN:'+oid)
            prev=oid
            if not missing_obs(db,oid):continue
            sides={}
            for side in ('minus','plus'):
                path,fr=old.source_path_and_frame(old.take(row,side+'_path'),old.take(row,side+'_frame'))
                sides[side]=frame_data(db,'endpoint',path,fr)
            path,fr=old.source_path_and_frame(old.take(row,'hessian_source_path'),old.take(row,'hessian_source_frame'))
            if old.take(row,'hessian_source_frame') is None:fr=0
            mode_id=None;mode_status='NO_RECORDED_MODE_PATH'
            if path:
                mode_id=old.mode_id(path,fr)
                mode=db.execute('SELECT status FROM modes WHERE id=?',(mode_id,)).fetchone()
                if mode is None:raise RuntimeError('UNRESOLVED_MODE:'+mode_id)
                mode_status=mode['status']
            rec={'observation_id':row['observation_id'],
                 'canonical_saddle_id':old.take(row,'canonical_saddle_id'),
                 'canonical_reaction_id':old.take(row,'canonical_reaction_id'),
                 'structure_id':old.take(row,'structure_id'),
                 'saddle_group_id':old.take(row,'saddle_group_id'),
                 'protocol':old.take(row,'protocol'), 'outcome':old.take(row,'outcome'),
                 'protocol_eligible_registry_flag':old.take(row,'protocol_eligible'),
                 'source_kind':old.take(row,'source_kind'),
                 'minus':sides['minus'],'plus':sides['plus'],
                 'saddle_reference':old.take(row,'canonical_saddle_id'),
                 'mode_id':mode_id,'mode_status':mode_status}
            add_row(db,'triplets',rec)
            new+=1
            if new%progress_every==0:
                db.commit();log('OBSERVATIONS_BUILT=%d'%new)
        db.commit()
    finally:ro.close()
    log('OBSERVATIONS_DONE new=%d'%new)


def totals(db):
    return {k:int(db.execute('SELECT COUNT(*) FROM '+tbl).fetchone()[0])
            for k,tbl in [('saddles','saddles'),('triplets','observations'),
                          ('reactions','reactions'),('minima','minima'),('modes','modes')]}


def validate(db,mark=False):
    expected=json.loads(db.execute("SELECT value FROM meta WHERE key='expected_counts'").fetchone()[0])
    actual=totals(db)
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    orphan_modes=db.execute('SELECT COUNT(*) FROM observations o LEFT JOIN modes m ON o.mode_id=m.id WHERE o.mode_id IS NOT NULL AND m.id IS NULL').fetchone()[0]
    queue_left=db.execute('SELECT COUNT(*) FROM source_frames WHERE status IS NULL').fetchone()[0]
    ok=(integrity=='ok' and not orphan_modes and not queue_left and all(actual[k]==v for k,v in expected.items()))
    coverage={key:{str(row[0]):int(row[1]) for row in db.execute('SELECT '+column+', COUNT(*) FROM '+table+' GROUP BY '+column)}
              for key,table,column in [('minus','observations','minus_status'),('plus','observations','plus_status'),('modes','observations','mode_status')]}
    report={'integrity_check':integrity,'expected':expected,'actual':actual,
            'orphan_mode_references':orphan_modes,'pending_frame_count':queue_left,
            'record_status_counts':coverage,
            'complete':ok,'schema':SCHEMA}
    if mark:
        db.execute("UPDATE meta SET value=? WHERE key='completed'",('YES' if ok else 'NO',))
        db.commit()
    return report


def export_jsonl(db,out_dir,parts=3):
    if parts<1:raise ValueError('PARTS_POSITIVE_REQUIRED')
    report=validate(db)
    if not report['complete']:raise RuntimeError('ARCHIVE_INCOMPLETE: refuse publish/export')
    if out_dir.exists():raise FileExistsError('EXPORT_DESTINATION_MUST_BE_NEW:'+str(out_dir))
    out_dir.mkdir(parents=True)
    tables={'saddles':'saddles','triplets':'observations','reactions':'reactions',
            'minima':'minima','modes':'modes'}
    manifest={'schema_version':SCHEMA,'source_archive':str(db.execute('PRAGMA database_list').fetchone()[2]),
              'registry_signature':json.loads(db.execute("SELECT value FROM meta WHERE key='source_registry_signature'").fetchone()[0]),
              'counts':report['actual'],'parts':[],
              'qualification':'UNQUALIFIED_REGISTRY_EXPORT',
              'scientific_methods_recomputed':False,
              'note':'Archived constraints reflect available native evidence; saddle hints are not certified masks.'}
    for label,tbl in tables.items():
        directory=out_dir/label;directory.mkdir()
        n=db.execute('SELECT COUNT(*) FROM '+tbl).fetchone()[0]
        sizes=[]
        from contextlib import ExitStack
        with ExitStack() as stack:
            writers=[stack.enter_context((directory/('part-%03d.jsonl'%i)).open('w',encoding='utf-8',buffering=1024*1024)) for i in range(parts)]
            sizes=[0]*parts
            for j,rec in enumerate(db.execute('SELECT data FROM '+tbl+' ORDER BY id')):
                idx=min(parts-1,(j*parts)//max(1,n))
                writers[idx].write(zlib.decompress(rec[0]).decode('utf-8'))
                sizes[idx]+=1
        for i,k in enumerate(sizes):
            manifest['parts'].append({'file':label+'/part-%03d.jsonl'%i,'rows':k})
        log('EXPORT_TABLE=%s records=%d'%(label,n))
    tmp=out_dir/'manifest.json'
    tmp.write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    (out_dir/'EXPORT_COMPLETE').write_text('PASS\n')
    log('EXPORT_STATUS=PASS\nOUTPUT_MANIFEST='+str(tmp))


def backup_archive(db,dest):
    dest=dest.absolute()
    if dest.exists():raise FileExistsError('BACKUP_DESTINATION_EXISTS:'+str(dest))
    dest.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    fd,tmp=tempfile.mkstemp(prefix='.sbe_backup_',suffix='.sqlite',dir=dest.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(tmp)) as copy:
            db.backup(copy,pages=4096,sleep=0.05)
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('BACKUP_SQLITE_INTEGRITY_FAILED')
        with open(tmp,'rb') as f:os.fsync(f.fileno())
        # Atomic no-clobber publication; link fails if another process created dest.
        os.link(tmp,dest)
        os.unlink(tmp)
        log('BACKUP_COMPLETE='+str(dest))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def cli():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--registry',type=Path)
    a.add_argument('--pointer',type=Path,default=DEFAULT_POINTER)
    a.add_argument('--sqlite',type=Path,required=True)
    a.add_argument('--source-lean-run',type=Path)
    a.add_argument('--workers',type=int,default=3)
    a.add_argument('--progress-every',type=int,default=1000)
    a.add_argument('--import-only',action='store_true')
    a.add_argument('--export-only',action='store_true')
    a.add_argument('--strict-ase',action='store_true',help='Disable selective backend; use full ASE frame extraction')
    a.add_argument('--export-to',type=Path,help='New output dir; requires complete validated archive')
    a.add_argument('--status',action='store_true')
    a.add_argument('--backup-to',type=Path,help='Create independent atomic SQLite backup at a new destination path')
    args=a.parse_args()
    if args.import_only and args.export_only:a.error('--import-only and --export-only cannot be combined')
    only_sqlite=args.export_only or args.status
    if args.registry is None and not only_sqlite:
        args.registry=Path(args.pointer.read_text().strip())
    registry=args.registry.absolute() if args.registry is not None else None
    if not only_sqlite and (not registry.is_file() or registry.is_symlink()):a.error('pinned regular registry required')
    if args.progress_every<1:a.error('--progress-every must be >0')
    # All paths are distinct: output SQLite must never overwrite the source registry.
    if args.sqlite.absolute()==registry:a.error('SQLite archive cannot overwrite registry')
    start=time.monotonic()
    db=connection(args.sqlite)
    try:
        if only_sqlite:
            existing=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
            if not existing:raise RuntimeError('ARCHIVE_MISSING_SCHEMA')
            stored=db.execute("SELECT value FROM meta WHERE key='source_registry_signature'").fetchone()
            if not stored:raise RuntimeError('ARCHIVE_MISSING_PROVENANCE')
            sig=json.loads(stored[0])
            if registry is not None and registry.exists() and registry_signature(registry)!=sig:
                raise RuntimeError('REGISTRY_IDENTITY_MISMATCH')
        else:
            with closing(old.db_open(registry)) as ro:
                old.preflight(ro,max(1,args.workers))
            sig=init(db,registry)
        log('ARCHIVE='+str(args.sqlite.absolute()))
        log('REGISTRY_PIN='+sig['identity'][:16])
        if args.status:
            log('STATUS='+json.dumps(validate(db),sort_keys=True));return 0
        if not args.export_only:
            if args.source_lean_run:import_lean(db,registry,args.source_lean_run)
            if not args.import_only:
                ensure_registry_metadata(db,registry)
                populate_frame_queue(db,registry)
                extract_frames(db,args.workers,args.strict_ase)
                complete_observations(db,registry,args.progress_every)
            else:log('IMPORT_ONLY=PASS')
        report=validate(db,mark=not args.import_only)
        log('ARCHIVE_VALIDATION='+json.dumps(report,sort_keys=True))
        if args.export_to:
            export_jsonl(db,args.export_to)
        if report['complete']:
            log('ARCHIVE_COMPLETE=PASS')
        else:
            log('ARCHIVE_INCOMPLETE=YES')
        if args.backup_to:
            backup_archive(db,args.backup_to)
        log('ELAPSED_S=%.1f'%(time.monotonic()-start))
        return 0 if (args.import_only or report['complete']) else 1
    finally:db.close()

if __name__=='__main__':
    try:sys.exit(cli())
    except Exception as e:
        log('ARCHIVE_FAILED='+type(e).__name__+': '+str(e))
        traceback.print_exc()
        sys.exit(1)
