"""Symmetry families and near-null products for the UMA reference saddles (explorer labels only; scores unchanged).

For every confident seed with reference saddles:

* seed symmetries: lattice point operations R (integer in fractional coordinates, orthogonal in Cartesian ones) plus a
  translation that map the seed minimum onto itself, every atom onto a same-species atom within SEED_TOL;
* families: each reference saddle is transformed by every seed symmetry, its atoms relabelled with the seed's own
  permutation, and handed to the unchanged scorer. A match (canonical core-drift maxdev <= 0.2 A) to another reference
  saddle joins the two. A family is one physical mechanism; its members are distinct, symmetry-equivalent saddles;
* near-null products: seed-connected endpoint minima within NEAR_NULL of the seed (canonical maxdev).

    python scripts/build_uma_symmetry.py saddlebench/reference/uma_symmetry.json
"""
import argparse
from collections import defaultdict
import itertools
import json
from pathlib import Path

import numpy as np

from saddlebench.matching.geometry import core_drift_maxdev
from saddlebench.reference.array_cache import geometry
from saddlebench.reference.uma import REVISION, UmaDataset
from saddlebench.scoring.uma import THRESHOLDS, UmaScoring
from saddlebench.uma_catalog import select_cases

SEED_TOL = 0.10
NEAR_NULL = 0.30


def point_operations(cell):
    """(M, R): M integer on fractional coordinates, R = C^T M C^-T orthogonal."""
    C = np.asarray(cell, float)
    Ci = np.linalg.inv(C)
    for flat in itertools.product((-1, 0, 1), repeat=9):
        M = np.array(flat).reshape(3, 3)
        if abs(round(np.linalg.det(M))) == 1:
            R = C.T @ M @ Ci.T
            if np.allclose(R @ R.T, np.eye(3), atol=2e-3):
                yield M, R


def _assign(moved, numbers, cell, pbc, target):
    C = np.asarray(cell, float)
    Ci = np.linalg.inv(C)
    perm = np.full(len(moved), -1)
    worst = 0.0
    for z in np.unique(numbers):
        idx = np.flatnonzero(numbers == z)
        df = (moved[idx] @ Ci)[:, None, :] - (target[idx] @ Ci)[None, :, :]
        for k in range(3):
            if pbc[k]:
                df[..., k] -= np.round(df[..., k])
        dist = np.linalg.norm(df @ C, axis=2)
        j = dist.argmin(axis=1)
        if len(set(j.tolist())) != len(idx):
            return None, np.inf
        perm[idx] = idx[j]
        worst = max(worst, float(dist[np.arange(len(idx)), j].max()))
    return perm, worst


def seed_symmetries(seed):
    """Symmetry operations of the seed (identity first), each {R, t, perm, dev}."""
    pos = np.asarray(seed["positions_angstrom"], float)
    numbers = np.asarray(seed["numbers"])
    cell = np.asarray(seed["cell_angstrom"], float)
    species, counts = np.unique(numbers, return_counts=True)
    anchors = np.flatnonzero(numbers == species[counts.argmin()])
    ops, seen = [], set()
    for M, R in point_operations(cell):
        rotated = pos @ R.T
        for j in anchors:
            t = pos[j] - rotated[anchors[0]]
            perm, dev = _assign(rotated + t, numbers, cell, seed["pbc"], pos)
            if perm is not None and dev <= SEED_TOL and tuple(perm) not in seen:
                seen.add(tuple(perm))
                ops.append({"R": R, "t": t, "perm": perm, "dev": dev})
    ops.sort(key=lambda op: not (np.allclose(op["R"], np.eye(3)) and (op["perm"] == np.arange(len(pos))).all()))
    return ops


def transform(op, geom):
    moved = np.asarray(geom["positions_angstrom"], float) @ op["R"].T + op["t"]
    out = np.empty_like(moved)
    out[op["perm"]] = moved
    return {**geom, "positions_angstrom": out}


def describe(op):
    """Short human label: mirror plane, rotation axis and angle, inversion or relabelling."""
    R, t = op["R"], op["t"]
    det = round(float(np.linalg.det(R)))
    w, v = np.linalg.eig(R)
    if np.allclose(R, np.eye(3), atol=1e-6):
        if np.linalg.norm(t) > 1e-3:
            return "translation"
        return "identity" if (op["perm"] == np.arange(len(op["perm"]))).all() else "relabelling of identical atoms"
    if det > 0:
        axis = np.real(v[:, np.argmin(np.abs(w - 1))])
        angle = np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))
        return f"{angle:.0f}° rotation about {_vector(axis)}"
    if np.allclose(R, -np.eye(3), atol=1e-6):
        return "inversion"
    if np.isclose(np.trace(R), 1, atol=1e-3):
        normal = np.real(v[:, np.argmin(np.real(w))])
        normal = normal / np.linalg.norm(normal)
        return f"mirror ⟂ {_vector(normal)} at {float(normal @ t) / 2:.2f} Å"
    return "rotoreflection"


def _vector(x):
    x = x / np.abs(x).max()
    if x[np.argmax(np.abs(x))] < 0:
        x = -x
    for name, axis in (("x", (1, 0, 0)), ("y", (0, 1, 0)), ("z", (0, 0, 1))):
        if np.allclose(x, axis, atol=1e-3):
            return name
    return "[" + " ".join(f"{c:.2f}".rstrip("0").rstrip(".") for c in x) + "]"


def plain(g):
    return {**g, "numbers": [int(z) for z in g["numbers"]], "pbc": [bool(p) for p in g["pbc"]],
            "cell_angstrom": np.asarray(g["cell_angstrom"], float).tolist(),
            "positions_angstrom": np.asarray(g["positions_angstrom"], float).tolist()}


def seed_entry(dataset, scorer, seed_row, seed_pairs, minima):
    mid = seed_row["minimum_id"]
    seed = geometry(next(atoms for _, atoms in dataset.frames({mid: seed_row})), arrays=True)
    ops = seed_symmetries(seed)
    records = {record["identity_id"]: record["geometry"] for record in scorer.records}
    parent = {sid: sid for sid in records}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links = defaultdict(list)
    for sid in sorted(records):
        for k, op in enumerate(ops[1:], start=1):
            hit = scorer.nearest(transform(op, records[sid]))
            if hit and hit["identity_id"] != sid and hit["maxdev_angstrom"] <= THRESHOLDS[0]:
                other = hit["identity_id"]
                links[sid].append([other, k, round(hit["maxdev_angstrom"], 4)])
                a, b = root(sid), root(other)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    families = defaultdict(list)
    for sid in sorted(records):
        families[root(sid)].append(sid)

    products = defaultdict(set)
    own = {mid, seed_row["canonical_id"]}
    for sid in records:
        for pair in seed_pairs.get(sid, ()):
            a, b = pair["minimum_a"], pair["minimum_b"]
            if a != b and (a in own or b in own):
                products[sid].add(b if a in own else a)
    needed = sorted(set().union(*products.values())) if products else []
    frames = {ident: geometry(atoms, arrays=True) for ident, atoms in dataset.frames({x: minima[x] for x in needed})}
    seed_plain = plain(seed)
    distance = {x: core_drift_maxdev(seed_plain, plain(frames[x])) for x in needed}
    near_null = {}
    for sid, ends in sorted(products.items()):
        closest = min(ends, key=distance.get)
        if distance[closest] < NEAR_NULL:
            near_null[sid] = {"product": closest, "maxdev": round(distance[closest], 4),
                              "dE": round(minima[closest]["energy_eV"] - seed_row["energy_eV"], 4)}
    return {
        "operations": [{"label": describe(op), "seed_maxdev": round(op["dev"], 4)} for op in ops],
        "families": [members for members in families.values() if len(members) > 1],
        "links": {sid: [{"saddle": o, "operation": k, "maxdev": d} for o, k, d in rows] for sid, rows in sorted(links.items())},
        "near_null": near_null,
        "reference": len(records),
        "family_count": len(families),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--dataset", default="literature", help="Case selection, as for saddlebench baseline")
    parser.add_argument("--parent", action="append", help="Only these parents (repeatable)")
    args = parser.parse_args()
    dataset = UmaDataset()
    scoring = UmaScoring(dataset)
    pairs = defaultdict(list)
    for row in dataset.table("endpoint_pairs", columns=["saddle_id", "minimum_a", "minimum_b", "seed_barriers"]).to_pylist():
        for link in row["seed_barriers"] or ():
            pairs[link["minimum_id"], row["saddle_id"]].append(row)
    minima = {row["minimum_id"]: row for row in dataset.table(
        "minima", columns=["minimum_id", "energy_eV", "geometry_path", "geometry_index"]).to_pylist()}
    by_parent = defaultdict(list)
    for case in select_cases(scoring, args.dataset):
        if scoring.references[case["minimum_id"]]:
            by_parent[case["parent_id"]].append(case["minimum_id"])
    seeds = {}
    for parent in sorted(by_parent):
        if args.parent and parent not in args.parent:
            continue
        scorers = scoring.structure(parent, by_parent[parent])
        for mid in sorted(by_parent[parent]):
            seed_pairs = {sid: rows for (m, sid), rows in pairs.items() if m == mid}
            entry = seed_entry(dataset, scorers[mid], scoring.seeds[mid], seed_pairs, minima)
            seeds[mid] = {"parent_id": parent, **entry}
            print(f"{parent} {mid}: {entry['reference']} reference saddles, {entry['family_count']} families, "
                  f"{len(entry['operations'])} seed symmetries, {len(entry['near_null'])} near-null", flush=True)
    args.output.write_text(json.dumps({
        "hf_revision": REVISION,
        "reference_digest": scoring.revision_digest,
        "seed_tolerance_angstrom": SEED_TOL,
        "match_cutoff_angstrom": THRESHOLDS[0],
        "near_null_angstrom": NEAR_NULL,
        "dataset": args.dataset,
        "method": __doc__.split("\n\n")[1].strip(),
        "seeds": seeds,
    }, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
