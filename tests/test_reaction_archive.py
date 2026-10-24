import contextlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import zlib
import numpy as np
from tools.reaction_archive import reaction_archive as m
from tools.reaction_archive import legacy_lean as old


def blob():
    f=io.BytesIO()
    np.savez(f,numbers=np.array([1,1]),positions=np.array([[0.,0,0],[0,0,0.74]]),cell=np.eye(3),pbc=np.array([0,0,0]))
    return zlib.compress(f.getvalue())


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp())
        self.registry=self.root/'registry.sqlite'
        self.dbpath=self.root/'archive.sqlite'
        db=sqlite3.connect(self.registry)
        db.executescript('''
          CREATE TABLE saddle_registry(canonical_saddle_id TEXT PRIMARY KEY,structure_id TEXT,geometry_blob BLOB,energy_eV REAL,hessian_status TEXT,reaction_eligible INTEGER);
          CREATE TABLE canonical_minima(minimum_id TEXT PRIMARY KEY,parent_structure_id TEXT,geometry_blob BLOB,energy_eV REAL,object_path TEXT,certification_status TEXT);
          CREATE TABLE reaction_registry(canonical_reaction_id TEXT PRIMARY KEY,structure_id TEXT,barrier_eV REAL,saddle_group_id TEXT,status TEXT,connected_to_canonical_minimum INTEGER,rate_relevant INTEGER);
          CREATE TABLE reaction_observations(observation_id TEXT PRIMARY KEY,canonical_saddle_id TEXT,canonical_reaction_id TEXT,structure_id TEXT,saddle_group_id TEXT,minus_path TEXT,minus_frame INTEGER,plus_path TEXT,plus_frame INTEGER,protocol TEXT,outcome TEXT,source_kind TEXT,protocol_eligible INTEGER);
          CREATE TABLE reaction_observation_provenance(observation_id TEXT,hessian_source_path TEXT,hessian_source_frame INTEGER);
        ''')
        db.execute('INSERT INTO saddle_registry VALUES(?,?,?,?,?,?)',('sid','st',blob(),5.0,'ok',1))
        db.execute('INSERT INTO canonical_minima VALUES(?,?,?,?,?,?)',('min','st',blob(),1.,None,'ok'))
        db.execute('INSERT INTO reaction_registry VALUES(?,?,?,?,?,?,?)',('rid','st',4.,'sg','ok',1,1))
        for i in range(5):
            db.execute('INSERT INTO reaction_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (f'o{i}','sid','rid','st','sg',str(self.root/'a.traj'),i*2,
                        str(self.root/'a.traj'),i*2+1,'canonical','ok','from_saddle',1))
            db.execute('INSERT INTO reaction_observation_provenance VALUES(?,?,?)',
                       (f'o{i}',str(self.root/'b.traj'),i))
        db.commit();db.close()
        (self.root/'a.traj').write_bytes(b'fake'); (self.root/'b.traj').write_bytes(b'fake')
        self.db=m.connection(self.dbpath)
        m.init(self.db,self.registry)

    def tearDown(self):
        self.db.close(); shutil.rmtree(self.root)

    def fake_group(self,args):
        path,specs,strict=args
        rows=[]
        for kind,frame in specs:
            if kind=='endpoint':
                rec={'status':'OK','geometry':{'ordered_numbers':[1,1], 'positions_angstrom':[[0,0,0],[0,0,0.74]],
                    'cell_angstrom':np.eye(3).tolist(),'pbc':[False,False,False]},
                    'energy_eV':-float(frame),'energy_source':'serialized_calculator_energy',
                    'constraints_known':True,'fixed_indices':[0], 'constraint_status':'NATIVE_ENDPOINT_TRAJECTORY',
                    'constraint_descriptors':[{'type':'FixAtoms','indices':[0]}]}
            else:
                rec={'status':'OK','mode_geometry_parity':'UNVERIFIED',
                    'source_path':path,'source_frame':frame,'vector':[[1.,0,0],[0,0,0]],
                    'shape':[2,3],'lowest_eigenvalue_eV_A2':-0.5,
                    'negative_mode_count':1,'negative_eigenvalue_tolerance_eV_A2':-0.1,
                    'order_projection':'xyz'}
            rows.append((kind,frame,rec))
        return path,rows,'selective'

    def test_complete_and_repeated_export_without_registry(self):
        with mock.patch.object(m,'process_path_group',side_effect=self.fake_group) as read:
            m.ensure_registry_metadata(self.db,self.registry)
            m.populate_frame_queue(self.db,self.registry)
            m.extract_frames(self.db,workers=1)
            m.complete_observations(self.db,self.registry)
            self.assertEqual(read.call_count,2) # grouped 15 frames across TWO rank files
        rep=m.validate(self.db,mark=True)
        self.assertTrue(rep['complete'])
        self.assertEqual(rep['actual']['triplets'],5)
        self.assertEqual(rep['actual']['modes'],5)
        row=m.unpacked(self.db.execute('SELECT data FROM observations WHERE id=?',('o2',)).fetchone()[0])
        self.assertEqual(row['minus']['fixed_indices'],[0])
        self.assertEqual(row['minus']['energy_eV'],-4.)
        self.assertEqual(row['canonical_reaction_id'],'rid')
        self.assertEqual(row['canonical_saddle_id'],'sid')
        self.assertEqual(row['mode_status'],'OK')
        shutil.move(self.registry,self.root/'missing_registry.sqlite')
        m.export_jsonl(self.db,self.root/'export_1')
        m.export_jsonl(self.db,self.root/'export_2')
        self.assertEqual((self.root/'export_1'/'triplets'/'part-001.jsonl').read_bytes(),
                         (self.root/'export_2'/'triplets'/'part-001.jsonl').read_bytes())
        self.assertTrue((self.root/'export_1'/'EXPORT_COMPLETE').exists())

    def test_resume_after_interruption_skips_all_completed_frames(self):
        m.populate_frame_queue(self.db,self.registry)
        path,refs=next(iter(m.path_groups(self.db)))
        p,rows,method=self.fake_group((path,refs,False))
        m.persist_frame_results(self.db,p,rows)
        with mock.patch.object(m,'process_path_group',side_effect=self.fake_group) as read:
            m.extract_frames(self.db,workers=1)
            self.assertEqual(read.call_count,1) # other source only
            m.complete_observations(self.db,self.registry)
        with mock.patch.object(m,'process_path_group',side_effect=AssertionError('NO_REEXTRACT')):
            m.populate_frame_queue(self.db,self.registry)
            m.extract_frames(self.db,workers=1)
            m.complete_observations(self.db,self.registry)
        self.assertEqual(m.totals(self.db)['triplets'],5)

    def test_partial_jsonl_incremental_import(self):
        src=self.root/'lean'; (src/'triplets').mkdir(parents=True)
        for nm in ('modes','saddles','reactions','minima'):(src/nm).mkdir()
        p=src/'triplets'/'part-000.jsonl'
        orig={'observation_id':'o0','canonical_saddle_id':'sid','canonical_reaction_id':'rid',
              'structure_id':'st','saddle_group_id':'sg','minus':{'status':'OK','energy_eV':-9.},
              'plus':{'status':'OK','energy_eV':-8.},'mode_status':'NO_RECORDED_MODE_PATH','mode_id':None}
        with p.open('wb') as f:
            f.write(old.jsonline(orig).encode())
            f.write(b'{"observation_id":"o1"')
        m.import_lean(self.db,self.registry,src)
        self.assertEqual(m.totals(self.db)['triplets'],1)
        # Resuming append should not reimport the existing record.
        with p.open('ab') as f:
            f.write(b',"canonical_saddle_id":"sid","canonical_reaction_id":"rid","structure_id":"st","saddle_group_id":"sg"}\n')
        m.import_lean(self.db,self.registry,src)
        self.assertEqual(m.totals(self.db)['triplets'],2)
        m.import_lean(self.db,self.registry,src)
        self.assertEqual(m.totals(self.db)['triplets'],2)

    def test_import_lustre_unsigned_inode_device_and_resume(self):
        """Lustre stat fields may exceed SQLite signed INTEGER range."""
        src=self.root/'lean'; (src/'saddles').mkdir(parents=True)
        p=src/'saddles'/'part-000.jsonl'
        first={'canonical_saddle_id':'sid','structure_id':'st','energy_eV':5.}
        p.write_text(old.jsonline(first))
        huge_inode=(1<<64)-19
        huge_device=(1<<63)+711
        real_stat=Path.stat
        def lustre_stat(path,*args,**kwargs):
            st=real_stat(path,*args,**kwargs)
            if path == p:
                from types import SimpleNamespace
                return SimpleNamespace(st_dev=huge_device,st_ino=huge_inode,st_size=st.st_size)
            return st
        with mock.patch.object(Path,'stat',new=lustre_stat):
            m.import_lean(self.db,self.registry,src)
            row=self.db.execute('SELECT device,inode,typeof(device),typeof(inode) FROM import_offsets').fetchone()
            self.assertEqual((row[0],row[1]),('fid:'+str(huge_device),'fid:'+str(huge_inode)))
            self.assertEqual((row[2],row[3]),('text','text'))
            # Same identity is accepted on restart without new or duplicated records.
            m.import_lean(self.db,self.registry,src)
            with self.assertRaisesRegex(RuntimeError,'CHANGED_IMPORT_FILE_REFUSE'):
                def different_inode(path,*args,**kwargs):
                    out=lustre_stat(path,*args,**kwargs)
                    if path == p:out.st_ino += 1
                    return out
                with mock.patch.object(Path,'stat',new=different_inode):
                    m.import_lean(self.db,self.registry,src)
        self.assertEqual(m.totals(self.db)['saddles'],1)

    def test_file_id_backwards_compatible_with_signed_checkpoint(self):
        self.assertTrue(m.file_id_matches(173,173))
        self.assertTrue(m.file_id_matches('173',173))
        self.assertTrue(m.file_id_matches(m.file_id_to_storage(173),173))
        self.assertFalse(m.file_id_matches('fid:173',174))
        self.assertEqual(m.file_id_to_storage(2**80),'fid:'+str(2**80))
        with sqlite3.connect(':memory:') as tmp:
            tmp.execute('CREATE TABLE checkpoint (device INTEGER, inode INTEGER)')
            tmp.execute('INSERT INTO checkpoint VALUES (?,?)',
                        (m.file_id_to_storage(2**80),m.file_id_to_storage(2**81)))
            types=tmp.execute('SELECT typeof(device),typeof(inode) FROM checkpoint').fetchone()
            self.assertEqual(types,('text','text'))

    def test_import_rejects_mixed_registry(self):
        src=self.root/'lean'; (src/'saddles').mkdir(parents=True)
        (src/'saddles'/'part-000.jsonl').write_text(old.jsonline({'canonical_saddle_id':'imposter','structure_id':'st'}))
        with self.assertRaisesRegex(ValueError,'NOT_IN_PINNED'):
            m.import_lean(self.db,self.registry,src)

    def test_import_rejects_file_rewrite(self):
        src=self.root/'lean'; (src/'saddles').mkdir(parents=True)
        p=src/'saddles'/'part-000.jsonl'
        p.write_text(old.jsonline({'canonical_saddle_id':'sid','structure_id':'st','energy_eV':5.}))
        m.import_lean(self.db,self.registry,src)
        p.write_text(old.jsonline({'canonical_saddle_id':'sid','structure_id':'other','energy_eV':5.}))
        with self.assertRaisesRegex(RuntimeError,'CHANGED_IMPORT_FILE'):
            m.import_lean(self.db,self.registry,src)

    def test_source_change_blocks_resume(self):
        self.db.close()
        with sqlite3.connect(self.registry) as r:r.execute('UPDATE saddle_registry SET energy_eV=99')
        self.db=m.connection(self.dbpath)
        with self.assertRaisesRegex(RuntimeError,'REGISTRY_IDENTITY_MISMATCH'):
            m.init(self.db,self.registry)

    def test_integrity_rejects_missing_records(self):
        self.assertFalse(m.validate(self.db)['complete'])
        with self.assertRaisesRegex(RuntimeError,'ARCHIVE_INCOMPLETE'):
            m.export_jsonl(self.db,self.root/'bad_export')
        self.assertFalse((self.root/'bad_export').exists())

    def test_backup_atomic_and_no_overwrite(self):
        dest=self.root/'copies'/'safe.sqlite'
        m.backup_archive(self.db,dest)
        with contextlib.closing(sqlite3.connect(dest)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        with self.assertRaises(FileExistsError):m.backup_archive(self.db,dest)

if __name__=='__main__':unittest.main()

class FrameBackendTests(unittest.TestCase):
    def test_direct_mode_info_without_atoms_reconstruction(self):
        from tools.reaction_archive import fast_frames
        path='/scratch/11588/ndn454/sm_anskey/one.traj'
        class Backend:
            def get(self,key,default=None):
                if key=='info':return {'eigenmode':[[1.,0,0],[0.,0,0]], 'lowest_eigenvalue':-2.,
                                       'saddlemill_dimer_runtime_state':{'garbage':'omitted'}}
                return default
        class Traj:
            backend=[Backend()]
        got=fast_frames._read_from_reader(Traj(),path,'mode',0)
        self.assertEqual(got['status'],'OK')
        self.assertEqual(got['lowest_eigenvalue_eV_A2'],-2.)
        self.assertNotIn('saddlemill_dimer_runtime_state',json.dumps(got))

    def test_selective_backend_parity_fallback(self):
        import types
        from tools.reaction_archive import fast_frames
        path='/scratch/11588/ndn454/sm_anskey/one.traj'
        class B:
            def get(self,key,default=None):
                return {'eigenmode':[[1.,0,0],[0.,0,0]]} if key=='info' else default
        class T:
            backend=[B(),B()]
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __getitem__(self,i):return types.SimpleNamespace(info={'eigenmode':[[1.,0,0],[0.,0,0]]})
        ase=types.ModuleType('ase');io_mod=types.ModuleType('ase.io');traj=types.ModuleType('ase.io.trajectory')
        traj.Trajectory=lambda path,mode='r':T()
        with mock.patch.dict(sys.modules,{'ase':ase,'ase.io':io_mod,'ase.io.trajectory':traj}), \
             mock.patch.object(old,'permitted_source',return_value=True), \
             mock.patch.object(Path,'is_file',return_value=True), \
             mock.patch.object(fast_frames,'_full',return_value={'status':'MODE_ABSENT'}):
            out,rows,method=fast_frames.process_path_group((path,[('mode',0),('mode',1)],False))
        self.assertEqual(method,'ase_parity_fallback')
        self.assertTrue(all(row[2]['status']=='OK' for row in rows))
