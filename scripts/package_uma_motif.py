"""Package verified UMA motif proposals as compressed, metadata-free geometry shards."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import gzip
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms
from ase.io.trajectory import Trajectory

from saddlebench.reference.uma import REVISION
from saddlebench.uma_baseline import METHODS, motif_generation_times

PART_BYTES = 48 * 1024 * 1024


def package_trajectory(source, directory, method, case):
    name = f"{method}/{case}.traj"
    (directory / method).mkdir(exist_ok=True)
    parts = []
    digest = hashlib.sha256()
    with TemporaryDirectory(dir=directory) as scratch:
        sanitized = Path(scratch) / "geometry.traj"
        frames = 0
        if source.stat().st_size:
            with Trajectory(source) as original, Trajectory(sanitized, "w") as clean:
                for frame in original:
                    # Publish only geometry accepted by the scorer. Proposal metadata can
                    # carry local filenames and is not required to reproduce scores.
                    clean.write(Atoms(numbers=frame.numbers, positions=frame.positions,
                                      cell=frame.cell, pbc=frame.pbc))
                    frames += 1
        sanitized.touch(exist_ok=True)
        with sanitized.open("rb") as stream:
            for number in range(1, (sanitized.stat().st_size // PART_BYTES) + 2):
                block = stream.read(PART_BYTES)
                if not block and parts:
                    break
                suffix = ".gz" if not parts else f".{number}.gz"
                part = f"{name}{suffix}"
                digest.update(block)
                with (directory / part).open("xb") as handle:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=handle,
                                       compresslevel=3, mtime=0) as output:
                        output.write(block)
                parts.append(part)
    return {"method_id": method, "case_id": case, "frames": frames,
            "sha256": digest.hexdigest(), "parts": parts}


def source_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_arm(arm, *, proposals, output):
    method, case = arm
    source = proposals / method / f"{case}.traj"
    prepacked = output.parent / "prepacked"
    index = prepacked / method / f"{case}.json"
    if index.is_file():
        record = json.loads(index.read_text())
        if source_sha256(source) == record.pop("source_sha256"):
            (output / method).mkdir(exist_ok=True)
            for part in record["parts"]:
                os.link(prepacked / part, output / part)
            return record
    return package_trajectory(source, output, method, case)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposals", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = set.intersection(*({path.stem for path in (args.proposals / method).glob("case*.traj")}
                               for method in METHODS))
    if len(cases) != 36:
        raise ValueError(f"Expected 36 paired benchmark cases, found {len(cases)}")
    times = motif_generation_times(args.proposals)
    records = []
    arms = [(method, case) for method in METHODS for case in sorted(cases)]
    pack = partial(package_arm, proposals=args.proposals, output=args.output)
    workers = min(4, max(1, int(os.environ.get("SLURM_CPUS_PER_TASK", "1"))))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for record in pool.map(pack, arms):
            case, method = record["case_id"], record["method_id"]
            record["generation_wall_s"] = times.get((case, method))
            records.append(record)
            print(case, method, record["frames"], flush=True)
    path = args.output / "proposals-index.json"
    with path.open("x") as output:
        json.dump({"hf_revision": REVISION, "proposals": records}, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
