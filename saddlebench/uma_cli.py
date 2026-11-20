"""Saddle geometry scoring for one confident seed or a benchmark dataset."""
import argparse
import csv
import json
from pathlib import Path
import sys

from ase.io import iread

from saddlebench.reference.uma import REVISION, UmaDataset, confidence_sources
from saddlebench.scoring.uma import UmaScoring, geometry
from saddlebench.uma_catalog import add_dataset_argument, check_dataset, select_cases
from saddlebench.uma_metrics import rate_totals
from saddlebench.uma_scoreboard import write_scoreboard


def score_case(scoring, seed, scorer, path, *, group_outside):
    mid, parent = seed["minimum_id"], seed["parent_id"]
    report = scorer.score(
        (geometry(atoms, arrays=True) for atoms in iread(path, index=":")),
        group_outside=group_outside,
    )
    report.update(
        hf_revision=REVISION, case_id=seed["case_id"], minimum_id=mid, parent_id=parent,
        dataset=scoring.datasets[parent], seed_confident=bool(confidence_sources(seed)),
        excluded_aligned_groups=scoring.aligned_counts[mid], confidence_sources=confidence_sources(seed),
    )
    report.update(rate_totals(scoring.references[mid], report["matched_saddle_ids_by_threshold"]))
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
    matched = {}
    for parent, group in by_parent.items():
        scorers = scoring.structure(parent, [seed["minimum_id"] for seed in group])
        for seed in group:
            report = score_case(scoring, seed, scorers[seed["minimum_id"]], paths[seed["case_id"]],
                                group_outside=not args.recall_only)
            rows.append(case_result_row(report))
            if args.html:
                matched[(seed["minimum_id"], "submission")] = report["matched_saddle_ids_by_threshold"]
    if not rows:
        parser.error(f"no eligible seeds in {args.dataset}")
    rows.sort(key=lambda row: row["case_id"])
    handle = args.output.open("x", newline="") if args.output else sys.stdout
    try:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            handle.close()
    if args.html:
        write_scoreboard(args.html, scoring, rows, dataset=args.dataset, matched=matched)
    return 0


def case_result_row(report):
    row = {key: report[key] for key in (
        "case_id", "dataset", "parent_id", "predictions_scored", "eligible_reference_groups",
        "excluded_aligned_groups", "outside_reference_predictions", "outside_reference_groups",
    )}
    for threshold, count in report["matched_reference_groups"].items():
        row[f"reference_groups_found_{threshold}_A"] = count
        row[f"recall_{threshold}_A"] = report["recall"][threshold]
    row["confidence_sources"] = "+".join(report["confidence_sources"])
    row.update((key, value) for key, value in report.items() if "rate_weight" in key)
    hits = row["reference_groups_found_0.20_A"]
    calls = report.get("physical_pes_calls")
    row["physical_pes_calls_per_reference_group_found"] = calls / hits if hits and calls is not None else None
    row["total_benchmark_work_s"] = report.get("reported_search_wall_s")
    return row


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
    parser.add_argument("--html", type=Path, help="Also write an interactive HTML scoreboard")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--confident-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--include-unconfident", action="store_true", help="Also score published seeds below confidence")
    parser.add_argument("--recall-only", action="store_true", help="Skip grouping unmatched predictions")
    args = parser.parse_args(argv)
    if args.html and (args.html.exists() or args.output and args.html.resolve() == args.output.resolve()):
        parser.error("--html must be a new path distinct from --output")
    if bool(args.case_id) != bool(args.submission) or bool(args.dataset) != bool(args.submission_dir):
        parser.error("use --case-id with --submission or --dataset with --submission-dir")
    if args.dataset and (args.elapsed_seconds is not None or args.physical_pes_calls is not None):
        parser.error("search totals apply to one case")
    dataset = UmaDataset(args.cache_dir)
    if args.confident_only and args.include_unconfident:
        parser.error("--confident-only and --include-unconfident cannot be combined")
    scoring = UmaScoring(dataset, include_unconfident=args.include_unconfident)
    if args.dataset:
        return score_dataset(parser, scoring, args)
    if args.case_id not in dataset.cases:
        parser.error(f"unknown UMA case {args.case_id!r}")
    case = dataset.cases[args.case_id]
    mid, parent = case["minimum_id"], case["parent_id"]
    seed = scoring.seeds[mid]
    if not args.include_unconfident and not confidence_sources(seed):
        parser.error(f"case {args.case_id} did not reach the confidence threshold on either tail")
    scorer = scoring.structure(parent, [mid])[mid]
    report = score_case(scoring, seed, scorer, args.submission, group_outside=not args.recall_only)
    report.update(reported_search_wall_s=args.elapsed_seconds, physical_pes_calls=args.physical_pes_calls)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with args.output.open("x") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    if args.html:
        write_scoreboard(args.html, scoring, [case_result_row(report)], dataset=report["dataset"],
                         matched={(mid, "submission"): report["matched_saddle_ids_by_threshold"]})
    return 0
