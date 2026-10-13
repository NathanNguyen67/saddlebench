"""New RC20 progress, source-scope, index-reuse, and exact rejection tests."""
import gzip
import json
import time
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
from saddlebench.matching.geometry import core_drift_maxdev, cannot_match_by_anchor
from saddlebench.prototype import release_mvp as rel
from saddlebench.prototype import reaction_mvp as rm


def geometry(pos,cell,pbc):
    return {'numbers':[6]*len(pos),'positions_angstrom':np.asarray(pos).tolist(),
            'cell_angstrom':np.asarray(cell).tolist(),'pbc':list(pbc),
            'constraints_known':False,'fixed_indices':[]}


@pytest.mark.parametrize('cell',[
    [[12.,0,0],[0,10,0],[0,0,11]],
    [[10.,0,0],[9.6,0.9,0],[0,0,10]],
    [[4.,0,0],[2.,3.464101615,0],[0,0,9]],
])
def test_filter_never_rejects_exact_match_randomized(cell):
    rng=np.random.default_rng(98)
    pbc=[True,True,True]
    a=geometry(rng.normal(size=(20,3)),cell,pbc)
    for _ in range(65):
        perturb=rng.normal(scale=rng.uniform(.005,.25),size=(20,3))
        shift=rng.normal(size=3)
        b=geometry(np.asarray(a['positions_angstrom'])+perturb+shift,cell,pbc)
        exact=core_drift_maxdev(a,b)
        if exact<=.2+1e-12:
            assert not cannot_match_by_anchor(a,b,.2)


def test_far_geometries_can_be_rejected_without_falsely_claiming_a_distance():
    cell=np.diag([20.,20.,20.]);a=geometry(np.zeros((12,3)),cell,[True]*3)
    b=geometry(np.arange(36).reshape(12,3)*.4,cell,[True]*3)
    assert cannot_match_by_anchor(a,b,.2)
    assert core_drift_maxdev(a,b)>.2


def test_diagnostic_index_reads_once_for_two_cases_and_keeps_start_rule(tmp_path):
    cases=[{'case_id':'case1','structure_id':'p1','starting_minimum_id':'m1'},
           {'case_id':'case2','structure_id':'p1','starting_minimum_id':'m2'}]
    d=tmp_path/'private';d.mkdir()
    records=[{'structure_id':'p1','starting_minimum_ids':['m1'],'geometry':{'numbers':[6]}},
             {'structure_id':'p1','starting_minimum_ids':[],'geometry':{'numbers':[6]}},
             {'structure_id':'p2','starting_minimum_ids':[],'geometry':{'numbers':[6]}},
             {'structure_id':'p1','starting_minimum_ids':['m2'],'geometry':{'numbers':[8]}}]
    with gzip.open(d/'diagnostic_saddles.jsonl.gz','wt') as f:
        for x in records:f.write(json.dumps(x)+'\n')
    (d/'private_manifest.json').write_text(json.dumps({'diagnostic_saddle_status_counts':{'all':4}}))
    steps=[]
    got=rel._prepare_diag_lookups(d,cases,lambda *a:steps.append(a))
    assert len(got['case1'][(6,)])==2
    assert len(got['case2'][(6,)])==1
    assert len(got['case2'][(8,)])==1
    assert got['case1'][(6,)][0]['start_known']
    assert not got['case2'][(6,)][0]['start_known']
    assert steps[-1]==('diagnostic_index',4,4)


def test_eligible_score_progress_and_cache_same_result(tmp_path):
    from test_reaction_prototype_modes import dataset
    evidence=dataset(tmp_path)
    rm.build(evidence,tmp_path/'reference','all_saddles','pes:test')
    prepared=rm.load_reference(tmp_path/'reference')
    member=prepared[1][0]['members'][0]
    data={'submission_id':'a','geometry':member['geometry'],
          'starting_minimum_id':'m0','parent_structure_id':'synthetic:p1','pes_id':'pes:test'}
    cb=[]
    with patch.object(rm,'read_submissions',return_value=[data]):
        old=rm.score(tmp_path/'reference','ignored',3600)
        new=rm.score(tmp_path/'reference','ignored',3600,progress=lambda *x:cb.append(x),
                     prepared_reference=prepared)
    assert old==new
    assert ('eligible',1,1) in cb
    assert ('reference_load',1,1) in cb


def test_diagnostic_filter_retains_unknown_start(tmp_path):
    cell=np.diag([15,15,15]);a=geometry(np.zeros((8,3)),cell,[True]*3)
    item={'saddle_id':'s1','saddle_group_id':'g','registered_saddle_group_size':3,
          'diagnostic_kind':'KNOWN_NON_FIRST_ORDER','start_known':False,
          'geometry':a,'hessian_status':'NON_FIRST_ORDER','barrier_eV':[]}
    result=rel.diagnose({'status':'NO_MATCH'},{'geometry':a},{tuple(a['numbers']):[item]})
    assert result['diagnostic']=='UNKNOWN_START_PROVENANCE'
    assert result['matched_saddle_groups'][0]['registered_saddle_count']==3


def test_batch_scoring_loads_eligible_and_diagnostic_once(monkeypatch,tmp_path):
    import csv
    pub=tmp_path/'public';private=tmp_path/'private';pub.mkdir();private.mkdir()
    with (pub/'cases.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case_id','structure_id','starting_minimum_id'])
        w.writeheader();w.writerow({'case_id':'case1','structure_id':'p1','starting_minimum_id':'m1'})
        w.writerow({'case_id':'case2','structure_id':'p2','starting_minimum_id':'m2'})
    for i in (1,2):(tmp_path/f'c{i}.traj').write_bytes(b'test')
    csvpath=tmp_path/'submissions.csv'
    csvpath.write_text('case_id,trajectory,elapsed_seconds\ncase1,c1.traj,12\ncase2,c2.traj,13\n')
    calls={'ref':0,'diag':0,'case':[]}
    def load(_):calls['ref']+=1;return ('manifest','reference')
    def index(_,cases,progress=None):
        calls['diag']+=1
        return {c['case_id']:{'existing':'lookup'} for c in cases}
    def case(_,__,cid,___,seconds,*,progress=None,diagnostic_lookup=None,prepared_reference=None):
        calls['case'].append((cid,diagnostic_lookup,prepared_reference))
        return {'known_eligible_reactions_K':2,'unique_reactions_U':1,'known_bad_unique_B':0,
                'coverage':.5,'self_reported_elapsed_seconds':seconds}
    monkeypatch.setattr(rm,'load_reference',load)
    monkeypatch.setattr(rel,'_prepare_diag_lookups',index)
    monkeypatch.setattr(rel,'grade_case',case)
    result=rel.grade_batch(pub,private,csvpath)
    assert result['selected_cases']==2
    assert calls['ref']==1 and calls['diag']==1
    assert all(d=={'existing':'lookup'} and ref==('manifest','reference') for _,d,ref in calls['case'])


def test_progress_stream_is_stderr_only(tmp_path,capsys):
    import importlib.util
    p=Path(__file__).resolve().parents[1]/'scripts'/'score_from_hf.py'
    spec=importlib.util.spec_from_file_location('sbe_rc20_cli',p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    reporter=module.Progress()
    reporter('case0006','eligible',0,250)
    reporter('case0006','eligible',10,250)
    reporter('case0006','eligible',250,250)
    capture=capsys.readouterr()
    assert not capture.out
    assert 'SCORING_PROGRESS case=case0006 phase=eligible completed=250 total=250' in capture.err
    silent=module.Progress(False)
    silent('case0006','eligible',25,250)
    assert capsys.readouterr().err==''
