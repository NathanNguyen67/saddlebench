"""Generate per-case SaddleMotif proposals from each method's exact converged Search frames."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from time import perf_counter
from zipfile import ZipFile

from ase.io import write
from ase.io.trajectory import Trajectory

from saddlebench.reference.uma import UmaDataset, confidence_sources
from saddlebench.uma_baseline import load_attempts, METHODS


def generate_arm(arm, *, source, archive, output, seed_paths):
    (case, method), rows = arm
    destination = output / method / f"{case}.traj"
    if destination.exists():
        return f"{case} {method} already complete"
    work = Path(mkdtemp(prefix=f"{case}-{method}-", dir=output / "work"))
    converged = [row for row in rows if row["converged"]]
    events = []
    seen = set()
    with ZipFile(archive) as bundle, Trajectory(BytesIO(bundle.read(f"{case}/{method}.traj"))) as frames:
        if len(frames) != len(converged):
            raise ValueError(f"{case} {method} archive frame count differs from converged attempts")
        for row, atoms in zip(converged, frames):
            key = row["saddle_id"] or row["seq"]
            if key in seen:
                continue
            seen.add(key)
            path = f'saddle-{row["seq"]}.traj'
            write(work / path, atoms)
            events.append({"id": f'seq-{row["seq"]}', "minimum": str(seed_paths[case]), "saddle": path})
    summary = {
        "case_id": case, "method_id": method, "attempts": len(rows),
        "converged": len(converged), "source_saddles": len(events), "proposals": 0,
    }
    manifest = work / "events.json"
    manifest.write_text(json.dumps({"events": events}, indent=2))
    started = perf_counter()
    staged = work / "proposals.traj"
    if events:
        log_path = work / "saddlemotif.log"
        with log_path.open("w") as log:
            result = subprocess.run(
                [sys.executable, str(source / "scripts/run_generic_workflow.py"),
                 "--manifest", str(manifest), "--target", str(seed_paths[case]),
                 "--out", str(work / "generated")],
                stdout=log, stderr=subprocess.STDOUT,
            )
        if result.returncode:
            tail = log_path.read_text().splitlines()[-1:]
            if not tail or "NO_ELIGIBLE_REFERENCE_REACTIONS" not in tail[0]:
                result.check_returncode()
            summary["reason"] = "No source saddle met SaddleMotif's significant-motion rule"
        else:
            collated = work / "generated/COLLATED/ALL_GUESSES_BY_TAG1.traj"
            with Trajectory(collated) as predictions, Trajectory(staged, "w") as output_traj:
                # The native SaddleMotif collated file starts with the target minimum.
                if len(predictions) == 0:
                    raise ValueError(f"SaddleMotif omitted the target frame for {case} {method}")
                for index in range(1, len(predictions)):
                    output_traj.write(predictions[index])
                summary["proposals"] = len(predictions) - 1
    if not staged.exists():
        staged.touch()
    summary["generation_wall_s"] = perf_counter() - started
    (work / "result.json").write_text(json.dumps(summary, indent=2))
    destination.parent.mkdir(exist_ok=True)
    os.link(staged, destination)
    return json.dumps(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saddlemotif", required=True, type=Path, help="SaddleMotif source directory")
    parser.add_argument("--geometry-zip", required=True, type=Path)
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--case-id", action="append", help="Process only these benchmark cases")
    parser.add_argument("--exclude-case-id", action="append", default=[], help="Skip a case being generated separately")
    parser.add_argument("--include-unconfident", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    source, archive, output = (path.resolve() for path in
                               (args.saddlemotif, args.geometry_zip, args.output_dir))
    (output / "work").mkdir(parents=True, exist_ok=True)
    seeds_dir = output / "seeds"
    seeds_dir.mkdir(exist_ok=True)
    data = UmaDataset()
    seeds = {row["minimum_id"]: row for row in data.table("seeds").to_pylist()}
    arms = defaultdict(list)
    for row in sorted(load_attempts(data, args.attempts).to_pylist(), key=lambda row: row["seq"]):
        seed = seeds[row["minimum_id"]]
        case = seed["case_id"]
        if case in args.exclude_case_id:
            continue
        if not args.include_unconfident and not confidence_sources(seed):
            continue
        if args.case_id and case not in args.case_id:
            continue
        arms[case, row["method_id"]].append(row)
    cases = {case for case, _ in arms}
    if not arms or (args.case_id and cases != set(args.case_id)):
        parser.error("requested cases are outside this benchmark selection")
    if any(len(rows) != 500 or method not in METHODS for (_, method), rows in arms.items()):
        raise ValueError("Expected 500 attempts for each supplied baseline case and method")

    seed_paths = {case: seeds_dir / f"{case}.traj" for case in cases}
    missing = {mid: seed for mid, seed in seeds.items()
               if seed["case_id"] in seed_paths and not seed_paths[seed["case_id"]].exists()}
    for mid, atoms in data.frames(missing):
        write(seed_paths[missing[mid]["case_id"]], atoms)
    run = partial(generate_arm, source=source, archive=archive, output=output, seed_paths=seed_paths)
    def event_count(arm):
        return len({row["saddle_id"] or row["seq"] for row in arm[1] if row["converged"]})

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(run, arm): arm[0] for arm in sorted(
            arms.items(), key=event_count, reverse=True
        )}
        failures = []
        for task in as_completed(tasks):
            try:
                print(task.result(), flush=True)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                failures.append(tasks[task])
                print(f"FAILED {tasks[task]}: {error}", file=sys.stderr, flush=True)
    if failures:
        raise SystemExit(f"Failed SaddleMotif arms: {failures}")


if __name__ == "__main__":
    main()
