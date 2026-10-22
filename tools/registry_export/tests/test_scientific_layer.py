"""Read-only scientific enrichment regressions with true RC14 SQLite fixtures."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'payload'))
from payload import export_published_registry as exporter
from enrich_scientific import build, read_mode, hash_file
from validate_export import validate
from upload_hf_candidate import inspect
from tests.test_parallel_equivalence import seed_db


def json_rows(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


class ScienceLayer(unittest.TestCase):
    def fixture(self, root, include_mode=True):
        regdir=root/'registry';regdir.mkdir()
        reg=regdir/'source.sqlite';seed_db(reg)
        if include_mode:
            src=root/'verified_hessian.npz'
            mode=np.array([[.1,.2,.3],[.4,.5,.6]],dtype=np.float64)
            np.savez_compressed(src, lowest_eigenmode=mode,
                negative_mode_count=np.array(1),
                negative_eigenvalue_tolerance=np.array(1.e-6),
                eigenvalues=np.array([-.05, .2, .4]),
                order_projection=np.array('periodic_rigid_translations'))
            con=sqlite3.connect(reg)
            con.executescript('CREATE TABLE reaction_observation_provenance (observation_id TEXT,hessian_source_path TEXT,hessian_source_frame INTEGER);')
            con.execute('INSERT INTO reaction_observation_provenance VALUES (?,?,?)',('o000',str(src),0))
            con.commit();con.close()
        out=root/'raw'
        exporter.export(None,reg,out,workers=1)
        return out

    def test_all_observations_kept_and_geometry_join(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); evidence=self.fixture(root)
            m=build(evidence,root/'science',shard_size=1,extra_roots=[root])
            self.assertEqual(m['row_counts']['reaction_triplets'],23)
            self.assertEqual(m['row_counts']['saddles_enriched'],17)
            validate(evidence,root/'science')
            prepped,layer=inspect(evidence,root/'science')
            self.assertEqual(prepped['registry']['sha256'],layer['source_registry_sha256'])
            saddles=json_rows(root/'science'/'saddles_enriched.jsonl')
            self.assertEqual(saddles[0]['canonical_saddle_id'],'s000')
            self.assertEqual(saddles[0]['reaction_triplet_observation_ids'],['o000','o017'])
            rows=json_rows(root/'science'/'reaction_triplets.jsonl')
            self.assertEqual([r['observation_id'] for r in rows],sorted(r['observation_id'] for r in rows))
            self.assertEqual(len({r['observation_id'] for r in rows}),23)
            r=rows[0]
            self.assertEqual(r['canonical_saddle_id'],'s000')
            self.assertTrue(r['geometry']['saddle']['n_atoms']==2)
            self.assertEqual(r['status'],'INCOMPLETE_UNQUALIFIED')
            self.assertIn('MINIMUM_A_GEOMETRY_UNAVAILABLE',r['missing_or_inconsistent'])
            self.assertFalse(r['independently_verified_connected'])

    def test_sources_keep_mode_arrays_exact_and_avoids_false_certification(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); evidence=self.fixture(root)
            m=build(evidence,root/'science',shard_size=1,extra_roots=[root])
            self.assertEqual(m['mode_status_counts']['RECORDED_MODE_UNQUALIFIED'],1)
            rows=json_rows(root/'science'/'hessian_modes.jsonl')
            hit=next(r for r in rows if r['observation_id']=='o000' and r['mode_array'])
            self.assertEqual(hit['mode']['negative_mode_count'],1)
            self.assertIsNone(hit['mode']['exact_geometry_sha_match'])
            self.assertTrue(hit['mode']['full_atom_shape'])
            self.assertEqual(hit['mode_scientific_certification'],'NOT_ESTABLISHED')
            a=hit['mode_array']
            with np.load(root/'science'/a['file'],allow_pickle=False) as npz:
                actual=npz[a['key']]
            expected=np.array([[.1,.2,.3],[.4,.5,.6]],dtype=np.float64)
            np.testing.assert_array_equal(actual,expected)
            self.assertEqual(a['sha256_float_array'],hashlib.sha256(expected.tobytes()).hexdigest())

    def test_parallel_base_same_triplets_and_modes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); evidence=self.fixture(root)
            # Re-export with 3 workers; output scientific layer must be byte-identical.
            db=root/'registry'/'source.sqlite'
            exporter.export(None,db,root/'parallel',workers=3)
            build(evidence,root/'science_a',shard_size=2,extra_roots=[root])
            build(root/'parallel',root/'science_b',shard_size=2,extra_roots=[root])
            for fn in ('saddles_enriched.jsonl','reaction_triplets.jsonl','hessian_modes.jsonl','manifest_science.json'):
                self.assertEqual((root/'science_a'/fn).read_bytes(),(root/'science_b'/fn).read_bytes(),fn)

    def test_base_manifest_sha_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); evidence=self.fixture(root,False)
            with (evidence/'saddles.jsonl').open('a') as f:f.write('{}\n')
            with self.assertRaisesRegex(ValueError,'BASE_EVIDENCE_HASH_MISMATCH'):
                build(evidence,root/'science',extra_roots=[root])
            self.assertFalse((root/'science').exists())

    def test_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); evidence=self.fixture(root,False)
            dest=root/'science';dest.mkdir()
            with self.assertRaises(FileExistsError):build(evidence,dest)
            self.assertEqual(list(dest.iterdir()),[])

    def test_mode_rejects_artifact_hash_mismatch_and_invalid_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            src=root/'bad.npz'
            np.savez_compressed(src,lowest_eigenmode=np.ones((2,2)))
            a=dict(path=str(src),source_status='HASHED_READABLE',sha256=hash_file(src))
            v,meta=read_mode(a,2,None,extra_roots=[root]);self.assertIsNone(v)
            self.assertEqual(meta['status'],'INVALID_MODE_ARRAY')
            a['sha256']='0'*64
            v,meta=read_mode(a,2,None,extra_roots=[root]);self.assertIsNone(v)
            self.assertEqual(meta['status'],'SOURCE_SHA256_MISMATCH')

    def test_mode_different_atom_count_retains_but_flags(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'short.npz'
            np.savez_compressed(src,lowest_eigenmode=np.ones((1,3)),negative_mode_count=1)
            a=dict(path=str(src),source_status='HASHED_READABLE',sha256=hash_file(src))
            v,meta=read_mode(a,2,None,extra_roots=[root])
            self.assertIsNotNone(v)
            self.assertFalse(meta['full_atom_shape'])
            self.assertEqual(meta['status'],'RECORDED_MODE_UNQUALIFIED')


if __name__=='__main__':unittest.main()
