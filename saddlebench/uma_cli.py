"""Saddle geometry scoring for one confident seed or a benchmark dataset."""
import argparse
import csv
import json
from pathlib import Path
import sys

from ase.io import iread

from saddlebench.reference.uma import REVISION, UmaDataset
from saddlebench.scoring.uma import UmaScoring, geometry
from saddlebench.uma_catalog import add_dataset_argument, check_dataset, select_cases


def score_case(scoring, seed, scorer, path, *, group_outside):
    mid, parent = seed["minimum_id"], seed["parent_id"]
    report = scorer.score(
        (geometry(atoms, arrays=True) for atoms in iread(path, index=":")),
        group_outside=group_outside,
    )
    report.update(
        hf_revision=REVISION, case_id=seed["case_id"], minimum_id=mid, parent_id=parent,
        dataset=scoring.datasets[parent], seed_confident=True,
        excluded_aligned_groups=scoring.aligned_counts[mid],
    )
    return report


def score_dataset(parser, scoring, args):
    check_dataset(parser, scoring, args.dataset)
    seeds = select_cases(scoring, args.dataset)
    paths = {}
    for seed in seeds:
        case = seed["case_id"]
        options = [args.submission_dir / (case + ext) for ext in (".traj", ".extxyz")]
        present = [p for p in options if p.is_file()]
        if len(present) != 1:
            parser.error(f"provide exactly one trajectory for {case} in {args.submission_dir}")
        paths[case] = present[0]
    by_parent = {}
    for seed in seeds:
        by_parent.setdefault(seed["parent_id"], []).append(seed)
    rows = []
    for parent, group in by_parent.items():
        scorers = scoring.structure(parent, [seed["minimum_id"] for seed in group])
        for seed in group:
            report = score_case(scoring, seed, scorers[seed["minimum_id"]], paths[seed["case_id"]],
                                group_outside=not args.recall_only)
            row = {key: report[key] for key in (
                "case_id", "dataset", "parent_id", "predictions_scored", "eligible_reference_groups",
                "excluded_aligned_groups", "outside_reference_predictions", "outside_reference_groups",
            )}
            for threshold, count in report["matched_reference_groups"].items():
                row[f"reference_groups_found_{threshold}_A"] = count
                row[f"recall_{threshold}_A"] = report["recall"][threshold]
            rows.append(row)
    if not rows:
        parser.error(f"no confident seeds in {args.dataset}")
    handle = args.output.open("x", newline="") if args.output else sys.stdout
    try:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["case_id"]))
    finally:
        if args.output:
            handle.close()
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="saddlebench score")
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--case-id")
    add_dataset_argument(select)
    parser.add_argument("--submission", type=Path, help="ASE .traj or .extxyz for --case-id")
    parser.add_argument("--submission-dir", type=Path, help="CASE_ID.traj or CASE_ID.extxyz files for --dataset")
    parser.add_argument("--elapsed-seconds", type=float, help="Reported search time for --case-id")
    parser.add_argument("--physical-pes-calls", type=int, help="Reported total physical PES calls for --case-id")
    parser.add_argument("--output", type=Path, help="JSON for --case-id or CSV for --dataset")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--confident-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recall-only", action="store_true", help="Skip grouping unmatched predictions")
    args = parser.parse_args(argv)
    if bool(args.case_id) != bool(args.submission) or bool(args.dataset) != bool(args.submission_dir):
        parser.error("use --case-id with --submission or --dataset with --submission-dir")
    if args.dataset and (args.elapsed_seconds is not None or args.physical_pes_calls is not None):
        parser.error("search totals apply to one case")
    dataset = UmaDataset(args.cache_dir)
    scoring = UmaScoring(dataset)
    if args.dataset:
        return score_dataset(parser, scoring, args)
    if args.case_id not in dataset.cases:
        parser.error(f"unknown UMA case {args.case_id!r}")
    case = dataset.cases[args.case_id]
    mid, parent = case["minimum_id"], case["parent_id"]
    seed = scoring.seeds[mid]
    if not seed["confident"]:
        parser.error(f"case {args.case_id} did not reach the published confidence threshold")
    scorer = scoring.structure(parent, [mid])[mid]
    report = score_case(scoring, seed, scorer, args.submission, group_outside=not args.recall_only)
    report.update(reported_search_wall_s=args.elapsed_seconds, physical_pes_calls=args.physical_pes_calls)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with args.output.open("x") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0
