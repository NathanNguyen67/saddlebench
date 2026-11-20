"""Rebuild the UMA aligned-slide exclusions from the pinned public export."""
import argparse
from collections import defaultdict
import csv
from pathlib import Path
import sys

from ase.constraints import FixAtoms
from ase.io.trajectory import Trajectory
import numpy as np
import pyarrow.parquet as pq


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    parser.add_argument("saddlemotif_build", type=Path, help="Explorer v2 build/ at SaddleMotif commit 93c30fa")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.saddlemotif_build.resolve()))
    import motion
    import pack

    def rows(table, columns=None):
        with (args.export / f"{table}.parquet").open("rb") as handle:
            return pq.read_table(handle, columns=columns).to_pylist()

    seeds = {r["minimum_id"]: r for r in rows("seeds") if r["confident"]}
    references = {(r["minimum_id"], r["saddle_id"]) for r in rows("reference")
                  if r["minimum_id"] in seeds and r["barrier_eV"] < 1.0}
    products = defaultdict(set)
    for pair in rows("endpoint_pairs"):
        a, b = pair["minimum_a"], pair["minimum_b"]
        if a == b or not pair["n_complete"]:
            continue
        for link in pair["seed_barriers"]:
            mid = link["minimum_id"]
            key = mid, pair["saddle_id"]
            if key in references:
                canonical = seeds[mid]["canonical_id"]
                products[key].add(b if canonical == a else a)
    if products.keys() != references:
        raise ValueError(f"Missing endpoint pairs for {len(references - products.keys())} confident references")

    used = set().union(*products.values())
    minima = {r["minimum_id"]: r for r in rows("minima", ["minimum_id", "geometry_path", "geometry_index"])
              if r["minimum_id"] in used}
    by_shard = defaultdict(dict)
    for (mid, sid), endpoints in products.items():
        by_shard[seeds[mid]["geometry_path"]][mid, sid] = endpoints

    excluded = []
    for path, groups in sorted(by_shard.items()):
        needed = {mid: seeds[mid] for mid, _ in groups}
        needed.update({mid: minima[mid] for mids in groups.values() for mid in mids})
        with Trajectory(args.export / path) as trajectory:
            frames = {}
            for ident, row in sorted(needed.items(), key=lambda item: item[1]["geometry_index"]):
                if row["geometry_path"] != path:
                    raise ValueError(f"Unexpected geometry shard for {ident}")
                frames[ident] = trajectory[row["geometry_index"]]
        aligned = {}
        for mid, sid in sorted(groups):
            seed = frames[mid]
            fixed = np.zeros(len(seed), dtype=bool)
            for constraint in seed.constraints:
                if isinstance(constraint, FixAtoms):
                    fixed[constraint.get_indices()] = True
            decisions = []
            for product in groups[mid, sid]:
                key = mid, product
                if key not in aligned:
                    d = pack.align(frames[product].positions, seed.positions, seed.cell, seed.pbc, fixed)
                    rigid = motion.rigid_groups(seed.positions, d, seed.cell, seed.pbc, ~fixed)
                    aligned[key] = any(g["kind"] == "aligned" and g["size"] >= motion.THRESHOLDS["aligned_min_atoms"]
                                       for g in rigid)
                decisions.append(aligned[key])
            if all(decisions):
                excluded.append((mid, sid))

    with args.output.open("x", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("minimum_id", "saddle_id"))
        writer.writerows(sorted(excluded))
    print(f"{len(excluded)} aligned slides excluded out of {len(references)} confident reference groups")


if __name__ == "__main__":
    main()
