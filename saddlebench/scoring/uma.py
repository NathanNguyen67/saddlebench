"""Score ASE saddle predictions against the published per-seed reference."""
from collections import defaultdict
from hashlib import sha256
import json
from types import SimpleNamespace

import numpy as np

from saddlebench.matching.geometry import BatchedAnchorFilter, prepare_array_geometry
from saddlebench.matching.matcher import PreparedReferenceMatcher
from saddlebench.matching.profile_index import FIELDS, ProfileIndex
from saddlebench.reference import array_cache
from saddlebench.reference.array_cache import geometry

THRESHOLDS = (0.20, 0.10, 0.05)
GEOMETRY_COLUMNS = ["geometry_path", "geometry_index"]


class SeedScorer:
    def __init__(self, reference, profile=None):
        self.records = [
            {"reference_id": ident, "identity_id": ident, "geometry": geom}
            for ident, geom in sorted(reference.items())
        ]
        self.matcher = PreparedReferenceMatcher(
            SimpleNamespace(records=self.records, by_numbers=None), prepare=prepare_array_geometry,
        )
        self.anchors = None
        if profile and set(profile) == set(FIELDS):
            first = self.records[0]["geometry"]
            self.index = ProfileIndex(first["numbers"], first["pbc"], first["cell_angstrom"], profile)
        else:
            self.index = ProfileIndex.build([record["geometry"] for record in self.records])

    def nearest(self, prediction):
        ranked = self.index.candidates(prediction, THRESHOLDS[0]) if self.index is not None else None
        if ranked is None:
            if self.anchors is None:
                self.anchors = BatchedAnchorFilter(self.records, all_atoms=True)
            bounds = self.anchors.lower_bounds(prediction, THRESHOLDS[0])
            candidates = np.flatnonzero(bounds <= THRESHOLDS[0])
            bounds = bounds[candidates]
        else:
            candidates, bounds = ranked
        ordered = np.argsort(bounds, kind="stable")
        matches = self.matcher.match(
            {"geometry": prediction},
            ordered_bounds=((bounds[i], self.records[candidates[i]]) for i in ordered),
        )["matches"]
        return matches[0] if matches else None

    def score(self, predictions, *, group_outside=True):
        hits = {threshold: set() for threshold in THRESHOLDS}
        outside = []
        count = unmatched = 0
        for prediction in predictions:
            count += 1
            nearest = self.nearest(prediction)
            if nearest is None:
                unmatched += 1
                if group_outside:
                    outside.append(prediction)
                continue
            for threshold, groups in hits.items():
                if nearest["maxdev_angstrom"] <= threshold:
                    groups.add(nearest["identity_id"])
        eligible = len(self.records)
        return {
            "predictions_scored": count,
            "eligible_reference_groups": eligible,
            "matched_reference_groups": {f"{t:.2f}": len(groups) for t, groups in hits.items()},
            "recall": {f"{t:.2f}": len(groups) / eligible if eligible else None
                       for t, groups in hits.items()},
            "matched_saddle_ids": sorted(hits[THRESHOLDS[0]]),
            "outside_reference_predictions": unmatched,
            "outside_reference_groups": distinct_outside(outside) if group_outside else None,
        }


def distinct_outside(predictions):
    """Greedily count unmatched geometries against earlier representatives."""
    if not predictions:
        return 0
    records = [{"reference_id": i, "identity_id": i, "geometry": geom}
               for i, geom in enumerate(predictions)]
    anchors = BatchedAnchorFilter(records)
    matcher = PreparedReferenceMatcher(
        SimpleNamespace(records=records, by_numbers=None), prepare=prepare_array_geometry,
    )
    representatives = set()
    seen = set()
    for i, prediction in enumerate(predictions):
        fingerprint = (tuple(prediction["numbers"]), tuple(prediction["pbc"]),
                       tuple(map(tuple, prediction["cell_angstrom"])),
                       np.asarray(prediction["positions_angstrom"], dtype=float).tobytes())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates = ()
        if representatives:
            possible = anchors.possible(prediction, THRESHOLDS[0])
            candidates = (records[j] for j in representatives if possible[j])
        if not matcher.match({"geometry": prediction}, candidates=candidates, first=True)["matches"]:
            representatives.add(i)
    return len(representatives)


class UmaScoring:
    def __init__(self, dataset):
        self.dataset = dataset
        self.revision_digest = sha256(json.dumps(dataset.manifest, sort_keys=True).encode()).hexdigest()
        self.seeds = {row["minimum_id"]: row for row in dataset.table("seeds").to_pylist()}
        self.references = defaultdict(dict)
        for row in dataset.table("reference").to_pylist():
            if row["barrier_eV"] < 1.0:
                self.references[row["minimum_id"]][row["saddle_id"]] = row

    def structure(self, parent, seeds):
        scorers, pending = {}, {}
        for mid in seeds:
            path = array_cache.cache_path(self.dataset.cache_dir, "reference", self.revision_digest, mid)
            ids = sorted(self.references[mid])
            cached = array_cache.load(path, ids) if ids else None
            if cached is not None:
                scorers[mid] = SeedScorer(*cached)
            elif ids:
                pending[mid] = path
            else:
                scorers[mid] = SeedScorer({})
        if not pending:
            return scorers

        references = set().union(*(self.references[mid] for mid in pending))
        saddles = {
            row["saddle_id"]: row
            for row in self.dataset.table(
                "saddles", columns=["saddle_id", *GEOMETRY_COLUMNS],
                filters=[("parent_id", "=", parent)],
            ).to_pylist()
            if row["saddle_id"] in references
        }
        missing = references.difference(saddles)
        if missing:
            raise ValueError(f"Missing published reference geometry links for {sorted(missing)}")
        frames = {ident: geometry(atoms, arrays=True) for ident, atoms in self.dataset.frames(saddles)}
        for mid, path in pending.items():
            reference = {sid: frames[sid] for sid in sorted(self.references[mid])}
            scorer = SeedScorer(reference)
            array_cache.save(path, reference, scorer.index.arrays if scorer.index else None)
            scorers[mid] = scorer
        return scorers
