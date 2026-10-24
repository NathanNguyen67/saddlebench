"""Frozen-skew equality and preparation invariants for the batch-16 scorer."""
import numpy as np
import pytest
from saddlebench.matching import geometry as g
from saddlebench.matching import batch_exact


CELLS = (
    np.array([[10., 0, 0], [3., 10., 0], [0., 0., 12.]]),
    np.array([[10., 0, 0], [7.98, .4, 0], [1., 0., 13.]]),
    np.array([[8., .6, 0], [2., 9., 0], [0., 1., 11.]]),
)


def geo(cell, pbc, pos):
    return dict(numbers=[6]*len(pos), pbc=list(map(bool, pbc)),
                positions_angstrom=np.asarray(pos).tolist(),
                cell_angstrom=np.asarray(cell).tolist(),
                fixed_indices=[], constraints_known=False)


@pytest.mark.parametrize('cell', CELLS)
@pytest.mark.parametrize('pbc', [(1,1,1),(1,1,0),(1,0,1),(0,1,1)])
def test_skew_mic_random_translations_and_ref_snapshots(cell, pbc):
    rng=np.random.default_rng(188)
    pos=rng.uniform(-13, 13, (49,3))
    original=geo(cell,pbc,pos)
    prep=g.prepare_geometry(original)
    if prep.skew_mic is None:
        assert prep.orthogonal_basis is not None
    else:
        assert not prep.skew_mic.basis.flags.writeable
        assert not prep.skew_mic.q.flags.writeable
        assert not prep.skew_mic.r.flags.writeable
        if prep.skew_mic.projection is not None:
            assert not prep.skew_mic.projection.flags.writeable
            assert all(not img.flags.writeable for img in prep.skew_mic.image_offsets)
    for _ in range(8):
        displacements=rng.uniform(-40.,40.,(64,3))
        shifts=rng.integers(-4,5,(len(displacements),sum(pbc)))
        displacements += shifts @ cell[np.asarray(pbc,dtype=bool)]
        a=g._mic(displacements,cell,pbc)
        b=g._mic_prepared(displacements,prep)
        np.testing.assert_allclose(a,b,atol=1e-8,rtol=1e-10)
    original['cell_angstrom'][1][0] += 100
    original['positions_angstrom'][0][0] += 200
    assert not np.array_equal(prep.cell,np.asarray(original['cell_angstrom']))


def test_boundaries_and_certification_fallback():
    cell=CELLS[0]
    prep=g.prepare_geometry(geo(cell,[True]*3,np.zeros((2,3))))
    d=np.array([[5.,0.,0.], [-5.,0.,0.], [1.5,5.,0.],
                [0.,0.,6.], [6.5,5.,0.], [1000001.,0.,0.]])
    np.testing.assert_allclose(g._mic(d,cell,[True]*3),
                               g._mic_prepared(d,prep),atol=1e-9,rtol=1e-12)
    near=np.array([[10.,0.,0.],[10.,1e-12,0.],[0.,0.,12.]])
    nearprep=g.prepare_geometry(geo(near,[1,1,1],np.zeros((2,3))))
    assert nearprep.skew_mic is not None
    assert nearprep.skew_mic.projection is None
    with pytest.raises(ValueError,match='DEGENERATE_PERIODIC_CELL'):
        g._mic_prepared(d[:1],nearprep)
    with pytest.raises(ValueError,match='DEGENERATE_PERIODIC_CELL'):
        g._mic(d[:1],near,[1,1,1])


def test_real_batch16_if_fixture_present():
    import os
    from pathlib import Path
    fixture_root = os.environ.get('SADDLEBENCH_BENCH_FIXTURE_DIR')
    if fixture_root is None:
        pytest.skip('Real fixture directory not supplied')
    for case in ('case0006','case0022'):
        f=Path(fixture_root) / (case+'.npz')
        assert f.is_file(), f'MISSING_REAL_FIXTURE={f}'
        arr=np.load(f,allow_pickle=False)
        qa=arr['qa']; ra=arr['ra']; cell=arr['cell']; pbc=arr['pbc']
        ids=[tuple(int(x) for x in pair) for pair in arr['ids']]
        prep=g.prepare_geometry(geo(cell,pbc,qa[0]))
        assert prep.skew_mic is not None
        old=batch_exact._measure_packed(ids,qa,ra,cell,pbc)
        new=batch_exact._measure_packed(ids,qa,ra,cell,pbc,prepared=prep)
        np.testing.assert_allclose(old,new,atol=1e-10,rtol=1e-12)


def test_skew_prepared_reuses_svd_qr_and_projection_with_boundary_fallback():
    from unittest.mock import patch
    cell=CELLS[0]
    prepared=g.prepare_geometry(geo(cell,[1,1,1],np.zeros((8,3))))
    assert prepared.skew_mic is not None
    arr=np.array([[.5, .3, .2], [3., 7., 1.], [5., 0., 0.],
                  [0., 0., 6.], [-5., 0., 0.]])
    canonical=g._mic(arr,cell,[1,1,1])
    qr=g._qr_sphere_mic
    with (patch.object(np.linalg,'svd',side_effect=AssertionError('REPEATED_SVD')),
          patch.object(np.linalg,'qr',side_effect=AssertionError('REPEATED_QR')),
          patch.object(np.linalg,'solve',side_effect=AssertionError('REPEATED_SOLVE')),
          patch.object(g,'_qr_sphere_mic',wraps=qr) as qr_calls):
        got=g._mic_prepared(arr,prepared)
    np.testing.assert_allclose(got,canonical,atol=1e-10,rtol=1e-12)
    assert qr_calls.call_count > 0, 'Boundary must invoke cached original QR decoder'
