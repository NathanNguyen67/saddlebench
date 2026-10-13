"""Exact core-drift maxdev in angstrom, with closest-vector MIC for periodic cell."""
from __future__ import annotations
import math
from functools import lru_cache
from itertools import product
import numpy as np
from saddlebench.reference.snapshot import validate_geometry

@lru_cache(maxsize=12)
def _image_shifts(dim, radius):
    """Deterministic relative periodic images; immutable cached index grid."""
    values=np.asarray(list(product(range(-radius,radius+1),repeat=dim)),dtype=float)
    values.setflags(write=False)
    return values


def _certified_skew_mic(arr,basis):
    """Exact-CVP candidate vectors when a Euclidean lower bound certifies them.

    Return None to invoke the unchanged QR sphere decoder for nearly singular,
    extreme, boundary/tied or impractically wide lattice searches.
    """
    if not len(arr):return arr.copy()
    if arr.ndim!=2 or arr.shape[1]!=3 or not np.isfinite(arr).all():return None
    try:
        singular=np.linalg.svd(basis,compute_uv=False)
        sigma=float(singular[-1])
        scale=float(singular[0])
        if (not np.isfinite(sigma) or sigma<=1e-10*max(scale,1.0)
                or scale/sigma>20.):
            return None
        gram=basis.T@basis
        fractional=np.linalg.solve(gram,(arr@basis).T).T
        if not np.isfinite(fractional).all() or np.max(np.abs(fractional))>=1e6:
            return None
        center=np.rint(fractional)
        residual=arr-center@basis.T
        # radius 1 is the 3x3x3 neighborhood. Expand only when the
        # singular-value lower bound cannot certify all nearest images.
        for radius in (1,2,3):
            shifts=_image_shifts(basis.shape[1],radius)
            if len(arr)*len(shifts)>150000:return None
            images=shifts@basis.T
            differences=residual[:,None,:]-images[None,:,:]
            norms_sq=np.einsum('ijk,ijk->ij',differences,differences)
            best_index=np.argmin(norms_sq,axis=1)
            best_sq=norms_sq[np.arange(len(arr)),best_index]
            if not np.isfinite(best_sq).all():return None
            lower=sigma*(radius+0.5)
            # A lattice translate outside [-radius,radius]^m must be at
            # least sigma*(radius+0.5) away from the fractional point.
            # Keep generous numerical guards around the certification.
            epsilon=1e-9*(1.+scale+np.max(np.abs(arr)))
            if not np.all(np.sqrt(best_sq)+epsilon < lower):
                continue
            # Original decoder's tie-breaking remains authoritative.
            two_smallest=np.partition(norms_sq,1,axis=1)[:,:2]
            if np.any(two_smallest[:,1]-two_smallest[:,0] <
                      1e-10*(1.+two_smallest[:,0])):
                return None
            return differences[np.arange(len(arr)),best_index]
    except (np.linalg.LinAlgError,ValueError,FloatingPointError):
        pass
    return None


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
    # Certified vectorized enumeration also works for skewed periodic cells.
    # Strict singular-value bounding proves no unexamined periodic image wins;
    # the legacy exact QR solver below handles every uncertified case.
    if not np.all(np.abs(offdiag)<=1e-14*np.sqrt(np.outer(lensq,lensq))):
        accelerated=_certified_skew_mic(arr,basis)
        if accelerated is not None:return accelerated
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


def cannot_match_by_anchor(a,b,maxdev):
    """Certified necessary condition for a core-drift maxdev match.

    If corrected MIC displacements are all <= t from zero, periodic distances
    between *any two* raw displacement vectors must be <= 2*t by the triangle
    inequality. Sampling a few pairs can only reject true nonmatches. Any
    incompatible/invalid geometry is deferred to core_drift_maxdev for its
    existing error reporting. No cell-angle or fractional rounding assumption.
    """
    if (a.get('numbers') != b.get('numbers') or
        a.get('pbc') != b.get('pbc')):
        return False
    pa=np.asarray(a.get('positions_angstrom'),dtype=float)
    pb=np.asarray(b.get('positions_angstrom'),dtype=float)
    ca=np.asarray(a.get('cell_angstrom'),dtype=float)
    cb=np.asarray(b.get('cell_angstrom'),dtype=float)
    if (pa.ndim!=2 or pb.shape!=pa.shape or pa.shape[1]!=3 or
        ca.shape!=(3,3) or cb.shape!=(3,3) or
        pa.shape[0]<2 or not np.isfinite(pa).all() or
        not np.isfinite(pb).all() or not np.isfinite(ca).all() or
        not np.isfinite(cb).all() or np.max(np.abs(ca-cb))>0.05):
        return False
    n=len(pa)
    selected=sorted({0,n//4,n//2,(3*n)//4,n-1})
    displacement=pb[selected]-pa[selected]
    try:
        relative=_mic(displacement-displacement[0],ca,a['pbc'])
    except (ValueError,TypeError,IndexError,np.linalg.LinAlgError):
        return False
    return bool(np.any(np.linalg.norm(relative,axis=1)>2*float(maxdev)+1e-8))
