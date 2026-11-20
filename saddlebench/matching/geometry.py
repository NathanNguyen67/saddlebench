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


def _certified_skew_mic(arr,basis,*,prepared=None):
    """Exact-CVP enumeration with optional immutable skew-cell preparation.

    The original certified radius, numerical guards, and tie handling apply
    identically with or without cell-dependent factors supplied in advance.
    Return None when the unchanged QR sphere decoder must decide.
    """
    if not len(arr):return arr.copy()
    if arr.ndim!=2 or arr.shape[1]!=3 or not np.isfinite(arr).all():return None
    try:
        if prepared is None:
            singular=np.linalg.svd(basis,compute_uv=False)
            sigma=float(singular[-1])
            scale=float(singular[0])
            if (not np.isfinite(sigma) or sigma<=1e-10*max(scale,1.0)
                    or scale/sigma>20.):
                return None
            gram=basis.T@basis
            fractional=np.linalg.solve(gram,(arr@basis).T).T
        else:
            if prepared.projection is None:return None
            sigma,scale=prepared.sigma,prepared.scale
            fractional=arr@prepared.projection
        if not np.isfinite(fractional).all() or np.max(np.abs(fractional))>=1e6:
            return None
        center=np.rint(fractional)
        residual=arr-center@basis.T
        epsilon=1e-9*(1.+scale+np.max(np.abs(arr)))
        squared=np.einsum('ij,ij->i',residual,residual)
        norm=np.sqrt(squared)
        # Every nonzero lattice shift has length >= sigma. A residual inside
        # half that bound is closer to the rounded image than any alternative.
        certain=((norm+epsilon < 0.5*sigma)
                 & (sigma*(sigma-2*norm) > 1e-10*(1.+squared)))
        if np.all(certain):
            return residual
        unresolved=residual[~certain] if np.any(certain) else residual
        uncertain_count=len(unresolved)
        for radius in (1,2,3):
            shifts=_image_shifts(basis.shape[1],radius)
            if uncertain_count*len(shifts)>150000:return None
            images=(shifts@basis.T if prepared is None
                    else prepared.image_offsets[radius-1])
            differences=unresolved[:,None,:]-images[None,:,:]
            norms_sq=np.einsum('ijk,ijk->ij',differences,differences)
            best_index=np.argmin(norms_sq,axis=1)
            rows=np.arange(uncertain_count)
            best_sq=norms_sq[rows,best_index]
            if not np.isfinite(best_sq).all():return None
            lower=sigma*(radius+0.5)
            if not np.all(np.sqrt(best_sq)+epsilon < lower):
                continue
            two_smallest=np.partition(norms_sq,1,axis=1)[:,:2]
            if np.any(two_smallest[:,1]-two_smallest[:,0] <
                      1e-10*(1.+two_smallest[:,0])):
                return None
            residual[~certain]=differences[rows,best_index]
            return residual
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
    return _qr_sphere_mic(arr,basis,q,r)


def _qr_sphere_mic(arr,basis,q,r):
    """The original exact QR sphere decoder, also used with cached Q/R.

    Preserve enumeration order, Babai bound, numerical tolerances and ties.
    """
    if any(abs(r[i,i])<1e-11 for i in range(r.shape[0])):
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


# Prepared positions are owned by each caller. Only immutable cell factors
# are shared across geometries. The public scalar path retains validation.
from dataclasses import dataclass


@dataclass(frozen=True, eq=False)
class PreparedSkewMIC:
    """Immutable cell-dependent factors for the unchanged skew MIC solver."""
    basis: np.ndarray
    q: np.ndarray
    r: np.ndarray
    projection: np.ndarray | None
    sigma: float | None
    scale: float | None
    image_offsets: tuple


@dataclass(frozen=True, eq=False)
class PreparedGeometry:
    numbers: tuple
    pbc: tuple
    positions: np.ndarray
    cell: np.ndarray
    orthogonal_basis: np.ndarray | None
    orthogonal_lensq: np.ndarray | None
    skew_mic: PreparedSkewMIC | None = None


def _readonly(arr):
    arr = np.array(arr, dtype=float, copy=True)
    arr.setflags(write=False)
    return arr


def _prepare_skew_mic(basis):
    """Precompute only cell-dependent work; leave exact image choices dynamic.

    Failure to prepare merely preserves the existing unprepared MIC fallback.
    In particular, degenerate cells are still rejected at MIC evaluation time.
    """
    try:
        q, r = np.linalg.qr(basis, mode='reduced')
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return None
    projection = None
    sigma = scale = None
    images = ()
    try:
        singular = np.linalg.svd(basis, compute_uv=False)
        candidate_sigma = float(singular[-1])
        candidate_scale = float(singular[0])
        if (np.isfinite(candidate_sigma) and
                candidate_sigma > 1e-10*max(candidate_scale, 1.0) and
                candidate_scale/candidate_sigma <= 20.):
            gram = basis.T @ basis
            # The certified fast solver still guards extremes, boundaries,
            # near-degenerate cells, and image ties before accepting a result.
            # Solve the constant projection ONCE for repeated query batches.
            projection = np.linalg.solve(gram, basis.T).T
            if np.isfinite(projection).all():
                images = tuple(_readonly(_image_shifts(basis.shape[1], k) @ basis.T)
                               for k in (1, 2, 3))
                sigma, scale = candidate_sigma, candidate_scale
            else:
                projection = None
    except (ValueError, np.linalg.LinAlgError, FloatingPointError, OverflowError):
        projection = None
        images = ()
    return PreparedSkewMIC(_readonly(basis), _readonly(q), _readonly(r),
                           None if projection is None else _readonly(projection),
                           sigma, scale, images)


@lru_cache(maxsize=128)
def _prepare_cell(values, pbc):
    cell = np.asarray(values).reshape(3, 3)
    if not any(pbc):
        return None, None, None
    basis = cell[np.asarray(pbc, dtype=bool)].T
    gram = basis.T @ basis
    diag = np.diag(gram)
    offdiag = gram - np.diag(diag)
    if (np.all(diag > 1e-20) and
            np.all(np.abs(offdiag) <= 1e-14*np.sqrt(np.outer(diag, diag)))):
        return _readonly(basis), _readonly(diag), None
    return None, None, _prepare_skew_mic(basis)


def prepare_geometry(g):
    """Validate once and snapshot read-only geometry and MIC cell factors."""
    validate_geometry(g)
    return _prepare_arrays(g)


def prepare_array_geometry(g):
    """Prepare geometry constructed from ASE's typed arrays."""
    if not isinstance(g['positions_angstrom'], np.ndarray):
        return prepare_geometry(g)
    pos = g['positions_angstrom']
    cell = g['cell_angstrom']
    numbers = g['numbers']
    if (not numbers or any(not 1 <= z <= 118 for z in numbers)
            or pos.shape != (len(numbers), 3) or not np.isfinite(pos).all()
            or cell.shape != (3, 3) or not np.isfinite(cell).all()):
        raise ValueError('INVALID_ASE_GEOMETRY')
    return _prepare_arrays(g)


def _prepare_arrays(g):
    cell = _readonly(g['cell_angstrom'])
    pos = _readonly(g['positions_angstrom'])
    pbc = tuple(g['pbc'])
    orth_basis, orth_lensq, skew = _prepare_cell(tuple(cell.flat), pbc)
    return PreparedGeometry(tuple(g['numbers']), pbc, pos, cell,
                            orth_basis, orth_lensq, skew)




def _mic_prepared(disp, g):
    """Reuse orthogonal or skew cell factors; retain the exact QR fallback."""
    arr = np.asarray(disp, dtype=float)
    if not any(g.pbc):
        return arr.copy()
    if g.orthogonal_basis is not None:
        basis = g.orthogonal_basis
        fractional = (arr @ basis) / g.orthogonal_lensq
        nearest = np.rint(fractional)
        if (np.all(np.abs(fractional) < 1e6) and
                not np.any(np.abs(np.abs(fractional-nearest)-0.5) < 1e-9)):
            return arr - nearest @ basis.T
    if g.skew_mic is not None:
        context = g.skew_mic
        result = _certified_skew_mic(arr, context.basis, prepared=context)
        if result is not None:
            return result
        # Same QR sphere decoder as unprepared MIC, now with cached Q/R.
        return _qr_sphere_mic(arr, context.basis, context.q, context.r)
    # Orthogonal ties and skew prep failures invoke the frozen original MIC.
    return _mic(arr, g.cell, g.pbc)


def core_drift_maxdev_prepared(a, b):
    """Canonical maxdev for explicit prepared inputs; same scientific kernel.

    The first geometry's cell is used for MIC, exactly as in
    core_drift_maxdev. Every call still checks atom ordering/PBC/cell.
    """
    if not isinstance(a, PreparedGeometry) or not isinstance(b, PreparedGeometry):
        raise TypeError('PREPARED_GEOMETRY_REQUIRED')
    if a.numbers != b.numbers:
        raise ValueError('INCOMPATIBLE_ATOM_ORDER')
    if a.pbc != b.pbc:
        raise ValueError('INCOMPATIBLE_PBC')
    if np.max(np.abs(a.cell-b.cell)) > 0.05:
        raise ValueError('INCOMPATIBLE_CELL')
    disp = b.positions-a.positions
    mic = _mic_prepared(disp, a)
    magnitudes = np.linalg.norm(mic, axis=1)
    core = mic[magnitudes <= np.median(magnitudes)]
    corrected = _mic_prepared(mic-np.mean(core, axis=0), a)
    return float(np.max(np.linalg.norm(corrected, axis=1)))

def complete_link_diameter(geometries):
    """Conservative all-pairs diameter; never infer equivalence from transitive links."""
    return max((core_drift_maxdev(a,b) for i,a in enumerate(geometries) for b in geometries[i+1:]),default=0.0)


def _anchor_indices(n):
    return sorted({0, n//4, n//2, (3*n)//4, n-1})


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
    selected=_anchor_indices(n)
    displacement=pb[selected]-pa[selected]
    try:
        relative=_mic(displacement-displacement[0],ca,a['pbc'])
    except (ValueError,TypeError,IndexError,np.linalg.LinAlgError):
        return False
    return bool(np.any(np.linalg.norm(relative,axis=1)>2*float(maxdev)+1e-8))


@lru_cache(maxsize=128)
def _screen_cell(values, pbc):
    orthogonal, _, skew = _prepare_cell(values, pbc)
    if skew is not None:
        return skew.basis, skew.sigma, skew.projection
    if orthogonal is None:
        return None, None, None
    singular = np.linalg.svd(orthogonal, compute_uv=False)
    sigma, scale = float(singular[-1]), float(singular[0])
    if not np.isfinite(sigma) or sigma <= 1e-10*max(1., scale) or scale/sigma > 20.:
        return orthogonal, None, None
    projection = np.linalg.solve(orthogonal.T @ orthogonal, orthogonal.T).T
    return orthogonal, sigma, _readonly(projection)


class BatchedAnchorFilter:
    """Conservative, bounded vectorized anchor rejection for an ordered sequence.

    This is ONLY a necessary-condition filter. A True mask entry is still
    evaluated by the original per-pair filter and canonical maxdev kernel.
    The reference's cell is used for compatibility, but *the query cell* is
    used for MIC, exactly as in cannot_match_by_anchor(a, b, ...).

    Memory: O(reference_count * selected_atoms) persistent and
    O(chunk_size * selected_atoms) scratch.
    No result or reference identity is reordered or collapsed.
    """

    def __init__(self, entries, geometry_of=lambda item: item.get('geometry', item), chunk_size=128, *, all_atoms=False):
        self.all_atoms = all_atoms
        self.size = len(entries)
        self.chunk_size = max(1, int(chunk_size))
        buckets = {}
        for index, entry in enumerate(entries):
            try:
                g = geometry_of(entry)
                numbers = tuple(g['numbers'])
                pbc = tuple(g['pbc'])
                positions = np.asarray(g['positions_angstrom'], dtype=float)
                cell = np.asarray(g['cell_angstrom'], dtype=float)
                n = len(numbers)
                if (len(pbc) != 3 or n < 2 or positions.shape != (n, 3)
                        or cell.shape != (3, 3) or not np.isfinite(positions).all()
                        or not np.isfinite(cell).all()):
                    continue
                selected = self._selected(n)
                key = (numbers, pbc)
                buckets.setdefault(key, []).append((index, positions[selected], cell))
            except (KeyError, TypeError, ValueError, OverflowError, IndexError):
                # Preserve original validation/error behavior for malformed refs.
                continue
        self.buckets = {}
        for key, data in buckets.items():
            positions = np.stack([d[1] for d in data])
            sample = None
            if self.all_atoms and positions.shape[1] > 16 and len(data) > 16:
                relative = positions - positions[:, :1, :]
                spread = np.var(relative, axis=0).sum(axis=1)
                # Retain the original anchors so adding atoms can only reject
                # more candidates. Fill remaining slots with the largest spread.
                original = _anchor_indices(len(spread))
                spread[original] = -np.inf
                sample = np.r_[original, np.argsort(spread)[-(16-len(original)):]]
            self.buckets[key] = (
                np.asarray([d[0] for d in data], dtype=np.intp),
                positions, np.stack([d[2] for d in data]), sample,
            )

    def _selected(self, n):
        return slice(None) if self.all_atoms else _anchor_indices(n)

    def possible(self, query, maxdev):
        """Return ordered True=must-check mask, with no false match omissions."""
        return self.lower_bounds(query, maxdev) <= maxdev

    def lower_bounds(self, query, maxdev):
        """Conservative per-candidate bounds on core-drift maxdev."""
        squared = np.zeros(self.size)
        try:
            numbers = tuple(query['numbers'])
            pbc = tuple(query['pbc'])
            bucket = self.buckets.get((numbers, pbc))
            if bucket is None:
                return squared
            query_pos = np.asarray(query['positions_angstrom'], dtype=float)
            cell = np.asarray(query['cell_angstrom'], dtype=float)
            n = len(numbers)
            if (len(pbc) != 3 or query_pos.shape != (n, 3)
                    or cell.shape != (3, 3) or not np.isfinite(query_pos).all()
                    or not np.isfinite(cell).all()):
                return squared
            anchor = query_pos[self._selected(n)]
            indices, positions, cells, sample = bucket
            basis, sigma, projection = _screen_cell(tuple(cell.flat), pbc)
            if any(pbc) and projection is None:
                return squared
            # Extra guard is intentionally larger than the scalar filter's
            # 1e-8: near-threshold candidates go through the old exact path.
            guard = 1e-7 * (1. + float(maxdev))
            slack = 1e-8 + guard
            cutoff_sq = (2. * float(maxdev) + slack)**2

            def bound_squared(rel):
                rel -= rel[:, :1, :]
                # Skip numerically unsafe vectors individually. An inconclusive
                # added atom cannot undo a rejection proved by another atom.
                if not np.max(np.abs(rel)) < 1e5:
                    rel[~(np.abs(rel) < 1e5).all(axis=2)] = 0.
                # The orthogonal residual and the minimum singular value
                # bound the distance to every periodic image from below.
                if basis is None:
                    lower_sq = np.einsum('ijk,ijk->ij', rel, rel)
                else:
                    flat = rel.reshape(-1, 3)
                    f = flat @ projection
                    normal = flat - f @ basis.T
                    residue = f - np.rint(f)
                    lower_sq = (np.einsum('ij,ij->i', normal, normal)
                                + sigma*sigma*np.einsum('ij,ij->i', residue, residue))
                    lower_sq = lower_sq.reshape(len(rel), -1)
                np.copyto(lower_sq, 0., where=~np.isfinite(lower_sq))
                return lower_sq.max(axis=1)

            for lo in range(0, len(indices), self.chunk_size):
                hi = min(lo + self.chunk_size, len(indices))
                compatible = np.max(np.abs(cells[lo:hi] - cell), axis=(1, 2)) <= 0.05
                rows = lo + np.flatnonzero(compatible)
                if sample is not None and rows.size:
                    coarse = bound_squared(positions[rows[:, None], sample] - anchor[sample])
                    squared[indices[rows]] = coarse
                    rows = rows[coarse <= cutoff_sq]
                if rows.size:
                    keys = indices[rows]
                    squared[keys] = np.maximum(squared[keys], bound_squared(positions[rows] - anchor))
        except (KeyError, TypeError, ValueError, OverflowError, IndexError,
                np.linalg.LinAlgError, FloatingPointError):
            # No partial rejection if numerical certification failed.
            return np.zeros(self.size)
        return np.maximum(0., (np.sqrt(squared) - slack) / 2.)
