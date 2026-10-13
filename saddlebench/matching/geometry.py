"""Exact core-drift maxdev in angstrom, with closest-vector MIC for periodic cell."""
from __future__ import annotations
import math
import numpy as np
from saddlebench.reference.snapshot import validate_geometry

def _mic(displacements,cell,pbc):
    """Euclidean closest lattice vector via bounded QR sphere decoding (3D)."""
    arr=np.asarray(displacements,dtype=float)
    periodic=np.asarray(pbc,dtype=bool)
    if not periodic.any():return arr.copy()
    basis=np.asarray(cell,dtype=float)[periodic].T
    # Orthogonal periodic cells admit an exact component-wise nearest-lattice
    # calculation.  Avoid per-atom QR sphere decoding for this common case.
    # Nonorthogonal, degenerate, near-half-cell, and numerically extreme inputs
    # still use the original bounded sphere decoder below.
    gram=basis.T@basis
    lensq=np.diag(gram)
    offdiag=gram-np.diag(lensq)
    if (np.all(lensq>1e-20) and
            np.all(np.abs(offdiag)<=1e-14*np.sqrt(np.outer(lensq,lensq)))):
        fractional=(arr@basis)/lensq
        nearest=np.rint(fractional)
        # On a Voronoi boundary, the legacy decoder's tie resolution is kept.
        if (np.all(np.abs(fractional)<1e6) and
                not np.any(np.abs(np.abs(fractional-nearest)-0.5)<1e-9)):
            return arr-nearest@basis.T
    q,r=np.linalg.qr(basis,mode='reduced')
    if len(periodic[periodic]) and any(abs(r[i,i])<1e-11 for i in range(r.shape[0])):
        raise ValueError('DEGENERATE_PERIODIC_CELL')
    coords=arr@q
    result=[]
    m=basis.shape[1]
    for d,y in zip(arr,coords):
        # Babai nearest-plane gives a finite initial sphere bound.
        guess=np.zeros(m,dtype=int)
        for i in reversed(range(m)):
            guess[i]=int(np.rint((y[i]-sum(r[i,j]*guess[j] for j in range(i+1,m)))/r[i,i]))
        best=guess.copy()
        best_sq=float(np.dot(y-r@guess,y-r@guess)) + 1e-12
        cur=np.zeros(m,dtype=int)
        def walk(i,partial):
            nonlocal best,best_sq
            if i<0:
                if partial < best_sq:
                    best_sq=partial
                    best=cur.copy()
                return
            residual=y[i]-sum(r[i,j]*cur[j] for j in range(i+1,m))
            room=max(0.0,best_sq-partial)
            center=residual/r[i,i]
            radius=math.sqrt(room)/abs(r[i,i])
            lo=math.ceil(center-radius-1e-10);hi=math.floor(center+radius+1e-10)
            for v in sorted(range(lo,hi+1),key=lambda n:abs(n-center)):
                cur[i]=v
                delta=residual-r[i,i]*v
                newpartial=partial+delta*delta
                if newpartial<=best_sq+1e-12:walk(i-1,newpartial)
        walk(m-1,0.)
        result.append(d-basis@best)
    return np.asarray(result)

def core_drift_maxdev(a,b):
    """Canonical: MIC -> <= median magnitude core -> subtract core mean -> MIC -> max norm.

    Fixed-atom constraints are recorded as metadata but do not enter geometric identity.
    Atom ordering, periodicity and cell compatibility remain mandatory.
    """
    validate_geometry(a);validate_geometry(b)
    if a['numbers']!=b['numbers']:raise ValueError('INCOMPATIBLE_ATOM_ORDER')
    if a['pbc']!=b['pbc']:raise ValueError('INCOMPATIBLE_PBC')
    ca=np.asarray(a['cell_angstrom'],dtype=float)
    cb=np.asarray(b['cell_angstrom'],dtype=float)
    if np.max(np.abs(ca-cb))>0.05:raise ValueError('INCOMPATIBLE_CELL')
    displacement=np.asarray(b['positions_angstrom'],dtype=float)-np.asarray(a['positions_angstrom'],dtype=float)
    mic=_mic(displacement,ca,a['pbc'])
    magnitudes=np.linalg.norm(mic,axis=1)
    core=mic[magnitudes<=np.median(magnitudes)]
    corrected=_mic(mic-np.mean(core,axis=0),ca,a['pbc'])
    return float(np.max(np.linalg.norm(corrected,axis=1)))

def complete_link_diameter(geometries):
    """Conservative all-pairs diameter; never infer equivalence from transitive links."""
    return max((core_drift_maxdev(a,b) for i,a in enumerate(geometries) for b in geometries[i+1:]),default=0.0)
