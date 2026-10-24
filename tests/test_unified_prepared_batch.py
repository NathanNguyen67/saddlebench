"""Public first-hit uses prepared MIC and preserves canonical classifications."""
from __future__ import annotations
import copy
import numpy as np
import pytest
from saddlebench.matching import batch_exact, geometry
from saddlebench.matching.group_radius_exists import GroupCatalog, build_index


def geo(positions, *, skew=0., pbc=(True, True, True)):
    return {'numbers':[78]*len(positions),'positions_angstrom':np.asarray(positions).tolist(),
            'pbc':[bool(x) for x in pbc],'cell_angstrom':[[11.,0.,0.],[skew,12.,0.],[0.,0.,13.]],
            'constraints_known':False,'fixed_indices':[]}


@pytest.mark.parametrize('skew,pbc',[(0.,(0,0,0)),(0.,(1,1,1)),(2.8,(1,1,1)),(7.5,(1,1,0))])
def test_batch_exact_matches_frozen_and_prepared_scalar(skew,pbc):
    rng=np.random.default_rng(23014)
    base=rng.uniform(0,10,(21,3))
    query=geo(base,skew=skew,pbc=pbc)
    refs=[]
    for d in (0.,.01,.0499,.1,.1999999999,.2000000001,.5,2.7):
        g=geo(base+rng.normal(0,.01,(21,3)),skew=skew,pbc=pbc)
        g['positions_angstrom'][20][2]+=d
        refs.append(g)
    pa=geometry.prepare_geometry(query)
    qa=np.asarray([query['positions_angstrom']],float)
    ra=np.asarray([r['positions_angstrom'] for r in refs],float)
    ids=[(0,i) for i in range(len(refs))]
    old=batch_exact._measure_packed(ids,qa,ra,pa.cell,pa.pbc)
    new=batch_exact._measure_packed(ids,qa,ra,pa.cell,pa.pbc,prepared=pa)
    np.testing.assert_allclose(new,old,atol=1e-11,rtol=0)
    for i,ref in enumerate(refs):
        scalar=geometry.core_drift_maxdev(query,ref)
        assert abs(scalar-new[i])<=1e-11
        assert abs(geometry.core_drift_maxdev_prepared(pa,geometry.prepare_geometry(ref))-scalar)<=1e-11


def test_group_catalog_invokes_prepared_batch(monkeypatch):
    rng=np.random.default_rng(75)
    base=rng.uniform(0,8,(31,3))
    refs=[]
    for i in range(19):
        pos=base+rng.normal(scale=.006,size=(31,3))
        refs.append((None,dict(reaction_id=f'r{i}',saddle_group_id='common',geometry=geo(pos))))
    cat=GroupCatalog(refs,build_index(refs))
    observed=[]
    real=batch_exact._measure_packed
    def monitor(*args,**kwargs):
        observed.append(kwargs.get('prepared'))
        return real(*args,**kwargs)
    monkeypatch.setattr('saddlebench.matching.group_radius_exists._measure_packed',monitor)
    query=geo(base+np.array([0.,0.,.02]))
    cat.find_first(query,batch_size=8,order='original')
    assert observed and all(isinstance(x,geometry.PreparedGeometry) for x in observed)


def test_group_catalog_can_use_warm_reference_preparation(monkeypatch):
    base=np.arange(45.,dtype=float).reshape(-1,3)/7
    refs=[(None,dict(reaction_id=f'r{i}',saddle_group_id=f'g{i}',geometry=geo(base+np.array([0,0,i*.3])))) for i in range(3)]
    c=GroupCatalog(refs,build_index(refs))
    query=geo(base+np.array([0,0,.02]))
    v=c.find_first(query,batch_size=1)
    assert v.get('found')
    assert len(c._prepared_refs)>0
    ids={i:id(g) for i,g in c._prepared_refs.items()}
    c.find_first(query,batch_size=1)
    assert {i:id(g) for i,g in c._prepared_refs.items()}==ids


def test_incompatible_prepared_cell_cannot_skip_verification():
    base=np.arange(15.).reshape(5,3)
    q=geo(base);p=geometry.prepare_geometry(q)
    q2=copy.deepcopy(q);q2['cell_angstrom'][0][0]=12.
    with pytest.raises(ValueError,match='PREPARED_CELL_MISMATCH'):
        batch_exact._measure_packed([(0,0)],np.array([base]),np.array([base]),np.asarray(q2['cell_angstrom']),q2['pbc'],prepared=p)
