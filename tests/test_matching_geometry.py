import copy
import pytest
from saddlebench.matching.geometry import core_drift_maxdev, complete_link_diameter
from saddlebench.matching.matcher import match
from examples.generate_fixture import geom

def test_translation_invariant():
    a=geom(0);b=copy.deepcopy(a)
    for row in b['positions_angstrom']:
        row[0]+=4;row[1]-=3
    assert core_drift_maxdev(a,b)<1e-10

def test_exact_mic_wrap():
    a=geom(0);b=copy.deepcopy(a)
    a['pbc']=b['pbc']=[True,False,False]
    a['positions_angstrom'][2][0]=9.94
    b['positions_angstrom'][2][0]=0.06
    assert core_drift_maxdev(a,b)==pytest.approx(0.12)

def test_core_median_tie_half():
    a=geom(0);b=geom(0.2)
    assert core_drift_maxdev(a,b)==pytest.approx(0.2)
    assert core_drift_maxdev(a,geom(0.2001))>0.2

def test_complete_link_not_single_link():
    assert complete_link_diameter([geom(0),geom(.08),geom(.16)])==pytest.approx(.16)

def test_order_pbc_cell_fixed_constraints():
    a=geom(0);b=copy.deepcopy(a)
    for changed in [dict(numbers=[6,1,8]),dict(pbc=[True,False,False]),dict(fixed_indices=[0]),
                    dict(constraints_known=False),dict(cell_angstrom=[[10.051,0,0],[0,10,0],[0,0,10]])]:
        b=copy.deepcopy(a);b.update(changed)
        with pytest.raises(ValueError):core_drift_maxdev(a,b)

def test_matches_all_ambiguous(refs):
    s={'geometry':geom(18.15)}
    res=match(s,refs)
    assert res['state']=='AMBIGUOUS_REFERENCE_MATCH'
    assert {x['reference_id'] for x in res['matches']}=={'j','k'}

def test_same_identity_not_ambiguous(refs):
    result=match({'geometry':geom(0.)},refs)
    assert result['state']=='UNIQUE_REFERENCE_MATCH'
    assert {x['reference_id'] for x in result['matches']}=={'a','a-start-B'}

def test_triclinic_exact_nearest_periodic():
    a=geom(0);b=copy.deepcopy(a)
    skew=[[2.0,0,0],[1.99,0.2,0],[0,0,8]]
    a['cell_angstrom']=b['cell_angstrom']=skew
    a['pbc']=b['pbc']=[True,True,False]
    b['positions_angstrom'][2][0]+=3.99
    b['positions_angstrom'][2][1]+=.2
    assert core_drift_maxdev(a,b)<1e-9
