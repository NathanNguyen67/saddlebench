"""One-row synthetic builder and evidence-mode smoke; no fabricated force calls."""
import hashlib,json
from pathlib import Path
from tools import build_catalog_from_lean as b
from saddlebench.reference.catalog_v2 import load_catalog

def test_builder_and_scoring_separate_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'EXPECTED',dict.fromkeys(b.TABLES,1))
    lean=tmp_path/'lean';lean.mkdir();(lean/'EXPORT_COMPLETE').write_text('PASS\n')
    (lean/'manifest.json').write_text(json.dumps({'schema_version':'saddlebench-lean-registry-v1','source_counts':dict.fromkeys(b.TABLES,1)}))
    orig_hash=hashlib.sha256((lean/'manifest.json').read_bytes()).hexdigest()
    proj=tmp_path/'proj';proj.mkdir();(proj/'PROJECTION_COMPLETE').write_text('PASS\n')
    assoc='m1';st='s1';g={'ordered_numbers':[1,1], 'positions_angstrom':[[0.,0.,0.],[0.5,0.,0.]],
                         'cell_angstrom':[[3.,0.,0.],[0.,3.,0.],[0.,0.,3.]],'pbc':[True,True,True]}
    rows={
       'canonical_minima':{'minimum_id':assoc,'parent_structure_id':st},
       'saddle_registry':{'canonical_saddle_id':'sad1','structure_id':st,'saddle_group_id':'grp1',
                          'geometry':g,'geometry_status':'OK','hessian_status':'FIRST_ORDER'},
       'reaction_registry':{'canonical_reaction_id':'rx1','structure_id':st,'saddle_group_id':'grp1',
                            'connected_to_canonical_minimum':True,'rate_relevant_registry_flag':False,'barrier_eV':0.6},
       'reaction_observations':{'observation_id':'obs1','canonical_reaction_id':'rx1',
                                'structure_id':st,'saddle_group_id':'grp1','canonical_saddle_id':'sad1',
                                'outcome':'SUCCESS','minus':{'status':'OK'},'plus':{'status':'OK'}}}
    sha={}
    for k,folder in b.TABLES.items():
        (proj/folder).mkdir()
        rec=rows[k];rec['associated_starting_minimum_id']=assoc
        p=proj/folder/'part-000.jsonl';p.write_text(json.dumps(rec)+'\n')
        sha[str(p.relative_to(proj))]=hashlib.sha256(p.read_bytes()).hexdigest()
    pm={'schema':'saddlebench-associated-start-projection-v1','source_manifest_sha256':orig_hash,
        'basis':'DERIVED_UNIQUE_REGISTERED_MINIMUM_FOR_STRUCTURE_NOT_OBSERVED_SEARCH_ORIGIN',
        'counts':{x:y for k in b.TABLES for x,y in ((k,1),(k+'_annotated',1))},'shard_sha256':sha}
    (proj/'projection_manifest.json').write_text(json.dumps(pm))
    out=tmp_path/'catalog'
    meta=b.build(proj,lean,out,'PES_TEST')
    assert meta['stats']['total']==1 and meta['stats']['registry_reported_connected']==1
    assert meta['stats']['science_verified']==0
    strict,_=load_catalog(out,out/'cases.csv',case_id='s1')
    exploratory,policy=load_catalog(out,out/'cases.csv',case_id='s1',allow_registry_reported=True)
    assert strict==[] and len(exploratory)==1
    assert exploratory[0]['science_verified'] is False
    assert exploratory[0]['historical_registry_rate_relevant_flag'] is False
    assert policy['barrier_max_eV_exclusive']==1.0


def test_catalog_main_uses_input_catalog_not_historical_snapshot(tmp_path,monkeypatch):
    import pytest
    pytest.importorskip('ase')
    from ase import Atoms
    from ase.io import write
    from saddlebench import coverage_tiers
    from unittest.mock import patch
    # Reuse the one-record fixture, without any historical private_manifest.
    with patch.object(b,'EXPECTED',dict.fromkeys(b.TABLES,1)):
        lean=tmp_path/'lean';lean.mkdir();(lean/'EXPORT_COMPLETE').write_text('PASS\n')
        (lean/'manifest.json').write_text(json.dumps({'schema_version':'saddlebench-lean-registry-v1','source_counts':dict.fromkeys(b.TABLES,1)}))
        src=hashlib.sha256((lean/'manifest.json').read_bytes()).hexdigest()
        proj=tmp_path/'proj';proj.mkdir();(proj/'PROJECTION_COMPLETE').write_text('PASS\n')
        g={'ordered_numbers':[1,1],'positions_angstrom':[[0.,0.,0.],[0.5,0.,0.]],
           'cell_angstrom':[[3.,0.,0.],[0.,3.,0.],[0.,0.,3.]],'pbc':[True,True,True]}
        records={'canonical_minima':{'minimum_id':'m1','parent_structure_id':'s1'},
           'saddle_registry':{'canonical_saddle_id':'sad1','structure_id':'s1','saddle_group_id':'grp1',
                    'geometry':g,'geometry_status':'OK','hessian_status':'FIRST_ORDER'},
           'reaction_registry':{'canonical_reaction_id':'rx1','structure_id':'s1','saddle_group_id':'grp1',
                   'connected_to_canonical_minimum':True,'barrier_eV':0.6},
           'reaction_observations':{'observation_id':'obs1','canonical_reaction_id':'rx1','canonical_saddle_id':'sad1',
                     'structure_id':'s1','saddle_group_id':'grp1','minus':{'status':'OK'},
                     'plus':{'status':'OK'},'outcome':'SUCCESS'}}
        sums={}
        for name,folder in b.TABLES.items():
            (proj/folder).mkdir()
            rec=records[name];rec['associated_starting_minimum_id']='m1'
            f=proj/folder/'part-000.jsonl';f.write_text(json.dumps(rec)+'\n')
            sums[str(f.relative_to(proj))]=hashlib.sha256(f.read_bytes()).hexdigest()
        (proj/'projection_manifest.json').write_text(json.dumps({
            'schema':'saddlebench-associated-start-projection-v1','source_manifest_sha256':src,
            'basis':'DERIVED_UNIQUE_REGISTERED_MINIMUM_FOR_STRUCTURE_NOT_OBSERVED_SEARCH_ORIGIN',
            'counts':{x:y for k in b.TABLES for x,y in ((k,1),(k+'_annotated',1))},
            'shard_sha256':sums}))
        cat=tmp_path/'cat';b.build(proj,lean,cat,'PES_TEST')
    traj=tmp_path/'one.traj'
    write(str(traj),Atoms('H2',positions=[[0.,0.,0.],[0.5,0.,0.]],cell=[3.,3.,3.],pbc=True))
    result=tmp_path/'result.json'
    assert coverage_tiers.main(['--catalog-root',str(cat),'--case-id','s1','--submission',str(traj),
        '--elapsed-seconds','5.0','--output',str(result),'--index-cache-dir',str(tmp_path/'indices'),
        '--allow-registry-reported-evidence','--no-html','--no-image','--no-color'])==0
    data=json.loads(result.read_text())
    assert data['case_results'][0]['thresholds']['0.20']['known_eligible_reactions_K']==1
    assert data['case_results'][0]['thresholds']['0.20']['greedy_unique_reactions_U']==1
    assert data['case_results'][0]['starting_minimum_filter'] is False
    assert 'EXPLORATORY' in data['catalog_scoring_policy']['evidence_policy']
