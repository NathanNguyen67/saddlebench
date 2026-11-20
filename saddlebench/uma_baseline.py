"""Reproduce the pinned UMA benchmark from all attempt rows."""
import argparse
import csv
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from multiprocessing import get_context
from pathlib import Path
from time import perf_counter

import pyarrow.parquet as pq

from saddlebench.reference.uma import REVISION, SEARCH_REVISION, BenchmarkSearchFrames, UmaDataset
from saddlebench.scoring.uma import UmaScoring

COHORT = "bench_20261118"
METHODS = ("sella_prfo_diag4_abs01", "tsbfgs_dimer_rot4_frot01_r23_allprobes")


def load_attempts(dataset, path=None):
    if path is None:
        return dataset.table("attempts", filters=[("benchmark_cohort", "=", COHORT)])
    with path.open("rb") as handle:
        return pq.read_table(handle)


def summarize(scoring, attempts, search, workers=1, *, group_outside=True):
    rows = attempts.to_pylist()
    groups = Counter((row["minimum_id"], row["method_id"]) for row in rows)
    if (len(rows) != 36000 or len({row["seq"] for row in rows}) != 36000
            or {row["benchmark_cohort"] for row in rows} != {COHORT}
            or set(groups.values()) != {500} or set(m for _, m in groups) != set(METHODS)
            or len({mid for mid, _ in groups}) != 36):
        raise ValueError("Expected 36 unique seeds with 500 attempts for each of the two baseline methods")
    by_structure = defaultdict(lambda: defaultdict(list))
    for row in sorted(rows, key=lambda row: row["seq"]):
        mid, parent = row["minimum_id"], row["parent_id"]
        if (scoring.seeds[mid]["parent_id"] != parent
                or not (parent.startswith("lit_") or parent in {"oc20:al_385", "oc20:pt_343"})):
            raise ValueError(f"Unexpected baseline seed or parent: {mid}, {parent}")
        by_structure[parent][mid, row["method_id"]].append(row)

    structures = sorted(by_structure.items())
    score = partial(score_structure, scoring=scoring, search=search, group_outside=group_outside)
    if workers == 1:
        for item in structures:
            yield from score(item)
        return

    # Verify the shared file once. Each worker opens its own Parquet reader,
    # avoiding a fork of the Arrow thread pool in the parent.
    scoring.dataset.file("saddles.parquet")
    # Start structures with the most potential comparisons first.
    def work(item):
        return sum(len(scoring.references[mid]) * len(group)
                   for (mid, _), group in item[1].items())

    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        jobs = {item[0]: pool.submit(score, item)
                for item in sorted(structures, key=work, reverse=True)}
        for parent, _ in structures:
            yield from jobs[parent].result()


def score_structure(item, *, scoring, search, group_outside):
    parent, submissions = item
    seeds = sorted({mid for mid, _ in submissions})
    expected = {row["seq"] for group in submissions.values() for row in group if row["converged"]}
    frames = search.geometries(parent)
    if frames.keys() != expected:
        raise ValueError(f"Search geometry coverage differs from converged attempt rows for {parent}")
    scorers = scoring.structure(parent, seeds)
    totals = defaultdict(Counter)
    for (mid, method), group in submissions.items():
        counts = totals[method]
        counts["seeds"] += 1
        counts["attempts"] += len(group)
        converged = [row for row in group if row["converged"]]
        counts["converged"] += len(converged)
        report = scorers[mid].score(
            (frames[row["seq"]] for row in converged), group_outside=group_outside,
        )
        counts["predictions_scored"] += report["predictions_scored"]
        counts["eligible_reference_groups"] += report["eligible_reference_groups"]
        counts["outside_reference_predictions"] += report["outside_reference_predictions"]
        if group_outside:
            counts["outside_reference_groups"] += report["outside_reference_groups"]
        else:
            counts["outside_reference_groups"] = None
        for threshold, hits in report["matched_reference_groups"].items():
            counts[f"reference_groups_found_{threshold}_A"] += hits
        for source, label in (("physical_pes_calls", "physical_pes_calls"),
                              ("total_wall_s", "reported_search_wall_s")):
            values = [row[source] for row in group if row[source] is not None]
            counts[f"known_{label}"] += sum(values)
            counts[f"missing_{label}"] += len(group) - len(values)

    results = []
    for method, counts in sorted(totals.items()):
        found = counts["reference_groups_found_0.20_A"]
        eligible = counts["eligible_reference_groups"]
        results.append({
            "parent_id": parent, "method_id": method, **counts,
            "convergence_rate": counts["converged"] / counts["attempts"],
            "converged_without_exported_geometry": counts["converged"] - counts["predictions_scored"],
            "recall_0.20_A": found / eligible if eligible else None,
            "physical_pes_calls_per_reference_group_found": (
                counts["known_physical_pes_calls"] / found
                if found and not counts["missing_physical_pes_calls"] else None
            ),
        })
    return results


def main(argv):
    parser = argparse.ArgumentParser(prog="saddlebench baseline")
    parser.add_argument("--attempts", type=Path, help="Bundled baseline Parquet table (default fetches pinned export)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--search-geometry-dir", type=Path, help="Use a local copy of the exact Search supplement")
    parser.add_argument("--workers", type=int, default=1, help="Score independent structures in parallel")
    parser.add_argument("--recall-only", action="store_true", help="Skip grouping unmatched predictions")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    start = perf_counter()
    dataset = UmaDataset(args.cache_dir)
    search = BenchmarkSearchFrames(args.cache_dir, directory=args.search_geometry_dir)
    results = list(summarize(
        UmaScoring(dataset), load_attempts(dataset, args.attempts), search, args.workers,
        group_outside=not args.recall_only,
    ))
    with args.output.open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    total = Counter()
    for row in results:
        for column in ("attempts", "converged", "predictions_scored", "converged_without_exported_geometry"):
            total[column] += row[column]
    print(f"UMA {REVISION}, Search frames {SEARCH_REVISION}: {dict(total)}, "
          f"{len(results)} structure-method rows; "
          f"scorer elapsed time {perf_counter() - start:.2f} s; {args.output}")
    return 0
