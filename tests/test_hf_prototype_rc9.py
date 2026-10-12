from saddlebench.prototype.release_mvp import confidence_from_counts,score_metric,_diag_kind,diagnose
import pytest

def test_confidence_200_example():
    # 201 observations of one known reaction always leave 200 redundant.
    n,c=confidence_from_counts({'rx':201},seed=42)
    assert n==200 and c==pytest.approx(0.95)

def test_shuffling_reproducible():
    assert confidence_from_counts({'a':10,'b':10},seed=11)==confidence_from_counts({'a':10,'b':10},seed=11)

def test_missing_confidence():
    assert confidence_from_counts({'rx':1})==(None,None)

def test_coverage_squares_and_no_duplication():
    assert score_metric(10,20,0,3600)==pytest.approx(25.)
    assert score_metric(10,20,5,3600)==pytest.approx(12.5)
    assert score_metric(18,20,0,3600)==pytest.approx(81.)
    assert score_metric(0,20,0,3600)==0

def test_no_denominator_case_unscorable():
    assert score_metric(0,0,0,3600) is None

def test_status_precedence():
    s={'hessian_status':'NON_FIRST_ORDER'}
    assert _diag_kind(s,[{'connected_to_canonical_minimum':1,'barrier_eV':2.2}])=='KNOWN_NON_FIRST_ORDER'
    s={'hessian_status':'FIRST_ORDER'}
    assert _diag_kind(s,[{'connected_to_canonical_minimum':1,'barrier_eV':1.8}])=='KNOWN_CONNECTED_HIGH_BARRIER'
    assert _diag_kind(s,[{'connected_to_canonical_minimum':0,'barrier_eV':2.1}])=='KNOWN_FIRST_ORDER_DISCONNECTED'

def test_no_match_unknown_neutral():
    result=diagnose({'status':'NO_MATCH','submission_id':'1'},
       {'geometry':{'numbers':[1]}},{})
    assert result['diagnostic']=='UNKNOWN_UNMATCHED' and result['penalty_key'] is None

def test_no_double_bad_penalty(monkeypatch):
    from saddlebench.prototype import release_mvp as m
    monkeypatch.setattr(m,'core_drift_maxdev',lambda *args:0.0)
    obj={'saddle_id':'s1','saddle_group_id':'g1','starting_minimum_ids':['m1'],
         'start_known':True,'hessian_status':'NON_FIRST_ORDER',
         'diagnostic_kind':'KNOWN_NON_FIRST_ORDER', 'geometry':{'numbers':[1]},'barrier_eV':[2.0]}
    out=diagnose({'status':'NO_MATCH'}, {'geometry':{'numbers':[1]}},{(1,):[obj,obj]})
    assert out['diagnostic']=='KNOWN_BAD_NON_FIRST_ORDER' and out['penalty_key']=='g1'

def test_multiple_distinct_saddle_groups_ambiguous(monkeypatch):
    from saddlebench.prototype import release_mvp as m
    monkeypatch.setattr(m,'core_drift_maxdev',lambda *args:0.0)
    objs=[{'saddle_id':f's{i}','saddle_group_id':f'g{i}','starting_minimum_ids':['m1'],
         'start_known':True,'hessian_status':'NON_FIRST_ORDER',
         'diagnostic_kind':'KNOWN_NON_FIRST_ORDER','geometry':{'numbers':[1]},'barrier_eV':[]} for i in (1,2)]
    out=diagnose({'status':'NO_MATCH'}, {'geometry':{'numbers':[1]}},{(1,):objs})
    assert out['diagnostic']=='AMBIGUOUS_CATALOG_MATCH' and out['penalty_key'] is None

def test_stage_public_private_split_with_mocked_ase(monkeypatch,tmp_path):
    # Synthetic end-to-end staging validates file segregation without LS6/ASE.
    import hashlib,json,sys,types
    from pathlib import Path
    from saddlebench.prototype import release_mvp as m
    from saddlebench.prototype import reaction_mvp as rm
    fixture=Path(__file__).parent/'fixtures'/'synthetic_evidence_v1'
    geo={g['geometry_id']:g for g in rm.rows(fixture,'geometries.jsonl')}
    original_min=next(rm.rows(fixture,'minima.jsonl'))
    minimum_geo=geo[original_min['geometry_id']]
    g={'numbers':minimum_geo['ordered_numbers'],'positions_angstrom':minimum_geo['positions_angstrom'],
        'cell_angstrom':minimum_geo['cell_angstrom'],'pbc':minimum_geo['pbc'],
        'constraints_known':True,'fixed_indices':[]}
    inputs=tmp_path/'inputs';inputs.mkdir()
    rec={'minimum_id':original_min['minimum_id'],'parent_structure_id':original_min['parent_structure_id'],
         'geometry':g,'constraints_metadata_verified':True}
    (inputs/'all_minima.jsonl').write_text(json.dumps(rec)+'\n')
    (inputs/'manifest.json').write_text(json.dumps({'source_registry_sha256':'f'*64,
         'file_sha256':{'all_minima.jsonl':m.sha(inputs/'all_minima.jsonl')}}))
    ref=tmp_path/'reference';ref.mkdir()
    rn={'reaction_id':'rx1','structure_id':original_min['parent_structure_id'],
        'starting_minimum_id':original_min['minimum_id'],'pes_id':'REGISTRY_PES_SCOPE_UNVERIFIED',
        'confirming_observation_ids':['o1'],'members':[]}
    raw=json.dumps(rn)+'\n'
    (ref/'reference.jsonl').write_text(raw)
    (ref/'starting_minima.jsonl').write_text('')
    (ref/'manifest.json').write_text(json.dumps({'schema_version':'saddlebench-reaction-prototype-v1',
           'mode':'all_saddles','registry_sha256':'f'*64,
           'reference_sha256':hashlib.sha256(raw.encode()).hexdigest()}))
    ase=types.ModuleType('ase');aseio=types.ModuleType('ase.io');asedata=types.ModuleType('ase.data')
    aseio.write=lambda path,atom,format:Path(path).write_bytes(b'FAKE_TRAJ_TEST_ONLY')
    asedata.chemical_symbols=['X']*120
    asedata.chemical_symbols[1]='H';asedata.chemical_symbols[6]='C'
    monkeypatch.setitem(sys.modules,'ase',ase)
    monkeypatch.setitem(sys.modules,'ase.io',aseio)
    monkeypatch.setitem(sys.modules,'ase.data',asedata)
    monkeypatch.setattr(rm,'_atoms_from_geometry',lambda *a,**k:object())
    root=tmp_path/'stage'
    result=m.stage(fixture,ref,inputs,root)
    assert result['case_count']==1
    assert len(list((root/'public'/'minima').glob('*.traj')))==1
    assert (root/'public'/'cases.csv').is_file()
    assert (root/'private'/'diagnostic_saddles.jsonl.gz').is_file()
    assert not (root/'public'/'reference.jsonl').exists()
    assert not (root/'public'/'diagnostic_saddles.jsonl.gz').exists()
    assert (root/'READY.json').exists()
    with pytest.raises(FileExistsError):m.stage(fixture,ref,inputs,root)

def test_grade_case_detailed_bad_once_and_duplicates(monkeypatch,tmp_path):
    import json,gzip,csv
    from pathlib import Path
    from saddlebench.prototype import release_mvp as m
    from saddlebench.prototype import reaction_mvp as rm
    pub=tmp_path/'public';priv=tmp_path/'private';pub.mkdir();priv.mkdir()
    with (pub/'cases.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=m.CASE_COLUMNS);w.writeheader()
        w.writerow({'case_id':'case0001','structure_id':'x:a','starting_minimum_id':'m1','pes_id':'p1',
            'registry_sha256':'f'*64,'known_reaction_count':2,'exploration_confidence_heuristic':''})
    (priv/'private_manifest.json').write_text(json.dumps({'source_registry_sha256':'f'*64}))
    (priv/'reference').mkdir()
    candidates=[{'saddle_id':'s2','structure_id':'x:a','starting_minimum_ids':['m1'],
        'saddle_group_id':None,'hessian_status':'NON_FIRST_ORDER','diagnostic_kind':'KNOWN_NON_FIRST_ORDER',
        'geometry':{'numbers':[1]},'barrier_eV':[]}]
    with gzip.open(priv/'diagnostic_saddles.jsonl.gz','wt') as f:
        for candidate in candidates:f.write(json.dumps(candidate)+'\n')
    monkeypatch.setattr(rm,'score',lambda *a,**k:{'submissions':[
        {'status':'CREDIT','submission_id':'a'},
        {'status':'DUPLICATE','submission_id':'b'},
        {'status':'NO_MATCH','submission_id':'c'},
        {'status':'NO_MATCH','submission_id':'d'}], 'eligible_unique_count':1})
    monkeypatch.setattr(rm,'read_submissions',lambda *a,**k:[{'geometry':{'numbers':[1]}}]*4)
    monkeypatch.setattr(m,'core_drift_maxdev',lambda *a,**k:0.)
    result=m.grade_case(pub,priv,'case0001','submission.traj',3600)
    assert result['unique_reactions_U']==1 and result['known_bad_unique_B']==1
    assert result['coverage']==0.5
    assert result['provisional_score_per_hour']==pytest.approx(100*.25/3)
    assert [r['status'] for r in result['entries']]==['CREDIT','DUPLICATE','NO_MATCH','NO_MATCH']
    assert result['entries'][2]['diagnostic']=='KNOWN_BAD_NON_FIRST_ORDER'

def test_batch_scoring_uses_all_selected_cases(monkeypatch,tmp_path):
    from saddlebench.prototype import release_mvp as m
    from pathlib import Path
    import csv
    pub=tmp_path/'public';pub.mkdir();(pub/'cases.csv').write_text('case_id\ncase0001\ncase0002\ncase0003\n')
    (tmp_path/'a.traj').write_bytes(b'fake');(tmp_path/'b.traj').write_bytes(b'fake')
    csvpath=tmp_path/'submissions.csv'
    csvpath.write_text('case_id,trajectory,elapsed_seconds\ncase0001,a.traj,3600\ncase0002,b.traj,3600\n')
    def fake(pub,priv,cid,pred,sec):
        return {'coverage':0.5,'known_eligible_reactions_K':2,'unique_reactions_U':1,
                'known_bad_unique_B':1 if cid=='case0001' else 0,
                'self_reported_elapsed_seconds':sec}
    monkeypatch.setattr(m,'grade_case',fake)
    report=m.grade_batch(pub,tmp_path,csvpath)
    assert report['aggregate']['reference_known_K']==4
    assert report['aggregate']['unique_credited_U']==2
    assert report['aggregate']['known_bad_unique_B']==1
    assert report['aggregate']['micro_coverage']==.5
    assert report['selected_cases']==2 and report['available_cases']==3
    with pytest.raises(ValueError):
        csvpath.write_text('case_id,trajectory,elapsed_seconds\ncase0001,a.traj,3600\ncase0001,b.traj,3600\n')
        m.grade_batch(pub,tmp_path,csvpath)
