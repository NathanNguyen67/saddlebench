"""CPU-only public UMA score and benchmark commands."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from ase.io import iread

from saddlebench.reference.uma import REVISION, UmaDataset, select_endpoint_pair
from saddlebench.scoring.uma import UmaScoring, geometry


def main(argv):
    parser = argparse.ArgumentParser(prog="saddlebench score")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--submission", required=True, help="ASE .traj or .extxyz of predicted saddles")
    parser.add_argument("--elapsed-seconds", type=float, help="Reported total search time in seconds")
    parser.add_argument("--physical-pes-calls", type=int, help="Reported total physical PES calls")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--confident-only", action="store_true")
    args = parser.parse_args(argv)
    dataset = UmaDataset(args.cache_dir)
    if args.case_id not in dataset.cases:
        parser.error(f"unknown UMA case {args.case_id!r}")
    case = dataset.cases[args.case_id]
    scoring = UmaScoring(dataset)
    minimum_id, parent = case["minimum_id"], case["parent_id"]
    seed = scoring.seeds[minimum_id]
    if args.confident_only and not seed["confident"]:
        parser.error(f"case {args.case_id} did not reach the published confidence threshold")
    scorers = scoring.structure(parent, [minimum_id])
    report = scorers[minimum_id].score(
        geometry(atoms) for atoms in iread(args.submission, index=":")
    )
    matched = set(report["matched_saddle_ids"])
    pairs = defaultdict(list)
    for pair in dataset.table("endpoint_pairs", filters=[("parent_id", "=", parent)]).to_pylist():
        if (pair["saddle_id"] in matched and pair["n_complete"]
                and pair["minimum_a"] != pair["minimum_b"]):
            pairs[pair["saddle_id"]].append(pair)
    report.update(
        hf_revision=REVISION, case_id=args.case_id, minimum_id=minimum_id, parent_id=parent,
        seed_confident=seed["confident"], reported_search_wall_s=args.elapsed_seconds,
        physical_pes_calls=args.physical_pes_calls,
        default_endpoint_pairs={sid: select_endpoint_pair(pairs[sid], minimum_id)
                                for sid in report["matched_saddle_ids"]},
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with args.output.open("x") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0
