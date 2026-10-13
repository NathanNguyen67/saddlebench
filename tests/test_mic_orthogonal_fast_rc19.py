"""Fast orthogonal MIC must retain the legacy exact MIC behavior."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
from saddlebench.matching.geometry import _mic, core_drift_maxdev


@pytest.fixture(scope='module')
def legacy_mic():
    # Keep a frozen copy of the original exact algorithm in this regression.
    src=Path(__file__).resolve().parent/'fixtures'/'legacy_mic_rc17.py'
    spec=importlib.util.spec_from_file_location('_legacy_geometry_rc17',src)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._mic


@pytest.mark.parametrize('periodic',[[True,False,False],[True,True,False],
                                    [True,True,True],[False,False,False]])
def test_random_orthogonal_mic_agreement(legacy_mic,periodic):
    rng=np.random.default_rng(90210)
    for _ in range(10):
        sizes=rng.uniform(3,15,3)
        basis=np.diag(sizes)
        # Test rotated orthogonal cells too.
        q,_=np.linalg.qr(rng.normal(size=(3,3)))
        cell=basis@q
        d=rng.normal(scale=7,size=(12,3))
        expected=legacy_mic(d,cell,periodic)
        got=_mic(d,cell,periodic)
        np.testing.assert_allclose(got,expected,rtol=0,atol=1e-10)


def test_tie_boundary_uses_frozen_algorithm(legacy_mic):
    cell=np.diag([10.,12.,14.])
    for dx in [5.,-5.,15.,-15.,5.+1e-11,5.-1e-11]:
        d=np.array([[dx,1.,2.],[1.,dx,2.]])
        np.testing.assert_allclose(_mic(d,cell,[True,True,True]),
                                   legacy_mic(d,cell,[True,True,True]),atol=1e-10)


def test_skew_cell_uses_original_decoder(legacy_mic):
    cell=np.array([[2.,0,0],[1.99,0.2,0],[0,0,8]])
    displacement=np.array([[3.99,0.2,0],[2.03,-0.31,0]])
    np.testing.assert_allclose(_mic(displacement,cell,[True,True,False]),
                               legacy_mic(displacement,cell,[True,True,False]),atol=1e-10)


def test_maxdev_unchanged_at_threshold(legacy_mic):
    a={'numbers':[6,1,1], 'positions_angstrom':[[0.,0.,0.],[1.,0.,0.],[2.,0.,0.]], 'pbc':[False,False,False], 'cell_angstrom':np.diag([10.,10.,10.]).tolist(), 'fixed_indices':[], 'constraints_known':True}
    for dx in [0.,0.199999,0.2,0.200001,2.0]:
        b={**a, 'positions_angstrom':[[0.,0.,0.],[1.,0.,0.],[2.+dx,0.,0.]]}
        assert core_drift_maxdev(a,b)==pytest.approx(dx,abs=2e-12)
