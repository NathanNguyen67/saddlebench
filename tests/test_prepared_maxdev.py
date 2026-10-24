"""Scientific invariance: prepared kernel never changes canonical maxdev semantics."""
from __future__ import annotations
import copy
import numpy as np
import pytest
from saddlebench.matching.geometry import (core_drift_maxdev,prepare_geometry,
                                           core_drift_maxdev_prepared)
from saddlebench.matching.matcher import match, PreparedReferenceMatcher
from examples.generate_fixture import geom


def check(a,b):
    pa,pb=prepare_geometry(a),prepare_geometry(b)
    try:
        expected=core_drift_maxdev(a,b)
    except ValueError as e:
        with pytest.raises(ValueError,match=str(e)):
            core_drift_maxdev_prepared(pa,pb)
        return
    actual=core_drift_maxdev_prepared(pa,pb)
    assert abs(expected-actual)<=1e-11
    for t in (0.05,0.1,0.2):assert (expected<=t)==(actual<=t)


def test_random_nonperiodic_periodic_and_skewed():
    rng=np.random.default_rng(7345)
    for pbc in ([False,False,False],[True,False,False],[True,True,False],[True,True,True]):
        for skew in (False,True):
            for i in range(16):
                n=5+int(rng.integers(16))
                cell=np.array([[6.,0.,0.],[2.1 if skew else 0.,7.,0.],[0.,1.7 if skew else 0.,8.]])
                positions=rng.uniform(-1,9,size=(n,3))
                a={'numbers':[int(v) for v in rng.integers(1,20,size=n)],
                   'positions_angstrom':positions.tolist(),'cell_angstrom':cell.tolist(),
                   'pbc':list(pbc),'fixed_indices':[],'constraints_known':False}
                b=copy.deepcopy(a)
                delta=rng.normal(scale=0.2 if i%2 else 0.02,size=(n,3))
                b['positions_angstrom']=(positions+delta+np.array([3. if i%3==0 else 0.,0.,0.])).tolist()
                check(a,b)


def test_validation_errors_and_mutations():
    a=geom(0)
    for diff in [dict(numbers=[6,1,8]),dict(pbc=[True,False,False]),
                 dict(cell_angstrom=[[10.051,0,0],[0,10,0],[0,0,10]])]:
        b=copy.deepcopy(a);b.update(diff)
        check(a,b)
    cached=prepare_geometry(a)
    old=cached.positions.copy()
    a['positions_angstrom'][0][0]+=500
    assert np.array_equal(cached.positions,old)
    with pytest.raises(ValueError):cached.positions[0,0]=22
    assert core_drift_maxdev_prepared(cached,cached)==pytest.approx(0.)
    with pytest.raises(TypeError,match='PREPARED_GEOMETRY_REQUIRED'):
        core_drift_maxdev_prepared(a,a)


def test_constraints_ignored_and_halfcell_boundary():
    a=geom(0);b=copy.deepcopy(a)
    b['constraints_known']=False;b['fixed_indices']=[1]
    check(a,b)
    a['pbc']=b['pbc']=[True,False,False]
    a['positions_angstrom'][2][0]=9.94
    b['positions_angstrom'][2][0]=0.06
    check(a,b)
    b['positions_angstrom'][2][0]=4.94
    check(a,b)


def test_reusable_matcher_matches_original_on_repeated_queries(refs):
    fast=PreparedReferenceMatcher(refs)
    for x in (0,0.1,2.,18.15,30.,35.,100.):
        payload={'geometry':geom(x)}
        original=match(payload,refs)
        new=fast.match(payload)
        assert original['state']==new['state']
        assert original['incompatibilities']==new['incompatibilities']
        assert [m['reference_id'] for m in original['matches']]==[m['reference_id'] for m in new['matches']]
        assert [m['identity_id'] for m in original['matches']]==[m['identity_id'] for m in new['matches']]
        assert np.allclose([m['maxdev_angstrom'] for m in original['matches']],
                           [m['maxdev_angstrom'] for m in new['matches']],atol=1e-12,rtol=0)
