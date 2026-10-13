"""Scoring metadata: full registry saddle-group population, not reaction count."""
import gzip
import hashlib
import json
from pathlib import Path

from saddlebench.prototype import reaction_mvp as rm
from saddlebench.prototype import release_mvp
from test_reaction_prototype_modes import dataset


def _rewrite_saddles(root, records):
    payload=''.join(json.dumps(x,sort_keys=True,separators=(',',':'))+'\n' for x in records).encode()
    (root/'saddles.jsonl').write_bytes(payload)
    manifest=json.loads((root/'manifest.json').read_text())
    manifest['file_sha256']['saddles.jsonl']=hashlib.sha256(payload).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest))


def test_count_all_registry_members_including_non_eligible(tmp_path):
    evidence=dataset(tmp_path)
    rows=list(rm.rows(evidence,'saddles.jsonl'))
    assert len(rows)==4
    extra=dict(rows[0]); extra['canonical_saddle_id']='different-saddle-same-group'
    extra['hessian_status']='NON_FIRST_ORDER'  # Not a scoring reference member.
    extra['geometry_id']=None
    _rewrite_saddles(evidence,rows+[extra])
    rm.build(evidence,tmp_path/'reference',mode='all_saddles',pes_id='pes:test')
    member=next(rm.rows(tmp_path/'reference','reference.jsonl'))['members'][0]
    assert member['registered_saddle_group_size']==2


def test_matched_reaction_output_has_full_group_size(tmp_path):
    evidence=dataset(tmp_path)
    rows=list(rm.rows(evidence,'saddles.jsonl'))
    extra=dict(rows[0]); extra['canonical_saddle_id']='different-saddle-same-group'
    extra['hessian_status']='NON_FIRST_ORDER'; extra['geometry_id']=None
    _rewrite_saddles(evidence,rows+[extra])
    rm.build(evidence,tmp_path/'reference',mode='all_saddles',pes_id='pes:test')
    member=next(rm.rows(tmp_path/'reference','reference.jsonl'))['members'][0]
    g=member['geometry']
    from unittest.mock import patch
    with patch.object(rm,'read_submissions',return_value=[{'submission_id':'a',
        'geometry':g,'starting_minimum_id':'m0','parent_structure_id':'synthetic:p1','pes_id':'pes:test'}]):
        scored=rm.score(tmp_path/'reference','unused.traj',3600,
                        start='m0',parent='synthetic:p1',pes='pes:test')
    assert scored['submissions'][0]['status']=='CREDIT'
    assert scored['submissions'][0]['matched_saddle_groups']==[{
        'saddle_group_id':member['saddle_group_id'],'registered_saddle_count':2}]


def test_diagnostic_match_group_counts_are_distinct(monkeypatch):
    monkeypatch.setattr(release_mvp,'core_drift_maxdev',lambda *a:0)
    lookup={(1,):[
        {'saddle_id':'a','saddle_group_id':'g1','registered_saddle_group_size':8,
         'diagnostic_kind':'KNOWN_NON_FIRST_ORDER','start_known':True,'geometry':{'numbers':[1]},
         'hessian_status':'NON_FIRST_ORDER','barrier_eV':[]},
        {'saddle_id':'b','saddle_group_id':'g2','registered_saddle_group_size':3,
         'diagnostic_kind':'KNOWN_NON_FIRST_ORDER','start_known':True,'geometry':{'numbers':[1]},
         'hessian_status':'NON_FIRST_ORDER','barrier_eV':[]} ]}
    r=release_mvp.diagnose({'status':'NO_MATCH'},{'geometry':{'numbers':[1]}},lookup)
    assert r['diagnostic']=='AMBIGUOUS_CATALOG_MATCH'
    assert r['matched_saddle_groups']==[
        {'saddle_group_id':'g1','registered_saddle_count':8},
        {'saddle_group_id':'g2','registered_saddle_count':3}]
