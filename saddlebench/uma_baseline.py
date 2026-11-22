"""Reproduce the pinned UMA benchmark from all attempt rows."""
import argparse
import csv
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import hashlib
from io import BytesIO
from itertools import chain
import json
import os
from multiprocessing import get_context
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from zipfile import ZipFile

from ase.io.trajectory import Trajectory
from ase.io import iread

import pyarrow.parquet as pq
from huggingface_hub.constants import HF_HUB_CACHE

from saddlebench.reference.uma import REVISION, BenchmarkSearchFrames, UmaDataset
from saddlebench.reference.uma_motif import default_motif_dir
from saddlebench.scoring.uma import GEOMETRY_COLUMNS, UmaScoring, geometry
from saddlebench.uma_catalog import add_dataset_argument, check_dataset, select_cases
from saddlebench.uma_metrics import AUGMENTED, first_hit_events, rate_totals
from saddlebench.uma_scoreboard import write_scoreboard

COHORT = "bench_20261118"
MOTIF_CHUNK = 8000  # proposal frames per parallel nearest-match task
METHODS = ("sella_prfo_diag4_abs01", "tsbfgs_dimer_rot4_frot01_r23_allprobes")


def load_attempts(dataset, path=None):
    if path is None:
        return dataset.table("attempts", filters=[("benchmark_cohort", "=", COHORT)])
    with path.open("rb") as handle:
        return pq.read_table(handle)


def motif_generation_times(directory):
    times = {}
    if directory is None:
        return times
    timings = directory / "timings.json"
    if timings.is_file():
        for entry in json.loads(timings.read_text()):
            case, method, elapsed = entry["case_id"], entry["method_id"], entry["generation_wall_s"]
            if elapsed >= 0 and (directory / method / f"{case}.traj").is_file():
                times[case, method] = elapsed
    for report in (directory / "work").glob("*/result.json"):
        item = json.loads(report.read_text())
        case, method = item["case_id"], item["method_id"]
        output = directory / method / f"{case}.traj"
        staged = report.with_name("proposals.traj")
        if (not item.get("timing_unavailable") and output.is_file() and staged.is_file()
                and output.samefile(staged)):
            elapsed = item.get("generation_wall_s")
            if elapsed is None:
                elapsed = report.stat().st_mtime - report.with_name("events.json").stat().st_mtime
            if elapsed >= 0:
                times[case, method] = elapsed
    return times


def summarize(scoring, attempts, search=None, workers=1, *, group_outside=True,
              dataset=None, archive=None, motif_dir=None, include_matches=False, seeds=None):
    """seeds optionally restricts scoring to these minimum IDs (the cohort is still validated in full)."""
    rows = attempts.to_pylist()
    groups = Counter((row["minimum_id"], row["method_id"]) for row in rows)
    if (len(rows) != 36000 or len({row["seq"] for row in rows}) != 36000
            or {row["benchmark_cohort"] for row in rows} != {COHORT}
            or set(groups.values()) != {500} or set(m for _, m in groups) != set(METHODS)
            or len({mid for mid, _ in groups}) != 36):
        raise ValueError("Expected 36 unique seeds with 500 attempts for each of the two baseline methods")
    selected = {seed['minimum_id'] for seed in select_cases(scoring, dataset)}
    if seeds is not None:
        selected &= set(seeds)
    by_structure = defaultdict(lambda: defaultdict(list))
    for row in sorted(rows, key=lambda row: row["seq"]):
        mid, parent = row["minimum_id"], row["parent_id"]
        if (scoring.seeds[mid]["parent_id"] != parent
                or not (parent.startswith("lit_") or parent in {"oc20:al_385", "oc20:pt_343"})):
            raise ValueError(f"Unexpected baseline seed or parent: {mid}, {parent}")
        if mid in selected:
            by_structure[parent][mid, row["method_id"]].append(row)

    structures = sorted(by_structure.items())
    score = partial(score_structure, scoring=scoring, search=search, archive=archive,
                    group_outside=group_outside, motif_dir=motif_dir, include_matches=include_matches,
                    motif_times=motif_generation_times(motif_dir))
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
        # SaddleMotif proposals dominate the work and one structure can hold half of them, so their nearest
        # matches are computed first in independent chunks; each structure then reuses them in order.
        known = motif_nearest(pool, scoring, structures, motif_dir) if motif_dir else {}
        jobs = {item[0]: pool.submit(score, item, motif_known={key: known[key] for key in known
                                                               if key[0] in {mid for mid, _ in item[1]}})
                for item in sorted(structures, key=work, reverse=True)}
        for parent, _ in structures:
            yield from jobs[parent].result()


def proposal_path(scoring, motif_dir, mid, method):
    path = motif_dir / method / f"{scoring.seeds[mid]['case_id']}.traj"
    return path if path.is_file() and path.stat().st_size else None


def nearest_chunk(task, *, scoring):
    """Nearest reference match of each proposal frame in [start, stop) of one proposal file."""
    parent, mid, path, start, stop = task
    scorer = scoring.structure(parent, [mid])[mid]
    return [scorer.within(geometry(atoms, arrays=True)) for atoms in iread(path, index=f"{start}:{stop}")]


def motif_nearest(pool, scoring, structures, motif_dir):
    """(minimum_id, method) -> reference matches of every proposal frame, computed in parallel chunks."""
    tasks = []
    for parent, submissions in structures:
        for mid, method in submissions:
            path = proposal_path(scoring, motif_dir, mid, method)
            if path is None:
                continue
            with Trajectory(path) as trajectory:
                count = len(trajectory)
            for start in range(0, count, MOTIF_CHUNK):
                tasks.append(((mid, method), (parent, mid, path, start, min(start + MOTIF_CHUNK, count))))
    tasks.sort(key=lambda task: task[1][4] - task[1][3], reverse=True)
    futures = [(key, task[3], pool.submit(nearest_chunk, task, scoring=scoring)) for key, task in tasks]
    chunks = defaultdict(list)
    for key, start, future in futures:
        chunks[key].append((start, future.result()))
    return {key: [match for _, part in sorted(parts, key=lambda part: part[0]) for match in part]
            for key, parts in chunks.items()}


def predictions_for_structure(parent, submissions, scoring, search, archive):
    if archive is not None:
        frames = {}
        with ZipFile(archive) as bundle:
            for (mid, method), rows in submissions.items():
                case = scoring.seeds[mid]["case_id"]
                name = f"{case}/{method}.traj"
                converged = [row["seq"] for row in rows if row["converged"]]
                with Trajectory(BytesIO(bundle.read(name))) as trajectory:
                    if len(trajectory) != len(converged):
                        raise ValueError(f"Wrong number of converged frames in {name}")
                    frames.update((seq, geometry(atoms, arrays=True))
                                  for seq, atoms in zip(converged, trajectory))
        return frames, "exact_search"
    if search is not None:
        frames = search.geometries(parent)
        expected = {row["seq"] for rows in submissions.values() for row in rows if row["converged"]}
        if not expected.issubset(frames):
            raise ValueError(f"Missing Search geometries for {parent}")
        return frames, "exact_search"

    attempts = [row for rows in submissions.values() for row in rows if row["converged"]]
    ids = {row["saddle_id"] for row in attempts}
    records = {
        row["saddle_id"]: row
        for row in scoring.dataset.table(
            "saddles", columns=["saddle_id", *GEOMETRY_COLUMNS],
            filters=[("parent_id", "=", parent)],
        ).to_pylist()
        if row["saddle_id"] in ids
    }
    representatives = {sid: geometry(atoms, arrays=True) for sid, atoms in scoring.dataset.frames(records)}
    return {row["seq"]: representatives[row["saddle_id"]] for row in attempts
            if row["saddle_id"] in representatives}, "public_group_representative"


def score_structure(item, *, scoring, search, archive, group_outside, motif_dir, include_matches,
                    motif_times=None, motif_known=None):
    parent, submissions = item
    seeds = sorted({mid for mid, _ in submissions})
    frames, geometry_source = predictions_for_structure(parent, submissions, scoring, search, archive)
    scorers = scoring.structure(parent, seeds)
    totals = defaultdict(Counter)
    matches = defaultdict(dict)
    timelines = defaultdict(lambda: (0., []))
    for (mid, method), group in submissions.items():
        converged = [row for row in group if row["converged"]]
        evaluated = [row for row in converged if row["seq"] in frames]
        base = [frames[row["seq"]] for row in evaluated]
        # A .traj under the source method ID contains additional SaddleMotif proposals for one seed.
        case = scoring.seeds[mid]["case_id"]
        motif = motif_dir / method / f"{case}.traj" if motif_dir else None
        submissions_to_score = [(method, [])]
        base_nearest = []
        added_nearest = (motif_known or {}).get((mid, method))
        if motif and motif.is_file():
            if added_nearest is not None and not group_outside:
                added = [None] * len(added_nearest)  # matches precomputed; geometry only needed for grouping
            else:
                added = [geometry(atoms, arrays=True) for atoms in iread(motif, index=":")] if motif.stat().st_size else []
            submissions_to_score.append((AUGMENTED[method], added))
        for label, added in submissions_to_score:
            counts = totals[label]
            counts["seeds"] += 1
            counts["attempts"] += len(group)
            counts["converged"] += len(converged)
            counts["motif_proposals"] += len(added)
            counts["converged_without_exported_geometry"] += len(converged) - len(base)
            proposal_time = (motif_times or {}).get((case, method)) if label != method else 0.
            if label != method:
                counts["known_motif_generation_wall_s"] += proposal_time or 0.
                counts["missing_motif_generation_times"] += proposal_time is None
            prediction_matches = []
            # The augmented row repeats the base predictions first; reuse their nearest matches.
            report = scorers[mid].score(
                chain(base, added), group_outside=group_outside,
                on_prediction=prediction_matches.append if include_matches or label == method else None,
                known=(base_nearest + (added_nearest or [])) if label != method else (),
            )
            if label == method:
                base_nearest = list(prediction_matches)
            matched = report["matched_saddle_ids_by_threshold"]
            if include_matches:
                matches[label][mid] = matched
                trace = first_hit_events(group, evaluated, prediction_matches, scoring.references[mid],
                                         proposal_time=proposal_time)
                if trace is None or proposal_time is None:
                    timelines[label] = None
                elif timelines[label] is not None:
                    offset, accumulated = timelines[label]
                    spent, events = trace
                    accumulated.extend((offset + time, cutoff, weight) for time, cutoff, weight in events)
                    timelines[label] = offset + spent, accumulated
            for key in ("predictions_scored", "eligible_reference_groups", "outside_reference_predictions"):
                counts[key] += report[key]
            counts["excluded_aligned_groups"] += scoring.aligned_counts[mid]
            if group_outside:
                counts["outside_reference_groups"] += report["outside_reference_groups"]
            else:
                counts["outside_reference_groups"] = None
            for threshold, hits in report["matched_reference_groups"].items():
                counts[f"reference_groups_found_{threshold}_A"] += hits
            counts.update(rate_totals(scoring.references[mid], matched))
            for source, cost in (("physical_pes_calls", "physical_pes_calls"),
                                 ("total_wall_s", "reported_search_wall_s")):
                values = [row[source] for row in group if row[source] is not None]
                counts[f"known_{cost}"] += sum(values)
                counts[f"missing_{cost}"] += len(group) - len(values) + (
                    len(added) if source == "physical_pes_calls" else 0
                )

    results = []
    for method, counts in sorted(totals.items()):
        found = counts["reference_groups_found_0.20_A"]
        eligible = counts["eligible_reference_groups"]
        counts["known_motif_generation_wall_s"] += 0.
        counts["missing_motif_generation_times"] += 0
        results.append({
            "dataset": scoring.datasets[parent], "parent_id": parent, "method_id": method,
            "geometry_source": geometry_source, **counts,
            "convergence_rate": counts["converged"] / counts["attempts"],
            "recall_0.20_A": found / eligible if eligible else None,
            "total_benchmark_work_s": (
                counts["known_reported_search_wall_s"] + counts["known_motif_generation_wall_s"]
                if not (counts["missing_reported_search_wall_s"] or counts["missing_motif_generation_times"])
                else None
            ),
            "physical_pes_calls_per_reference_group_found": (
                counts["known_physical_pes_calls"] / found
                if found and not counts["missing_physical_pes_calls"] else None
            ),
        })
        if include_matches:
            results[-1]["_matches"] = matches[method]
            results[-1]["_timeline"] = timelines[method]
    return results


def main(argv):
    parser = argparse.ArgumentParser(prog="saddlebench baseline")
    parser.add_argument("--attempts", type=Path, help="Bundled baseline Parquet table (default fetches pinned export)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--html", type=Path, help="Also write an interactive HTML scoreboard")
    parser.add_argument("--cache-dir", type=Path)
    add_dataset_argument(parser, default="literature")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--search-geometry-dir", type=Path, help="Use local exact Search shards")
    source.add_argument("--geometry-zip", type=Path, help="Use the exact Search trajectory ZIP")
    parser.add_argument("--workers", type=int, default=1, help="Score independent structures in parallel")
    parser.add_argument("--recall-only", action="store_true", help="Skip grouping unmatched predictions")
    parser.add_argument("--include-unconfident", action="store_true", help="Also score prefilled seeds below confidence")
    motif = parser.add_mutually_exclusive_group()
    motif.add_argument("--motif-dir", type=Path, help="Override published proposals with METHOD_ID/CASE_ID.traj files")
    motif.add_argument("--no-motif", action="store_true", help="Score only the two original methods")
    parser.add_argument("--explorer-dir", type=Path, help=argparse.SUPPRESS)  # local explorer payloads (testing)
    args = parser.parse_args(argv)
    if args.html and (args.html.exists() or args.html.resolve() == args.output.resolve()):
        parser.error("--html must be a new path distinct from --output")
    if args.workers < 1:
        parser.error("--workers must be positive")
    start = perf_counter()
    dataset = UmaDataset(args.cache_dir)
    scoring = UmaScoring(dataset, include_unconfident=args.include_unconfident)
    check_dataset(parser, scoring, args.dataset)
    if args.motif_dir and not args.motif_dir.is_dir():
        parser.error("--motif-dir must be an existing directory")
    if not args.no_motif and args.motif_dir is None:
        bundled = (args.attempts.parent if args.attempts else Path("benchmarks/uma")) / "motif"
        if bundled.is_dir():
            args.motif_dir = bundled
        else:
            cases = {seed["case_id"] for seed in select_cases(scoring, args.dataset)}
            args.motif_dir = default_motif_dir(args.cache_dir, cases)
    if args.motif_dir and not (args.geometry_zip or args.search_geometry_dir):
        bundled_archive = args.motif_dir / "exact-search.zip"
        if bundled_archive.is_file():
            args.geometry_zip = bundled_archive
    search = (BenchmarkSearchFrames(args.cache_dir, directory=args.search_geometry_dir)
              if args.search_geometry_dir else None)
    results = list(summarize(
        scoring, load_attempts(dataset, args.attempts), search, args.workers,
        group_outside=not args.recall_only, dataset=args.dataset, archive=args.geometry_zip,
        motif_dir=args.motif_dir, include_matches=bool(args.html),
    ))
    if not results:
        parser.error(f"no eligible seeds with prefilled attempts in {args.dataset}; use saddlebench score --dataset")
    matched = {(mid, row["method_id"]): matches
               for row in results for mid, matches in row.pop("_matches", {}).items()}
    timelines = {(row["parent_id"], row["method_id"]): row.pop("_timeline")
                 for row in results} if args.html else {}
    with args.output.open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    if args.html:
        write_scoreboard(args.html, scoring, results, dataset=args.dataset, baseline=True,
                         matched=matched, timelines=timelines, cache_dir=args.cache_dir,
                         explorer_payloads=args.explorer_dir)
    total = Counter()
    for row in results:
        if row["method_id"] in AUGMENTED.values():
            continue
        for column in ("attempts", "converged", "predictions_scored", "converged_without_exported_geometry"):
            total[column] += row[column]
    print(f"UMA {REVISION}, {args.dataset}, {results[0]['geometry_source']}: {dict(total)}, "
          f"{len(results)} structure-method rows; "
          f"scorer elapsed time {perf_counter() - start:.2f} s; {args.output}")
    return 0


def _code_digest():
    """Hash of the scoring code, so cached comparisons are recomputed after any scorer change."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted([root / "uma_baseline.py", root / "uma_metrics.py", root / "scoring" / "uma.py",
                        *(root / "matching").glob("*.py"), root / "reference" / "uma_motif.json"]):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def default_comparison(scoring, mids, *, cache_dir=None, attempts_path=None, workers=1):
    """The published four-method baseline restricted to these seeds, shown beside a user's submission.

    Uses the bundled attempt table when run from a checkout (benchmarks/uma/attempts.parquet), otherwise the pinned
    public table, and the published SaddleMotif proposals. The result depends only on published data and this code,
    so it is cached per seed set. Returns (rows, matched, timelines); all empty when no seed has prefilled attempts."""
    if attempts_path is None:
        bundled = Path("benchmarks/uma/attempts.parquet")
        attempts_path = bundled if bundled.is_file() else None
    attempts = load_attempts(scoring.dataset, attempts_path)
    cohort = set(attempts.column("minimum_id").to_pylist()) & set(mids)
    if not cohort:
        return [], {}, {}
    key = hashlib.sha256(json.dumps([scoring.revision_digest, _code_digest(), sorted(cohort),
                                     hashlib.sha256(attempts_path.read_bytes()).hexdigest() if attempts_path else COHORT],
                                    ).encode()).hexdigest()
    cache = Path(cache_dir or HF_HUB_CACHE) / "saddlebench-uma-comparison" / f"{key}.json"
    if cache.is_file():
        data = json.loads(cache.read_text())
        return (data["rows"], {tuple(k): v for k, v in data["matched"]},
                {tuple(k): v for k, v in data["timelines"]})
    cases = {scoring.seeds[mid]["case_id"] for mid in cohort}
    motif_dir = default_motif_dir(cache_dir, cases)
    results = list(summarize(scoring, attempts, None, workers, group_outside=False,
                             motif_dir=motif_dir, include_matches=True, seeds=cohort))
    matched = {(mid, row["method_id"]): found
               for row in results for mid, found in row.pop("_matches", {}).items()}
    timelines = {(row["parent_id"], row["method_id"]): row.pop("_timeline") for row in results}
    cache.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=cache.parent, delete=False, suffix=".tmp") as temp:
        json.dump({"rows": results, "matched": [[list(k), v] for k, v in matched.items()],
                   "timelines": [[list(k), v] for k, v in timelines.items()]}, temp)
    os.replace(temp.name, cache)
    return results, matched, timelines
