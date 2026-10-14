"""Public 3-mode API contract, first-hit scientific separation and process defaults."""
from __future__ import annotations
import csv
import json
import sys
import types
from pathlib import Path
import numpy as np
import pytest
from saddlebench import score_modes as api
from saddlebench.matching import greedy_modes

# This deliverable stages on top of an existing full pinned repository.
# The local unit-only runner supplies missing unchanged reference helper imports.
@pytest.fixture(autouse=True)
def _stub_unchanged_repo_snapshot(monkeypatch):
    import sys,types
    try:
        import saddlebench.reference.snapshot
        return
    except ModuleNotFoundError:
        pass
    pkg=types.ModuleType('saddlebench.reference')
    pkg.__path__=[]
    snap=types.ModuleType('saddlebench.reference.snapshot')
    snap.canonical=lambda x:json.dumps(x,sort_keys=True)
    snap.validate_geometry=lambda x:x
    snap.sha=lambda x:'stub'
    monkeypatch.setitem(sys.modules,'saddlebench.reference',pkg)
    monkeypatch.setitem(sys.modules,'saddlebench.reference.snapshot',snap)
    safe=types.ModuleType('saddlebench.reference.safe_output')
    safe.AtomicDirectory=type('AtomicDirectory',(),{})
    monkeypatch.setitem(sys.modules,'saddlebench.reference.safe_output',safe)
    io=types.ModuleType('saddlebench.io');io.__path__=[]
    extxyz=types.ModuleType('saddlebench.io.extxyz');extxyz.read_extxyz=lambda *_:[]
    monkeypatch.setitem(sys.modules,'saddlebench.io',io)
    monkeypatch.setitem(sys.modules,'saddlebench.io.extxyz',extxyz)


def geo(d):
    p=np.arange(30,dtype=float).reshape(10,3)/10
    p[:,0]+=d*np.arange(10)/10
    return {'numbers':[6]*10,'positions_angstrom':p.tolist(),
            'cell_angstrom':(np.eye(3)*20).tolist(),'pbc':[False]*3}


def mock_data(tmp_path, monkeypatch):
    first=geo(0)
    bad=geo(1.2)
    neutral=geo(2.4)
    missing=geo(8.5)
    reactions=[{'reaction_id':'reactionA','structure_id':'s','starting_minimum_id':'m',
                'pes_id':'p','members':[{'geometry':first,'saddle_group_id':'eligible-group'}]}]
    preds=[{'submission_id':str(i),'geometry':g,'starting_minimum_id':'m',
            'parent_structure_id':'s','pes_id':'p'} for i,g in enumerate([first,first,bad,neutral,missing])]
    rm=__import__('saddlebench.prototype.reaction_mvp',fromlist=['*'])
    monkeypatch.setattr(rm,'load_reference',lambda path: ({},reactions))
    monkeypatch.setattr(rm,'read_submissions',lambda *args,**kwargs:preds)
    diag={'geometry':bad,'structure_id':'s','diagnostic_kind':'KNOWN_NON_FIRST_ORDER',
          'saddle_id':'bad-1','saddle_group_id':'bad-group','start_known':True}
    from saddlebench.matching import fast_reference
    monkeypatch.setattr(fast_reference,'load_nonfo_for_case',lambda *_:[diag])
    from saddlebench.prototype import release_mvp as rel
    monkeypatch.setattr(rel,'_prepare_diag_lookup',lambda *_:{tuple(bad['numbers']): [diag,
        {'geometry':neutral,'structure_id':'s','diagnostic_kind':'KNOWN_DISCONNECTED_FIRST_ORDER',
         'saddle_id':'neutral-1','saddle_group_id':'neutral-group','start_known':True}]})
    private=tmp_path/'private'; private.mkdir()
    (private/'reference').mkdir()
    (private/'private_manifest.json').write_text(json.dumps({'source_registry_sha256':'abc'}))
    case={'case_id':'caseX','structure_id':'s','starting_minimum_id':'m',
          'pes_id':'p','registry_sha256':'abc','known_reaction_count':'1'}
    return private,case,diag


def test_default_mode_constants_and_formula():
    assert api.MODES == ('score','score_detailed','coverage')
    assert api._score_metric(20,23,0,1646)==pytest.approx(165.37771581217683)
    assert api._score_metric(0,23,0,100)==0.
    assert api._score_metric(0,0,0,100) is None


def test_score_is_eligible_first_then_greedy_nonfo(tmp_path,monkeypatch):
    private,case,_=mock_data(tmp_path,monkeypatch)
    report=greedy_modes.run_case(None,private,case,'unused',3600,'cache',mode='score',
                                 batch_size=8,cache_root=tmp_path/'cache')
    assert [e['status'] for e in report['entries']]==[
        'CREDIT','DUPLICATE','NONFO_FIRST_HIT','UNKNOWN_UNMATCHED','UNKNOWN_UNMATCHED']
    assert report['greedy_unique_reactions_U']==1
    assert report['greedy_nonfo_first_hit_groups']==1
    assert report['greedy_potential_bad_first_hit_groups']==1
    assert report['known_bad_unique_B_full'] is None


def test_coverage_never_even_loads_nonfo(tmp_path,monkeypatch):
    private,case,_=mock_data(tmp_path,monkeypatch)
    from saddlebench.matching import fast_reference
    def forbidden(*args): raise AssertionError('coverage read non-FO catalog')
    monkeypatch.setattr(fast_reference,'load_nonfo_for_case',forbidden)
    report=greedy_modes.run_case(None,private,case,'unused',3600,None,mode='coverage',
                                 batch_size=8,cache_root=tmp_path/'cache')
    assert [e['status'] for e in report['entries']]==[
        'CREDIT','DUPLICATE','UNKNOWN_UNMATCHED','UNKNOWN_UNMATCHED','UNKNOWN_UNMATCHED']
    assert report['stage_counts'].get('NONFO_FIRST_HIT',0)==0


def test_single_case_worker_schema_marks_noncanonical_B(tmp_path,monkeypatch):
    private,case,_=mock_data(tmp_path,monkeypatch)
    task={'public':str(tmp_path),'private':str(private),'case':case,
          'trajectory':'unused','elapsed_seconds':3600.,'mode':'score',
          'microbatch_size':8,'nonfo_cache':'cache','index_cache':str(tmp_path/'cache')}
    result=api._case_worker(task)
    assert result['provisional_score_per_hour']==pytest.approx(100/3)
    assert result['first_hit_nonfo_groups_with_known_start']==1
    assert result['canonical_known_bad_unique_B'] is None
    assert result['score_semantics']=='GREEDY_FIRST_HIT_NONFO'
    task['mode']='coverage';task['nonfo_cache']=None
    result=api._case_worker(task)
    assert result['provisional_score_per_hour']==pytest.approx(100)
    assert result['canonical_known_bad_unique_B'] is None
    assert result['nonfo_checked'] is False


def test_aggregate_score_and_coverage_are_distinct():
    item={'known_eligible_reactions_K':2,'greedy_unique_reactions_U':1,'coverage':.5,
          'self_reported_elapsed_seconds':3600.,'first_hit_nonfo_groups_with_known_start':1,
          'first_hit_potential_bad_groups_with_known_start':1}
    scored=api._batch_report([item,item],mode='score',total_available=123)
    covered=api._batch_report([item,item],mode='coverage',total_available=123)
    assert scored['aggregate']['provisional_score_per_hour']==pytest.approx(100*(.5**2)*(2/(2+2*2))*(3600/7200))
    assert covered['aggregate']['provisional_score_per_hour']==pytest.approx(100*.25*.5)
    assert scored['aggregate']['canonical_known_bad_unique_B'] is None
    assert covered['aggregate']['first_hit_nonfo_groups_with_known_start'] is None


def test_detailed_worker_routes_to_greedy_full(tmp_path,monkeypatch):
    private,case,_=mock_data(tmp_path,monkeypatch)
    task={'public':str(tmp_path),'private':str(private),'case':case,
          'trajectory':'unused','elapsed_seconds':3600.,'mode':'score_detailed',
          'microbatch_size':8,'nonfo_cache':'cache','index_cache':str(tmp_path/'cache')}
    result=api._case_worker(task)
    assert result['schema_version']=='saddlebench-quick-case-v1'
    assert result['score_semantics']=='GREEDY_FIRST_HIT_FULL_DIAGNOSTICS'
    assert result['canonical_known_bad_unique_B'] is None
    assert result['nonfo_checked'] and result['full_diagnostic_checked']
    assert [x['status'] for x in result['entries']]==[
        'CREDIT','DUPLICATE','NONFO_FIRST_HIT','CATALOG_FIRST_HIT','UNKNOWN_UNMATCHED']
    assert result['first_hit_potential_bad_groups_with_known_start']==1
    assert result['first_hit_nonfo_groups_with_known_start'] is None


def test_process_control_default_serial_and_explicit_parallel(tmp_path,monkeypatch):
    cases=tmp_path/'cases.csv'
    with cases.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case_id']);w.writeheader()
        w.writerow({'case_id':'x'});w.writerow({'case_id':'y'})
    csvfile=tmp_path/'submitted.csv'
    with csvfile.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case_id','trajectory','elapsed_seconds'])
        w.writeheader();w.writerow({'case_id':'x','trajectory':'x.traj','elapsed_seconds':'1'})
        w.writerow({'case_id':'y','trajectory':'y.traj','elapsed_seconds':'2'})
    (tmp_path/'x.traj').touch();(tmp_path/'y.traj').touch()
    called=[]
    def fake_worker(task):
        called.append(task['case']['case_id'])
        return {'case_id':task['case']['case_id'],'known_eligible_reactions_K':1,
                'greedy_unique_reactions_U':1,'coverage':1.,
                'self_reported_elapsed_seconds':task['elapsed_seconds'],
                'first_hit_nonfo_groups_with_known_start':None,
                'first_hit_potential_bad_groups_with_known_start':None}
    monkeypatch.setattr(api,'_case_worker',fake_worker)
    r=api.run(casefile=cases,private=tmp_path,submission_csv=csvfile,mode='coverage',index_cache=tmp_path/'cache')
    assert called==['x','y'] and [v['case_id'] for v in r['case_results']]==['x','y']
    tracker=[]
    class Pool:
        def __init__(self,max_workers):tracker.append(max_workers)
        def __enter__(self):return self
        def __exit__(self,*a):return None
        def map(self,fn,jobs):return (fn(x) for x in jobs)
    monkeypatch.setattr(api,'ProcessPoolExecutor',Pool)
    r=api.run(casefile=cases,private=tmp_path,submission_csv=csvfile,mode='coverage',
              processes=2,index_cache=tmp_path/'cache')
    assert tracker==[2]
    assert [v['case_id'] for v in r['case_results']]==['x','y']
    with pytest.raises(ValueError,match='PROCESSES_MUST_BE_POSITIVE'):
        api.run(casefile=cases,private=tmp_path,submission_csv=csvfile,mode='coverage',processes=0)


def test_cli_defaults_and_explicit_mode_without_network(tmp_path,monkeypatch,capsys):
    observed=[]
    monkeypatch.setattr(api,'verify_reference',lambda *args:True)
    monkeypatch.setattr(api,'run',lambda **kw:(observed.append(kw) or {'case_id':'x','mode':kw['mode']}))
    from saddlebench.matching import fast_reference
    monkeypatch.setattr(fast_reference,'verify_published_diagnostics',lambda *args:True)
    monkeypatch.setattr(fast_reference,'make_nonfo_cache',lambda *args:(tmp_path/'nonfo',{},'CACHED'))
    base=['--reference-root',str(tmp_path),'--cases-csv',str(tmp_path/'cases.csv'),
          '--case-id','x','--submission','x.traj','--elapsed-seconds','50']
    assert api.main(base+['--output',str(tmp_path/'default.json')])==0
    assert observed[-1]['mode']=='score' and observed[-1]['processes']==1
    assert api.main(base+['--mode','coverage','--processes','4',
                   '--output',str(tmp_path/'coverage.json')])==0
    assert observed[-1]['mode']=='coverage' and observed[-1]['processes']==4
    assert observed[-1]['nonfo_cache'] is None
    assert api.main(base+['--mode','score_detailed',
                   '--output',str(tmp_path/'detailed.json')])==0
    assert observed[-1]['mode']=='score_detailed'
    with pytest.raises(SystemExit,match='OUTPUT_ALREADY_EXISTS'):
        api.main(base+['--output',str(tmp_path/'default.json')])


def test_detailed_batch_serial_routes_via_greedy_worker(tmp_path,monkeypatch):
    cases=tmp_path/'cases.csv';cases.write_text('case_id\nx\n')
    submitted=tmp_path/'manifest.csv';submitted.write_text('case_id,trajectory,elapsed_seconds\nx,x.traj,10\n')
    (tmp_path/'x.traj').touch()
    seen=[]
    def fake_worker(task):
        seen.append(task['mode'])
        return {'case_id': 'x', 'known_eligible_reactions_K':1,
                'greedy_unique_reactions_U':1,'coverage':1.,
                'self_reported_elapsed_seconds':10.,
                'first_hit_potential_bad_groups_with_known_start':0}
    monkeypatch.setattr(api,'_case_worker',fake_worker)
    result=api.run(casefile=cases,private=tmp_path,submission_csv=submitted,
                   mode='score_detailed',nonfo_cache=tmp_path/'nonfo',index_cache=tmp_path/'idx')
    assert seen==['score_detailed']
    assert result['schema_version']=='saddlebench-quick-batch-v1'
    assert result['mode']=='score_detailed'
    assert result['aggregate']['canonical_known_bad_unique_B'] is None


def test_detailed_batch_quick_semantics_are_not_native_w2():
    one={'known_eligible_reactions_K':2,'greedy_unique_reactions_U':1,
         'self_reported_elapsed_seconds':3600.,'coverage':.5,
         'first_hit_potential_bad_groups_with_known_start':1}
    report=api._batch_report([one], 'score_detailed',123)
    assert report['schema_version']=='saddlebench-quick-batch-v1'
    assert report['aggregate']['canonical_known_bad_unique_B'] is None
    assert report['aggregate']['first_hit_potential_bad_groups_with_known_start']==1
    assert report['aggregate']['provisional_score_per_hour']==pytest.approx(100*(.5**2)*(1/(1+2)))
