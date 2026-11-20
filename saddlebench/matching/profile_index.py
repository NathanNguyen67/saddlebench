"""Range lookup on translation-invariant, continuous atom profiles."""
import numpy as np

from saddlebench.matching.geometry import _anchor_indices

# Allow for cancellation in unwrapped coordinates and roundoff at cutoffs.
SLACK = 1e-6
FIELDS = ("projection", "periods", "weights", "profiles", "features", "keys", "rows")


def _profile_bound(difference, periods, weights, dim, radius):
    difference[..., :dim] -= np.rint(difference[..., :dim] / periods[:dim]) * periods[:dim]
    # The minimum singular value bounds the periodic contribution.
    # The orthonormal normal-space coordinates give the remainder.
    weighted = difference * weights
    squared = np.einsum("ijk,ijk->ij", weighted, weighted).max(axis=1)
    bound = np.maximum(np.sqrt(squared), np.abs(difference).max(axis=(1, 2)))
    # A possible match has a unique wrap along these axes. The span
    # bounds the separation of any two atoms along the same axis.
    unique_wrap = (periods == 0) | (periods > 2 * radius)
    if unique_wrap.any():
        span = np.ptp(difference, axis=1)[:, unique_wrap].max(axis=1)
        bound = np.maximum(bound, span)
    return bound


class ProfileIndex:
    def __init__(self, numbers, pbc, cell, arrays):
        self.numbers = numbers
        self.pbc = pbc
        self.cell = np.asarray(cell)
        self.arrays = arrays
        self.feature_periods = arrays["periods"][arrays["features"] % 3]
        profiles = arrays["profiles"]
        self.short_profiles = np.ascontiguousarray(profiles.reshape(len(profiles), -1)[:, arrays["features"]])
        self.anchors = _anchor_indices(len(numbers))
        self.anchor_profiles = np.ascontiguousarray(profiles[:, self.anchors])

    @classmethod
    def build(cls, geometries):
        if len(geometries) < 16:
            return None
        first = geometries[0]
        numbers, pbc = first["numbers"], first["pbc"]
        cell = np.asarray(first["cell_angstrom"])
        if not numbers or any(g["numbers"] != numbers or g["pbc"] != pbc
               or not np.array_equal(g["cell_angstrom"], cell) for g in geometries):
            return None
        positions = np.asarray([g["positions_angstrom"] for g in geometries])
        if positions.shape != (len(geometries), len(numbers), 3) or not np.max(np.abs(positions)) < 1e5:
            return None
        basis = cell[np.asarray(pbc, dtype=bool)].T
        dim = basis.shape[1]
        periods = np.zeros(3)
        weights = np.ones(3)
        projection = np.eye(3)
        if dim:
            try:
                sigmas = np.linalg.svd(basis, compute_uv=False)
                sigma, scale = sigmas[-1], sigmas[0]
                if not sigma > 1e-10 * max(scale, 1.) or scale / sigma > 20.:
                    return None
                dual = np.linalg.solve(basis.T @ basis, basis.T).T
                normal = np.linalg.qr(basis, mode="complete")[0][:, dim:]
            except np.linalg.LinAlgError:
                return None
            periods[:dim] = 1 / np.linalg.norm(dual, axis=0)
            weights[:dim] = sigma / periods[:dim]
            projection = np.column_stack((dual * periods[:dim], normal))

        profiles = (positions - positions[:, :1]) @ projection
        profiles[..., :dim] %= periods[:dim]
        spread = profiles - profiles[0]
        spread[..., :dim] -= np.rint(spread[..., :dim] / periods[:dim]) * periods[:dim]
        scores = spread.var(axis=0)
        scores[:, (periods > 0) & (periods <= 0.4 * 2 + 2 * SLACK)] = 0
        features = np.flatnonzero(scores.ravel() > 0)
        features = features[np.argsort(scores.ravel()[features])[-8:]]
        if not len(features):
            return None

        values = profiles.reshape(len(profiles), -1)[:, features].T
        order = np.argsort(values, axis=1)
        keys = np.take_along_axis(values, order, axis=1)
        box = periods[features % 3, None]
        keys = np.concatenate((np.where(box > 0, keys - box, -np.inf), keys,
                               np.where(box > 0, keys + box, np.inf)), axis=1)
        rows = np.tile(order, (1, 3)).astype(np.int32)
        arrays = dict(zip(FIELDS, (projection, periods, weights, profiles, features, keys, rows)))
        return cls(numbers, pbc, cell, arrays)

    def candidates(self, prediction, threshold):
        if (prediction["numbers"] != self.numbers or prediction["pbc"] != self.pbc
                or not np.array_equal(prediction["cell_angstrom"], self.cell)):
            return None
        positions = np.asarray(prediction["positions_angstrom"])
        if positions.shape != (len(self.numbers), 3) or not np.max(np.abs(positions)) < 1e5:
            return None

        data = self.arrays
        periods = data["periods"]
        dim = sum(self.pbc)
        profile = (positions - positions[:1]) @ data["projection"]
        profile[:, :dim] %= periods[:dim]
        radius = 2 * threshold + SLACK
        # Each normalized coordinate is 1-Lipschitz for a displacement
        # vector. A true match has at most 2*threshold relative motion.
        closest = None
        subset = profile.ravel()[data["features"]]
        for value, period, keys, rows in zip(subset, self.feature_periods, data["keys"], data["rows"]):
            if 0 < period <= 2 * radius:
                continue
            lo = keys.searchsorted(value - radius, side="left")
            hi = keys.searchsorted(value + radius, side="right")
            if closest is None or hi - lo < len(closest):
                closest = rows[lo:hi]
                if not len(closest):
                    return closest, np.empty(0)
        if closest is None:
            return None
        if len(closest) > 1:
            projected = self.short_profiles[closest] - subset
            periodic = self.feature_periods > 0
            box = self.feature_periods[periodic]
            projected[:, periodic] -= np.rint(projected[:, periodic] / box) * box
            closest = closest[np.max(np.abs(projected), axis=1) <= radius]
            if not len(closest):
                return closest, np.empty(0)

        # The lookup features can miss useful atoms, especially on large
        # slabs. Test the original small atom set before reading all profiles.
        weights = data["weights"]
        if len(closest) > 8 and len(self.anchors) < len(profile):
            probe = self.anchor_profiles[closest] - profile[self.anchors]
            closest = closest[_profile_bound(probe, periods, weights, dim, radius) <= radius]
            if not len(closest):
                return closest, np.empty(0)

        difference = data["profiles"][closest] - profile
        bound = _profile_bound(difference, periods, weights, dim, radius)
        keep = bound <= radius
        return closest[keep], (np.maximum(0., bound[keep] - SLACK) / 2.)
