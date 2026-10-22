"""Actual SQLite-export equivalence for serial vs process-parallel RC14."""
import io
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
import zlib

import numpy as np

SRC = Path(__file__).resolve().parents[1]/'payload'/'export_published_registry.py'
sys.path.insert(0, str(SRC.parent))
import export_published_registry as e
sys.path.insert(0, str(SRC.parents[1]))
import audit_constraints as audit


def geo_blob():
    out=io.BytesIO()
    num=np.array([1,8],dtype=np.int16)
    xyz=np.array([[0.,0.,0.],[0.7,0.,0.]],dtype=np.float64)
    cell=np.eye(3)
    pbc=np.array([False,False,False],dtype=np.uint8)
    np.savez(out,numbers=num,positions=xyz,cell=cell,pbc=pbc)
    return zlib.compress(out.getvalue()),e.pipeline_geometry_sha(num,xyz,cell,pbc)


def seed_db(db):
    artifact = db.parent.parent/'recorded_source.npz'
    np.savez(artifact, scientific_auxiliary=np.array([1.,2.]))
    con=sqlite3.connect(db)
    try:
        con.executescript('''
        CREATE TABLE registry_meta(key TEXT,value TEXT);
        CREATE TABLE canonical_event_application(event_id TEXT,event_sequence INTEGER);
        CREATE TABLE canonical_event_publications(publication_id TEXT,published_through INTEGER);
        CREATE TABLE saddle_registry(canonical_saddle_id TEXT,structure_id TEXT,geometry_blob BLOB,geometry_sha256 TEXT,energy_eV REAL,hessian_status TEXT,reaction_eligible INTEGER,source_result_path TEXT);
        CREATE TABLE saddle_group_members(canonical_saddle_id TEXT,saddle_group_id TEXT);
        CREATE TABLE reaction_observations(observation_id TEXT,canonical_saddle_id TEXT,structure_id TEXT,saddle_group_id TEXT,canonical_reaction_id TEXT,protocol TEXT,protocol_eligible INTEGER,outcome TEXT,minus_path TEXT,minus_frame INTEGER,plus_path TEXT,plus_frame INTEGER,read_error TEXT,source_kind TEXT);
        CREATE TABLE reaction_registry(canonical_reaction_id TEXT,structure_id TEXT,saddle_group_id TEXT,status TEXT,connected_to_canonical_minimum INTEGER,rate_relevant INTEGER,barrier_eV REAL);
        CREATE TABLE canonical_minima(minimum_id TEXT,parent_structure_id TEXT,geometry_blob BLOB,geometry_sha256 TEXT,energy_eV REAL,certification_status TEXT,object_path TEXT);
        ''')
        con.execute("INSERT INTO registry_meta VALUES('canonical_events_published_through','2')")
        con.execute("INSERT INTO registry_meta VALUES('canonical_minima_snapshot_id','snap')")
        con.execute("INSERT INTO canonical_event_application VALUES('ev1',1)")
        con.execute("INSERT INTO canonical_event_publications VALUES('pub1',2)")
        blob,sha=geo_blob()
        for i in range(17):
            sid=f's{i:03d}'
            con.execute('INSERT INTO saddle_registry VALUES(?,?,?,?,?,?,?,?)',(sid,'structure',blob,sha,0.1,'FIRST_ORDER',1,str(artifact)))
            con.execute('INSERT INTO saddle_group_members VALUES(?,?)',(sid,'group'+str(i)))
        for i in range(23):
            oid=f'o{i:03d}'
            con.execute('INSERT INTO reaction_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                oid,f's{i%17:03d}','structure','group'+str(i%17),f'r{i%9:03d}','Powell',1,'SAME_REACTION',str(artifact),0,str(artifact),0,None,'test'))
        for i in range(9):
            con.execute('INSERT INTO reaction_registry VALUES(?,?,?,?,?,?,?)',(f'r{i:03d}','structure','g'+str(i),'CONNECTED',1,1,0.8))
        for i in range(5):
            con.execute('INSERT INTO canonical_minima VALUES(?,?,?,?,?,?,?)',(f'm{i:03d}','structure',blob,sha,0.0,'LEGACY_CANONICAL_STATE',str(artifact)))
        con.commit()
    finally:
        con.close()


class ParallelEquivalent(unittest.TestCase):
    def test_reproducible_jsonl_bytes_and_count(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            dbroot=root/'registry';dbroot.mkdir()
            db=dbroot/'source.sqlite';seed_db(db)
            source_digest=e.digest_file(db)
            one=e.export(None,db,root/'serial',workers=1)
            many=e.export(None,db,root/'parallel',workers=4)
            self.assertEqual(source_digest,e.digest_file(db))
            self.assertEqual(one['registry']['sha256'],many['registry']['sha256'])
            self.assertEqual(one['record_counts'],many['record_counts'])
            self.assertEqual(one['coverage'],many['coverage'])
            for p in list(e.FILES.values()) + list(e.EXTRA_FILES):
                a=(root/'serial'/p).read_bytes();b=(root/'parallel'/p).read_bytes()
                self.assertEqual(a,b,p)
                self.assertEqual(one['file_sha256'][p],many['file_sha256'][p])
            self.assertTrue((root/'parallel'/'manifest.json').is_file())
            self.assertEqual(one['record_counts']['saddles.jsonl'],17)
            self.assertEqual(one['record_counts']['doublemin_observations.jsonl'],23)
            with self.assertRaises(FileExistsError):e.export(None,db,root/'parallel',workers=3)

    def test_parallel_pointer_and_unknown_constraints_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dbroot=root/'registry';dbroot.mkdir()
            db=dbroot/'source.sqlite';seed_db(db)
            pointer=root/'pointer.txt';pointer.write_text(str(db)+'\n')
            exported=e.export(pointer,None,root/'export',workers=3)
            self.assertEqual(exported['registry']['published_through'],2)
            self.assertEqual(exported['registry']['pointer_path'],str(pointer))
            saddles=list(map(json.loads,(root/'export'/'geometries.jsonl').read_text().splitlines()))
            self.assertTrue(saddles)
            self.assertTrue(all(row['constraints_known'] is None for row in saddles))
            report=audit.run(root/'export')
            self.assertEqual(report['counts']['saddles']['unknown'],17)
            self.assertEqual(report['counts']['minima']['unknown'],5)

    def test_invalid_worker_counts_do_not_make_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dbroot=root/'registry';dbroot.mkdir()
            db=dbroot/'source.sqlite';seed_db(db)
            for n in [0,-1,17]:
                out=root/f'invalid_{n}'
                with self.assertRaises(ValueError):e.export(None,db,out,workers=n)
                self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
