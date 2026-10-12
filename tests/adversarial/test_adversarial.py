import copy
import pytest
from saddlebench.scoring.engine import score
from saddlebench.matching.geometry import core_drift_maxdev
from examples.generate_fixture import geom

def frame(y,scope='min-A'):
    return {'geometry':geom(y),'submission_id':'audit','starting_minimum_id':scope,
            'pes_id':'PES-A','parent_structure_id':'parent-A'}

def test_duplicate_and_wrong_start_same_geometry(refs):
    r=score([frame(0,'wrong-minimum')],refs,3600)
    assert r['eligible_unique_count']==0
    assert r['per_submission'][0]['geometry_state']=='UNIQUE_REFERENCE_MATCH'

def test_candidate_energy_ignored(refs):
    s=frame(2)
    s['energy']={'saddle_ev':-20,'minimum_ev':-50}
    assert score([s],refs,3600)['eligible_unique_count']==0

def test_reference_only_positive_malformed_evidence(refs):
    a=copy.deepcopy(next(r for r in refs.records if r['reference_id']=='a'))
    a['doublemin']['endpoints'][0]['final_fmax_ev_a']=float('nan')
    from saddlebench.scoring.engine import classify
    assert classify(a,'min-A','PES-A','parent-A')['connection_state']=='UNKNOWN'

def test_cell_skew_and_constraint_unknown_no_implicit_equivalence():
    a=geom(0);b=copy.deepcopy(a)
    b['constraints_known']=False
    with pytest.raises(ValueError,match='UNKNOWN_CONSTRAINTS'):core_drift_maxdev(a,b)
