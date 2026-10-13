"""Independent frozen exact decoder comparisons for certified skewed MIC."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
from saddlebench.matching import geometry as g


@pytest.fixture(scope='module')
def old():
    path=Path(__file__).parent/'fixtures'/'legacy_mic_rc17.py'
    spec=importlib.util.spec_from_file_location('frozen_old_rc17',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod._mic


@pytest.mark.parametrize('pbc',[[1,1,1],[1,1,0],[1,0,1],[0,1,1]])
@pytest.mark.parametrize('cell',[np.array([[10.,0.,0.],[5.,8.660254,0.],[1.2,2.,9.]]),
                                 np.array([[8.,0,0.],[7.98,.4,0.],[2.,1.,12.]]),
                                 np.array([[13.,0.,0.],[-6.5,11.258,0.],[0.2,4.1,12.]]),
                                 np.array([[7.,0.,0.],[1.5,9.,0.],[2.8,1.7,13.]])])
def test_skew_random_and_translated_equivalent(old,cell,pbc):
    rng=np.random.default_rng(9102)
    for _ in range(7):
        d=rng.uniform(-50,50,(16,3))
        got=g._mic(d,cell,pbc)
        ref=old(d,cell,pbc)
        np.testing.assert_allclose(got,ref,atol=1e-8,rtol=1e-9)
        v=np.asarray(pbc,dtype=bool)
        shifts=rng.integers(-5,6,(16,sum(v)))
        translated=d+shifts@cell[v]
        np.testing.assert_allclose(g._mic(translated,cell,pbc),ref,atol=1e-8,rtol=1e-9)


def test_skew_rounding_wrong_but_certified_answer_correct(old):
    cell=np.array([[10.,0,0],[5.,8.660254,0],[0,0,10]])
    d=np.array([[7.35,4.24352446,0]])  # f=(.49,.49,0)
    frac=np.array([[.49,.49,0]])
    wrong=d-np.rint(frac)@cell
    assert np.linalg.norm(wrong)>np.linalg.norm(d-cell[0])
    np.testing.assert_allclose(g._mic(d,cell,[True]*3),old(d,cell,[True]*3),atol=1e-10)


def test_certified_branch_really_runs_and_fallback_exists(old):
    cell=np.array([[10.,0,0],[2.,9.5,0],[0.,0.,12.]])
    basis=cell.T
    rng=np.random.default_rng(3)
    samples=rng.normal(size=(20,3))
    accelerated=g._certified_skew_mic(samples,basis)
    assert accelerated is not None
    np.testing.assert_allclose(accelerated,old(samples,cell,[True]*3),atol=1e-9)
    # Very ill-conditioned skew cannot safely pass the shortcut.
    near=np.array([[10.,0,0],[10.,1e-10,0],[0,0,10.]])
    assert g._certified_skew_mic(samples,near.T) is None


def test_boundary_cases_defer_to_legacy(old):
    cell=np.array([[10.,0,0],[3.,10.,0],[0,0,10.]])
    d=np.array([[5.,0,0],[-5.,0,0],[3.,5.,0],[0.,0.,5.]])
    np.testing.assert_allclose(g._mic(d,cell,[1,1,1]),old(d,cell,[1,1,1]),atol=1e-10)


def test_full_core_drift_identical_to_legacy(old):
    rng=np.random.default_rng(7)
    cell=np.array([[9.,0,0],[4.5,7.7942286,0],[0,0,18.]])
    geometry=lambda arr:{'numbers':[6]*len(arr),'positions_angstrom':arr.tolist(),
       'cell_angstrom':cell.tolist(),'pbc':[True,True,True],
       'constraints_known':False,'fixed_indices':[]}
    a=rng.uniform(-5,5,(28,3))
    for scale in (.01,.15,.3,1.0):
        b=a+rng.normal(size=a.shape)*scale
        new=g.core_drift_maxdev(geometry(a),geometry(b))
        with patch.object(g,'_mic',old):
            expected=g.core_drift_maxdev(geometry(a),geometry(b))
        assert new==pytest.approx(expected,rel=1e-9,abs=1e-9)
