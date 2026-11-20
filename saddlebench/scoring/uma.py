"""Score ASE saddle predictions against the published per-seed reference."""
from collections import defaultdict
from itertools import compress
from types import SimpleNamespace

import numpy as np

from saddlebench.matching.geometry import BatchedAnchorFilter
from saddlebench.matching.matcher import PreparedReferenceMatcher

THRESHOLDS = (0.20, 0.10, 0.05)
GEOMETRY_COLUMNS = ["geometry_path", "geometry_index"]


def geometry(atoms):
    return {
        "numbers": atoms.numbers.tolist(),
        "pbc": atoms.pbc.tolist(),
        "cell_angstrom": atoms.cell.array.tolist(),
        "positions_angstrom": atoms.positions.tolist(),
        "fixed_indices": [],
        "constraints_known": False,
    }


class SeedScorer:
    def __init__(self, reference):
        self.records = [
            {"reference_id": ident, "identity_id": ident, "geometry": geom}
            for ident, geom in sorted(reference.items())
        ]
        self.matcher = PreparedReferenceMatcher(SimpleNamespace(records=self.records, by_numbers=None))
        self.anchors = BatchedAnchorFilter(self.records, all_atoms=True)

    def nearest(self, prediction):
        candidates = compress(self.records, self.anchors.possible(prediction, THRESHOLDS[0]))
        matches = self.matcher.match({"geometry": prediction}, candidates=candidates)["matches"]
        return matches[0] if matches else None

    def score(self, predictions):
        hits = {threshold: set() for threshold in THRESHOLDS}
        outside = []
        count = 0
        for prediction in predictions:
            count += 1
            nearest = self.nearest(prediction)
            if nearest is None:
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
            "outside_reference_predictions": len(outside),
            "outside_reference_groups": distinct_outside(outside),
        }


def distinct_outside(predictions):
    """Greedily count unmatched geometries against earlier representatives."""
    if not predictions:
        return 0
    records = [{"reference_id": i, "identity_id": i, "geometry": geom}
               for i, geom in enumerate(predictions)]
    anchors = BatchedAnchorFilter(records)
    matcher = PreparedReferenceMatcher(SimpleNamespace(records=records, by_numbers=None))
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
        if not matcher.match({"geometry": prediction}, candidates=candidates)["matches"]:
            representatives.add(i)
    return len(representatives)


class UmaScoring:
    def __init__(self, dataset):
        self.dataset = dataset
        self.seeds = {row["minimum_id"]: row for row in dataset.table("seeds").to_pylist()}
        self.references = defaultdict(dict)
        for row in dataset.table("reference").to_pylist():
            if row["barrier_eV"] < 1.0:
                self.references[row["minimum_id"]][row["saddle_id"]] = row

    def structure(self, parent, seeds):
        references = set().union(*(self.references[mid] for mid in seeds))
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
        records = {mid: self.seeds[mid] for mid in seeds}
        records.update(saddles)
        frames = {ident: geometry(atoms) for ident, atoms in self.dataset.frames(records)}
        return {mid: SeedScorer({sid: frames[sid] for sid in self.references[mid]}) for mid in seeds}
