"""Exact MIC microbatches shared by the public first-hit scorer.

No legacy all-pairs matrix or independent scientific metric lives here.
Numerically uncertain pairs still use the canonical scalar maxdev verifier.
"""
from __future__ import annotations
import numpy as np
from saddlebench.matching import geometry as geom

_STATS = {'mic_calls': 0}


def _geom_key(g):
    try:
        cell=np.asarray(g['cell_angstrom'],dtype=np.float64)
        pos=np.asarray(g['positions_angstrom'],dtype=np.float64)
        n=len(g['numbers'])
        if (cell.shape!=(3,3) or pos.shape!=(n,3) or n<2 or n>1024 or
            not np.isfinite(cell).all() or not np.isfinite(pos).all() or
            np.max(np.abs(pos))>=1e5 or np.max(np.abs(cell))>=1e5):
            return None
        return tuple(g['numbers']),tuple(g['pbc']),tuple(cell.ravel())
    except (ValueError,TypeError,IndexError,KeyError,OverflowError):
        return None


def _mic_in_chunks(displacements, cell, pbc, atom_count, prepared=None):
    """Apply frozen MIC, never crossing its 150000 image-evaluation limit.

    Chunking at whole geometry boundaries avoids splitting any one geometry's
    median selection. MIC itself only needs separate displacement vectors.
    """
    n=len(displacements)
    if not n:return np.empty_like(displacements)
    # Same certified radius-1 budget used in A; preserve sub-4500 memory bound.
    size=max(atom_count, (4500//atom_count)*atom_count)
    result=np.empty_like(displacements)
    for lo in range(0,n,size):
        hi=min(lo+size,n)
        # A prepared query caches the periodic cell basis across both passes
        # and across every microbatch for this query. Skew/boundary cases
        # still use the canonical MIC fallback within _mic_prepared.
        value=(geom._mic_prepared(displacements[lo:hi],prepared)
               if prepared is not None else geom._mic(displacements[lo:hi],cell,pbc))
        if value.shape!=(hi-lo,3):raise ValueError('MIC_SHAPE_CHANGED')
        result[lo:hi]=value
        _STATS['mic_calls']+=1
    return result


def _measure_packed(ids, qa, ra, cell, pbc, *, prepared=None):
    """Return vector of exact candidate distances, with canonical fallback on error."""
    q_ids=np.fromiter((v[0] for v in ids),dtype=np.intp,count=len(ids))
    r_ids=np.fromiter((v[1] for v in ids),dtype=np.intp,count=len(ids))
    n=qa.shape[1]
    if prepared is not None:
        if not isinstance(prepared,geom.PreparedGeometry):
            raise TypeError('PREPARED_GEOMETRY_REQUIRED')
        if tuple(pbc)!=prepared.pbc or not np.array_equal(cell,prepared.cell):
            raise ValueError('PREPARED_CELL_MISMATCH')
    delta=ra[r_ids]-qa[q_ids]
    shape=delta.shape
    mic=_mic_in_chunks(delta.reshape(-1,3),cell,pbc,n,prepared=prepared).reshape(shape)
    magnitudes=np.linalg.norm(mic,axis=2)
    core=magnitudes<=np.median(magnitudes,axis=1)[:,None]
    denom=np.sum(core,axis=1)
    if np.any(denom==0):raise ValueError('EMPTY_CORE')
    drift=np.sum(np.where(core[:,:,None],mic,0.),axis=1)/denom[:,None]
    fixed=_mic_in_chunks((mic-drift[:,None,:]).reshape(-1,3),cell,pbc,n,prepared=prepared).reshape(shape)
    return np.max(np.linalg.norm(fixed,axis=2),axis=1)
