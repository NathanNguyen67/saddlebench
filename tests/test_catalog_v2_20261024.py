"""Focused catalog-scoring regressions: no silent scientific qualification."""
import csv,hashlib,json
from pathlib import Path
import pytest
from saddlebench.reference.catalog_v2 import load_catalog,digest,SCHEMA

def make_catalog(root):
    root.mkdir();(root/'case_shards').mkdir()
    case='structure:one'
    with (root/'cases.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case_id','structure_id','pes_id','starting_minimum_id']);w.writeheader()
        w.writerow({'case_id':case,'structure_id':case,'pes_id':'PES_TEST','starting_minimum_id':'min:one'})
    r=[]
    for i,energy,verified,reported in ((0,0.5,True,True),(1,1.3,True,True),
                                        (2,0.6,False,True),(3,None,False,True),(4,0.2,False,False)):
        r.append({'reaction_id':'rx'+str(i),'structure_id':case,'pes_id':'PES_TEST','science_verified':verified,
                  'registry_recorded_connected':reported,'barrier_eV':energy,'members':[{'geometry':{'numbers':[1]}}]})
    raw=''.join(json.dumps(x)+'\n' for x in r)
    (root/'reference.jsonl').write_text(raw)
    rel='case_shards/'+hashlib.sha256(case.encode()).hexdigest()[:24]+'.jsonl'
    (root/rel).write_text(raw)
    manifest={'schema_version':SCHEMA,'evidence_standard':'lean_registry_reported_not_force_verified',
              'reference_count':len(r),'reference_sha256':digest(root/'reference.jsonl'),
              'cases_sha256':digest(root/'cases.csv'),'case_count':1,
              'case_shards':{case:{'path':rel,'sha256':digest(root/rel),'count':len(r)}}}
    (root/'catalog_manifest.json').write_text(json.dumps(manifest))
    return root,root/'cases.csv',case

def test_default_strict_requires_scientific_evidence(tmp_path):
    root,cases,case=make_catalog(tmp_path/'cat')
    rows,p=load_catalog(root,cases,case_id=case)
    assert [r['reaction_id'] for r in rows]==['rx0']
    assert p['barrier_max_eV_exclusive']==1.0 and p['evidence_policy']=='science_verified_only'

def test_exploratory_and_unlimited_policy_do_not_mutate_identity(tmp_path):
    root,cases,case=make_catalog(tmp_path/'cat')
    rows,p=load_catalog(root,cases,case_id=case,allow_registry_reported=True)
    assert [r['reaction_id'] for r in rows]==['rx0','rx2']
    rows,p=load_catalog(root,cases,case_id=case,barrier_max_eV=None,allow_registry_reported=True)
    assert [r['reaction_id'] for r in rows]==['rx0','rx1','rx2','rx3']
    assert 'EXPLORATORY' in p['evidence_policy']

def test_bad_cutoff_and_modified_data_fail_closed(tmp_path):
    root,cases,case=make_catalog(tmp_path/'cat')
    for n in (-1,0,float('nan')):
        with pytest.raises(ValueError,match='INVALID_BARRIER_POLICY'):
            load_catalog(root,cases,barrier_max_eV=n,case_id=case)
    shard=next((root/'case_shards').iterdir())
    shard.write_text(shard.read_text()+'{}\n')
    with pytest.raises(ValueError,match='CHECKSUM_MISMATCH'):
        load_catalog(root,cases,case_id=case)

def test_identity_not_based_on_start(tmp_path):
    from saddlebench.scoring.scope import matches
    r={'structure_id':'s','pes_id':'p','starting_minimum_id':None}
    c={'structure_id':'s','pes_id':'p','starting_minimum_id':'MIN1'}
    assert matches(r,c)
    assert not matches(r,c,require_starting_minimum=True)
