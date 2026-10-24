#!/usr/bin/env python3
"""Read-only SQLite -> lean SaddleBench geometry/triplet/unstable-mode JSONL shards.

No calculators, registry provenance audit, external file hashing, per-row NPZ
roundtrip, temp SQLite spooling, scientific qualification, or existing-output edits.
Requires numpy; ASE needed only for actual .traj endpoint / minimum reads.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import hashlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback
import zlib

import numpy as np

TABLES = (
    ('saddles', 'saddle_registry', 'canonical_saddle_id'),
    ('triplets', 'reaction_observations', 'observation_id'),
    ('reactions', 'reaction_registry', 'canonical_reaction_id'),
    ('minima', 'canonical_minima', 'minimum_id'),
)
NEEDED = {
    'saddle_registry': {'canonical_saddle_id','structure_id','geometry_blob','energy_eV','hessian_status'},
    'reaction_observations': {'observation_id','canonical_saddle_id','canonical_reaction_id','structure_id','minus_path','minus_frame','plus_path','plus_frame'},
    'reaction_registry': {'canonical_reaction_id','structure_id','barrier_eV'},
    'canonical_minima': {'minimum_id','parent_structure_id','geometry_blob','energy_eV'},
}
DEFAULT_POINTER = Path('/scratch/11588/ndn454/sm_anskey/pipeline/state/latest_canonical_registry_v1.txt')
ALLOWED_ROOTS = (Path('/scratch/11588/ndn454/sm_anskey'), Path('/work/11588/ndn454/ls6'))
SCHEMA = 'saddlebench-lean-registry-v1'


def info(line):
    print(line, flush=True)


def sql_ident(name):
    return '"' + name.replace('"', '""') + '"'


def db_open(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def columns(db, table):
    return {row[1] for row in db.execute('PRAGMA table_info(' + sql_ident(table) + ')')}


def jsonline(o):
    return json.dumps(o, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True) + '\n'


def finite_number(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def safe_scalar(v):
    if isinstance(v, np.ndarray):
        if v.size != 1: return None
        v = v.reshape(-1)[0]
    return v.item() if hasattr(v, 'item') else v


def geometry_from_blob(blob):
    if not isinstance(blob, bytes):
        raise ValueError('NON_BYTES_GEOMETRY_BLOB')
    with np.load(io.BytesIO(zlib.decompress(blob)), allow_pickle=False) as z:
        n = np.asarray(z['numbers'], dtype=np.int64)
        p = np.asarray(z['positions'], dtype=float)
        c = np.asarray(z['cell'], dtype=float).reshape(3, 3)
        b = np.asarray(z['pbc'], dtype=int)
    return make_geometry(n, p, c, b)


def safely_decode_geometry(blob):
    try:
        return geometry_from_blob(blob), 'OK'
    except (ValueError, KeyError, TypeError, OSError, zlib.error) as exc:
        return None, 'BLOB_DECODE_FAILED:' + type(exc).__name__


def make_geometry(numbers, positions, cell, pbc):
    n = np.asarray(numbers)
    p = np.asarray(positions)
    c = np.asarray(cell)
    b = np.asarray(pbc)
    if (n.ndim != 1 or len(n) == 0 or p.shape != (len(n), 3)
            or c.shape != (3, 3) or b.shape != (3,)
            or not np.all(np.isfinite(p)) or not np.all(np.isfinite(c))
            or not np.all(n > 0) or not np.isin(b, (0, 1, False, True)).all()):
        raise ValueError('INVALID_GEOMETRY_VALUES')
    return {'ordered_numbers': n.astype(int).tolist(),
            'positions_angstrom': p.astype(float).tolist(),
            'cell_angstrom': c.astype(float).tolist(),
            'pbc': b.astype(bool).tolist()}


def permitted_source(path):
    if not path or not isinstance(path, str): return False
    p = Path(path)
    if not p.is_absolute(): return False
    # No dereference of paths outside the known science trees. (No file hashing.)
    extra = [Path(x).resolve() for x in os.environ.get('SBE_EXTRA_SOURCE_ROOTS','').split(':') if x]
    try:
        resolved = p.resolve(strict=False)
        return any(resolved == root or root in resolved.parents for root in (*ALLOWED_ROOTS, *extra))
    except OSError:
        return False


def source_path_and_frame(path, frame):
    if isinstance(path, str) and '#' in path:
        head, sep, tail = path.rpartition('#')
        if sep and tail.lstrip('-').isdigit():
            path = head
            if frame is None: frame = int(tail)
    try: frame = int(frame) if frame is not None else -1
    except (TypeError, ValueError): frame = -1
    return path, frame


def native_constraints(atoms):
    descriptors, fixed = [], []
    for c in getattr(atoms, 'constraints', []):
        typ = type(c).__name__
        if typ != 'FixAtoms':
            return {'constraints_known': False, 'fixed_indices': None,
                    'constraint_status': 'UNSUPPORTED_NATIVE_CONSTRAINT:' + typ}
        indices = [int(i) for i in c.get_indices()]
        fixed.extend(indices)
        descriptors.append({'type': 'FixAtoms', 'indices': sorted(indices)})
    return {'constraints_known': True, 'fixed_indices': sorted(set(fixed)),
            'constraint_status': 'NATIVE_ENDPOINT_TRAJECTORY',
            'constraint_descriptors': descriptors}


def stored_energy(atoms):
    # Do not call get_potential_energy() since that might execute a calculator.
    calc = getattr(atoms, 'calc', None)
    res = getattr(calc, 'results', None)
    if isinstance(res, dict):
        for name in ('energy', 'free_energy'):
            e = finite_number(res.get(name))
            if e is not None: return e, 'serialized_calculator_' + name
    meta = getattr(atoms, 'info', None)
    if isinstance(meta, dict):
        for name in ('energy_eV', 'energy', 'total_energy_eV', 'potential_energy'):
            e = finite_number(meta.get(name))
            if e is not None: return e, 'native_info_' + name
    return None, 'NOT_STORED'


def read_native(path, frame):
    if not permitted_source(path):
        return {'status': 'SOURCE_PATH_ABSENT_OR_OUTSIDE_ALLOWED_ROOT'}
    if not Path(path).is_file():
        return {'status': 'SOURCE_FILE_MISSING'}
    if Path(path).suffix.lower() not in ('.traj', '.extxyz', '.xyz'):
        return {'status': 'UNSUPPORTED_ENDPOINT_FORMAT'}
    try:
        from ase.io import read
        atoms = read(path, index=frame)
        g = make_geometry(atoms.numbers, atoms.positions, atoms.cell, atoms.pbc)
        energy, source = stored_energy(atoms)
        return {'status': 'OK', 'geometry': g, 'energy_eV': energy,
                'energy_source': source, **native_constraints(atoms)}
    except Exception as exc:
        return {'status': 'ENDPOINT_READ_FAILED:' + type(exc).__name__}


@lru_cache(maxsize=128)
def cached_native(path, frame):
    return read_native(path, frame)


def mode_id(path, frame):
    return hashlib.sha256((str(path) + '#' + str(frame)).encode()).hexdigest()[:24]


def search_dict(dct, name, depth=3):
    if not isinstance(dct, dict) or depth <= 0: return None
    if name in dct: return dct[name]
    for value in dct.values():
        if isinstance(value, dict):
            found = search_dict(value, name, depth-1)
            if found is not None: return found
    return None


def read_recorded_mode(path, frame):
    if not permitted_source(path):
        return {'status': 'SOURCE_PATH_ABSENT_OR_OUTSIDE_ALLOWED_ROOT'}
    if not Path(path).is_file():
        return {'status': 'SOURCE_FILE_MISSING'}
    try:
        p = Path(path)
        if p.suffix.lower() == '.npz':
            with np.load(p, allow_pickle=False) as data:
                if 'lowest_eigenmode' not in data.files:
                    return {'status': 'MODE_ABSENT'}
                vec = np.asarray(data['lowest_eigenmode'])
                eigs = data['order_eigenvalues'] if 'order_eigenvalues' in data.files else (
                    data['eigenvalues'] if 'eigenvalues' in data.files else None)
                eig = safe_scalar(np.asarray(eigs).reshape(-1)[0]) if eigs is not None and np.asarray(eigs).size else None
                projection = safe_scalar(data['order_projection']) if 'order_projection' in data.files else None
                neg = safe_scalar(data['negative_mode_count']) if 'negative_mode_count' in data.files else None
                tol = safe_scalar(data['negative_eigenvalue_tolerance']) if 'negative_eigenvalue_tolerance' in data.files else None
        elif p.suffix.lower() == '.traj':
            from ase.io import read
            atoms = read(path, index=frame if frame is not None else 0)
            meta = getattr(atoms, 'info', {})
            raw = search_dict(meta, 'lowest_eigenmode')
            if raw is None: raw = search_dict(meta, 'eigenmode')
            if raw is None: return {'status': 'MODE_ABSENT'}
            vec = np.asarray(raw)
            eig = search_dict(meta, 'lowest_eigenvalue')
            projection = search_dict(meta, 'order_projection')
            neg = search_dict(meta, 'negative_mode_count')
            tol = search_dict(meta, 'negative_eigenvalue_tolerance')
        else:
            return {'status': 'UNSUPPORTED_MODE_FILE_TYPE'}
        if vec.ndim != 2 or vec.shape[1] != 3 or not np.issubdtype(vec.dtype, np.number):
            return {'status': 'INVALID_MODE_SHAPE'}
        if not np.isfinite(vec).all(): return {'status': 'INVALID_MODE_VALUES'}
        if not np.linalg.norm(vec) > 1e-14: return {'status': 'ZERO_MODE'}
        return {'status': 'OK', 'vector': vec.astype(float).tolist(),
                'shape': list(vec.shape), 'lowest_eigenvalue_eV_A2': finite_number(eig),
                'negative_mode_count': int(neg) if neg is not None else None,
                'negative_eigenvalue_tolerance_eV_A2': finite_number(tol),
                'order_projection': str(projection) if projection is not None else None,
                'mode_geometry_parity': 'UNVERIFIED', 'source_path': path, 'source_frame': frame}
    except Exception as exc:
        return {'status': 'MODE_READ_FAILED:' + type(exc).__name__}


@lru_cache(maxsize=32)
def cached_mode(path, frame):
    return read_recorded_mode(path, frame)



def load_reference_mask_hints(registry):
    """Read the small canonical-minima catalog once for non-authoritative mask hints.

    No topology-based structure equivalence or scientific identity is asserted.
    A hint is exported only when one compatible index mask is unambiguous.
    """
    db = db_open(registry)
    catalog = {}
    try:
        c = columns(db, 'canonical_minima')
        if not {'parent_structure_id', 'object_path', 'geometry_blob'}.issubset(c): return {}
        for row in db.execute('SELECT minimum_id, parent_structure_id, object_path, geometry_blob FROM canonical_minima'):
            sid = row['parent_structure_id']
            if not sid: continue
            native = cached_native(row['object_path'], -1) if row['object_path'] else None
            geo, status = safely_decode_geometry(row['geometry_blob'])
            if not native or native.get('status')!='OK' or not native.get('constraints_known') or geo is None:
                continue
            ng = native.get('geometry')
            if not ng or any(ng[k] != geo[k] for k in ('ordered_numbers','cell_angstrom','pbc')):
                continue
            if not np.allclose(np.asarray(ng['positions_angstrom']), np.asarray(geo['positions_angstrom']),
                               rtol=0.0, atol=1e-8):
                continue
            entry = {'ordered_numbers':geo['ordered_numbers'], 'cell_angstrom':geo['cell_angstrom'],
                     'pbc':geo['pbc'], 'fixed_indices':native['fixed_indices'],
                     'source_minimum_ids':[row['minimum_id']]}
            if sid not in catalog:
                catalog[sid] = entry
            elif catalog[sid] is not None:
                old=catalog[sid]
                if any(entry[k] != old[k] for k in ('ordered_numbers','cell_angstrom','pbc','fixed_indices')):
                    catalog[sid]=None  # conflicting constraints or indexing; no hint
                else:
                    old['source_minimum_ids'].append(row['minimum_id'])
    finally: db.close()
    return {k:v for k,v in catalog.items() if v is not None}


def shard_tasks(db, workers):
    tasks = []
    for output_name, table, key in TABLES:
        n = int(db.execute('SELECT COUNT(*) FROM ' + sql_ident(table)).fetchone()[0])
        count = min(workers, n) if n else 1
        edges = [None]
        for i in range(1, count):
            offset = n*i//count
            row = db.execute('SELECT ' + sql_ident(key) + ' FROM ' + sql_ident(table) +
                             ' ORDER BY ' + sql_ident(key) + ' LIMIT 1 OFFSET ?', (offset,)).fetchone()
            if not row: raise ValueError('SHARD_BOUNDARY_MISSING:' + table)
            edges.append(str(row[0]))
        edges.append(None)
        for i in range(count):
            tasks.append({'output_name':output_name, 'table':table, 'key':key, 'idx':i,
                          'start':edges[i], 'stop':edges[i+1], 'expected': (n*(i+1)//count) - (n*i//count)})
    return tasks


def select_sql(db, task):
    tab, key = task['table'], task['key']
    cols = columns(db, tab)
    extra = []
    if tab == 'saddle_registry':
        names = ['canonical_saddle_id','structure_id','geometry_blob','energy_eV','hessian_status','reaction_eligible']
    elif tab == 'canonical_minima':
        names = ['minimum_id','parent_structure_id','geometry_blob','energy_eV','certification_status','object_path']
    elif tab == 'reaction_registry':
        names = ['canonical_reaction_id','structure_id','saddle_group_id','status','connected_to_canonical_minimum','rate_relevant','barrier_eV']
    else:
        names = ['observation_id','structure_id','canonical_saddle_id','canonical_reaction_id',
                 'saddle_group_id','minus_path','minus_frame','plus_path','plus_frame',
                 'protocol','outcome','source_kind','protocol_eligible']
    names = [x for x in names if x in cols]
    if key not in names: raise ValueError('MISSING_KEY:' + key)
    left_join = ''
    if tab == 'reaction_observations' and 'reaction_observation_provenance' in {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        pc = columns(db, 'reaction_observation_provenance')
        if {'observation_id', 'hessian_source_path'}.issubset(pc):
            for n in ['hessian_source_path','hessian_source_frame']:
                if n in pc: extra.append('p.'+sql_ident(n)+' AS '+sql_ident(n))
            left_join = ' LEFT JOIN reaction_observation_provenance p ON o.observation_id=p.observation_id'
    alias = 'o'
    projection = ', '.join([alias+'.'+sql_ident(x) for x in names] + extra)
    bounds, args = [], []
    if task['start'] is not None: bounds.append(alias+'.'+sql_ident(key)+'>=?'); args.append(task['start'])
    if task['stop'] is not None: bounds.append(alias+'.'+sql_ident(key)+'<?'); args.append(task['stop'])
    where = ' WHERE '+ ' AND '.join(bounds) if bounds else ''
    sql = ('SELECT '+projection+' FROM '+sql_ident(tab)+' '+alias + left_join + where +
           ' ORDER BY '+alias+'.'+sql_ident(key))
    return sql, args


def take(row, key):
    try: return row[key]
    except (IndexError, KeyError): return None


def process_row(tab, row, modes_file, known_modes, mask_hints=None):
    if tab == 'saddle_registry':
        geometry, status = safely_decode_geometry(row['geometry_blob'])
        hint = (mask_hints or {}).get(take(row,'structure_id'))
        hint_ok = bool(hint and geometry and all(geometry[k] == hint[k] for k in
                       ('ordered_numbers','cell_angstrom','pbc')))
        return {'canonical_saddle_id':row['canonical_saddle_id'],
                'structure_id': take(row,'structure_id'),
                'geometry':geometry, 'geometry_status':status,
                'energy_eV':finite_number(row['energy_eV']),
                'hessian_status': take(row,'hessian_status'),
                'reaction_eligible_registry_flag': take(row,'reaction_eligible'),
                'constraints_known': False, 'fixed_indices': None,
                'constraint_status':'NOT_IN_REGISTRY_BLOB',
                'constraint_hint_fixed_indices':hint['fixed_indices'] if hint_ok else None,
                'constraint_hint_minimum_ids':hint['source_minimum_ids'] if hint_ok else None,
                'constraint_hint_status':'REFERENCE_MINIMUM_INDEX_MASK_UNVERIFIED' if hint_ok else 'UNAVAILABLE'}
    if tab == 'canonical_minima':
        path = take(row, 'object_path')
        native = cached_native(path, -1) if path else {'status':'SOURCE_PATH_ABSENT'}
        native_geom = native.get('geometry')
        # Mask assignment requires aligned numbers/cell/pbc (no inference of scientific identity).
        reggeom, geom_status = safely_decode_geometry(row['geometry_blob'])
        # Mask is transferred only for exactly aligned index-level geometry,
        # not based on topology or an inferred structure identity.
        aligned = bool(native_geom and reggeom and all(native_geom[k] == reggeom[k] for k in ('ordered_numbers','cell_angstrom','pbc'))
                       and np.allclose(np.asarray(native_geom['positions_angstrom']),
                                       np.asarray(reggeom['positions_angstrom']), rtol=0.0, atol=1e-8))
        return {'minimum_id':row['minimum_id'], 'parent_structure_id':take(row,'parent_structure_id'),
                'geometry':reggeom, 'geometry_status':geom_status, 'energy_eV':finite_number(row['energy_eV']),
                'certification_status':take(row,'certification_status'),
                'fixed_indices':native.get('fixed_indices') if aligned else None,
                'constraints_known':bool(aligned and native.get('constraints_known')),
                'constraint_status':'NATIVE_MINIMUM_OBJECT_ALIGNED_BY_ATOM_INDEX' if aligned else 'MINIMUM_MASK_UNAVAILABLE',
                'source_geometry_displacement_not_used_for_identity':True}
    if tab == 'reaction_registry':
        return {'canonical_reaction_id':row['canonical_reaction_id'],
                'structure_id':take(row,'structure_id'),
                'saddle_group_id':take(row,'saddle_group_id'),
                'status':take(row,'status'),
                'connected_to_canonical_minimum':take(row,'connected_to_canonical_minimum'),
                'rate_relevant_registry_flag':take(row,'rate_relevant'),
                'barrier_eV':finite_number(take(row,'barrier_eV'))}
    oid = row['observation_id']
    sides = {}
    for side in ('minus','plus'):
        path, frame = source_path_and_frame(row[side+'_path'], row[side+'_frame'])
        record = cached_native(path, frame)
        sides[side] = record
    raw_mode_path, mode_frame = source_path_and_frame(take(row,'hessian_source_path'), take(row,'hessian_source_frame'))
    if take(row,'hessian_source_frame') is None: mode_frame = 0
    mr = None
    mode_status = 'NO_RECORDED_MODE_PATH'
    if raw_mode_path:
        mid = mode_id(raw_mode_path, mode_frame)
        if mid not in known_modes:
            mode = cached_mode(raw_mode_path, mode_frame)
            modes_file.write(jsonline({'mode_id':mid,**mode}))
            known_modes[mid] = mode['status']
        mr = mid
        mode_status = known_modes[mid]
    return {'observation_id':oid, 'canonical_saddle_id':take(row,'canonical_saddle_id'),
            'canonical_reaction_id':take(row,'canonical_reaction_id'),
            'structure_id':take(row,'structure_id'), 'saddle_group_id':take(row,'saddle_group_id'),
            'protocol':take(row,'protocol'),'outcome':take(row,'outcome'),
            'protocol_eligible_registry_flag':take(row,'protocol_eligible'),
            'source_kind':take(row,'source_kind'),
            'minus':sides['minus'], 'plus':sides['plus'],
            'saddle_reference':take(row,'canonical_saddle_id'),
            'mode_id':mr, 'mode_status':mode_status}


def process_shard(db_path, task, out, every, mask_hints):
    os.environ['OMP_NUM_THREADS']='1'
    start = time.monotonic()
    tab, key, idx = task['table'], task['key'], task['idx']
    db = db_open(Path(db_path))
    name = task['output_name']
    outbase=Path(out)
    outpath=outbase/name/('part-%03d.jsonl'%idx)
    modepath=outbase/'modes'/('part-%03d.jsonl'%idx) if tab == 'reaction_observations' else Path(os.devnull)
    count, first, last = 0, None, None
    statuses = Counter()
    known_modes = {}
    # Create files before work so status monitoring can see this shard immediately.
    with outpath.open('w', encoding='utf-8', buffering=1024*1024) as w, modepath.open('w',encoding='utf-8',buffering=1024*1024) as mw:
        sql, args = select_sql(db, task)
        for row in db.execute(sql, args):
            cur = str(row[key]);
            if cur == last: raise ValueError('DUPLICATE_OBSERVATION_OR_JOIN:'+cur)
            if last is not None and cur < last: raise ValueError('UNSORTED_INPUT:'+cur)
            if first is None: first=cur
            last=cur
            rec=process_row(tab,row,mw,known_modes,mask_hints)
            if tab == 'reaction_observations':
                statuses['minus:' + rec['minus']['status']]+=1
                statuses['plus:' + rec['plus']['status']]+=1
                statuses['mode:' + rec['mode_status']]+=1
            elif tab in ('saddle_registry', 'canonical_minima'):
                statuses['geometry:' + rec['geometry_status']]+=1
                if tab == 'canonical_minima': statuses['minimum_mask:' + rec['constraint_status']]+=1
            w.write(jsonline(rec)); count+=1
            if count%every == 0:
                w.flush(); mw.flush()
                sec = max(1e-6,time.monotonic()-start)
                info('PROGRESS table=%s shard=%d records=%d expected=%d pct=%.1f elapsed_s=%.1f rows_per_s=%.2f' %
                     (tab,idx,count,task['expected'],100*count/max(1,task['expected']),sec,count/sec))
    db.close()
    if count != task['expected']:
        raise ValueError('SHARD_COUNT_MISMATCH table=%s index=%d expected=%d actual=%d' % (tab,idx,task['expected'],count))
    info('SHARD_PASS table=%s shard=%d rows=%d modes=%d elapsed_s=%.1f' %
         (tab,idx,count,len(known_modes),time.monotonic()-start))
    return {'table':tab,'part':outpath.relative_to(outbase).as_posix(),
            'rows':count,'first_id':first,'last_id':last,
            'mode_part':modepath.relative_to(outbase).as_posix() if tab == 'reaction_observations' else None,'unique_mode_keys_in_shard':len(known_modes), 'statuses':dict(statuses)}


def preflight(db, workers):
    present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for _, t, k in TABLES:
        if t not in present: raise ValueError('MISSING_REGISTRY_TABLE:'+t)
        missing=NEEDED[t]-columns(db,t)
        if missing: raise ValueError('MISSING_REGISTRY_COLUMNS:%s:%s'%(t,','.join(sorted(missing))))
    if workers < 1 or workers > 16: raise ValueError('WORKER_RANGE_1_TO_16')
    return shard_tasks(db,workers)


def create_run(base):
    import tempfile
    base.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(dir=base,prefix='saddlebench_lean_'))
    for name,_,_ in TABLES: (root/name).mkdir()
    (root/'modes').mkdir()
    return root


def run(registry, output_base, workers, every, preflight_only=False):
    if not registry.is_absolute() or not registry.is_file(): raise ValueError('ABSOLUTE_REGISTRY_FILE_REQUIRED')
    if registry.is_symlink(): raise ValueError('REGISTRY_MUST_BE_PINNED_REGULAR_FILE')
    db=db_open(registry)
    try:
        tasks=preflight(db, workers)
        counts={table:int(db.execute('SELECT COUNT(*) FROM '+sql_ident(table)).fetchone()[0]) for _,table,_ in TABLES}
    finally: db.close()
    info('PREFLIGHT=PASS')
    info('REGISTRY='+str(registry))
    info('RECORD_COUNTS='+json.dumps(counts,sort_keys=True))
    info('WORKERS='+str(workers))
    if preflight_only: return None
    out=create_run(output_base)
    info('RESULT_DIR='+str(out))
    info('REFERENCE_MINIMUM_MASK_HINTS=LOADING')
    mask_hints=load_reference_mask_hints(registry)
    info('REFERENCE_MINIMUM_MASK_HINTS=%d (unverified saddle inference)' % len(mask_hints))
    t0=time.monotonic(); result=[]
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures={pool.submit(process_shard,str(registry),task,str(out),every,mask_hints):task for task in tasks}
            for f in as_completed(futures):
                result.append(f.result())
        result.sort(key=lambda r:(list(t[1] for t in TABLES).index(r['table']),r['part']))
        bytab=Counter()
        for record in result: bytab[record['table']]+=record['rows']
        if any(bytab[t] != n for t,n in counts.items()): raise ValueError('OUTPUT_ROW_COUNTS_INCOMPLETE')
        total_statuses=Counter()
        for part in result: total_statuses.update(part.get('statuses', {}))
        manifest={'schema_version':SCHEMA,'qualification':'UNQUALIFIED_REGISTRY_EXPORT',
                  'source_registry':str(registry), 'provenance_artifact_hashing_performed':False,
                  'saddle_scientific_eligibility_recomputed':False,
                  'notes':['Do not infer constraints for saddles without native constraint evidence.',
                           'Triplet sides retain recorded DoubleMin minus/plus ordering.',
                           'Triplet saddle geometry joins through canonical_saddle_id in saddles parts.',
                           'Recorded modes are observation-source data, not re-certified Hessian results.',
                           'Source files and registry contents are not independently verified.'],
                  'source_counts':counts,'reference_mask_hint_structures':len(mask_hints),
                  'status_counts':dict(sorted(total_statuses.items())),
                  'parts':result,'elapsed_seconds':time.monotonic()-t0}
        with (out/'manifest.json').open('w') as f: json.dump(manifest,f,sort_keys=True,indent=2);f.write('\n')
        import tarfile
        # Small diagnostic-only upload; never bundle the enormous science data by default.
        report = out / 'lean_export_diagnostic_report.tar.gz'
        with tarfile.open(report, 'w:gz') as tar:
            tar.add(out/'manifest.json', arcname='manifest.json')
        (out/'EXPORT_COMPLETE').write_text('PASS\n')
        info('EXPORT_STATUS=PASS')
        info('OUTPUT_MANIFEST='+str(out/'manifest.json'))
        info('UPLOAD_THIS_FILE='+str(report))
        return out
    except BaseException as exc:
        (out/'EXPORT_FAILED').write_text(type(exc).__name__+': '+str(exc)+'\n')
        info('EXPORT_STATUS=FAIL reason='+str(exc))
        raise


def cli():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--registry',type=Path)
    g.add_argument('--pointer',type=Path)
    p.add_argument('--out-base',type=Path, default=Path('/scratch/11588/ndn454/sm_anskey/saddlebench_diagnostics'))
    p.add_argument('--workers',type=int,default=3)
    p.add_argument('--progress-every',type=int,default=1000)
    p.add_argument('--preflight-only',action='store_true')
    args=p.parse_args()
    if args.progress_every<1: p.error('progress-every must be positive')
    if args.pointer is not None:
        if not args.pointer.is_file():p.error('pointer missing')
        text=args.pointer.read_text().strip()
        if not text.startswith('/'):p.error('pointer must contain absolute registry path')
        registry=Path(text)
    else:registry=args.registry
    try:
        run(registry,args.out_base,args.workers,args.progress_every,args.preflight_only)
    except Exception:
        traceback.print_exc()
        return 1
    return 0

if __name__=='__main__':sys.exit(cli())
