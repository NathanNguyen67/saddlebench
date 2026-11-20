"""Read the pinned public SaddleBench-UMA export."""
import csv
import gzip
from io import BytesIO
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
from ase.io.trajectory import Trajectory
from huggingface_hub import hf_hub_download

REPO = "traingletrain/saddlebench-uma"
REVISION = "acdac15b98282a9137a76c060913fcc0ce08462b"
SEARCH_REPO = "traingletrain/saddlebench-uma-benchmark-search"
SEARCH_REVISION = None


class UmaDataset:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.verified = {}
        self.manifest = json.loads(self.download("MANIFEST.json").read_text())
        with self.file("cases.csv").open(newline="") as handle:
            self.cases = {row["case_id"]: row for row in csv.DictReader(handle)}

    def download(self, name):
        return Path(hf_hub_download(REPO, name, repo_type="dataset",
                                    revision=REVISION, cache_dir=self.cache_dir))

    def file(self, name):
        if name not in self.verified:
            path = self.download(name)
            verify_file(path, self.manifest["sha256"][name], name)
            self.verified[name] = path
        return self.verified[name]

    def table(self, name, **options):
        return pq.read_table(self.file(f"{name}.parquet"), **options)

    def frames(self, records):
        by_path = defaultdict(dict)
        for ident, row in records.items():
            by_path[row["geometry_path"]][ident] = row["geometry_index"]
        for path, indices in by_path.items():
            with Trajectory(self.file(path)) as trajectory:
                for ident, index in sorted(indices.items(), key=lambda item: item[1]):
                    atoms = trajectory[index]
                    if atoms.info.get("saddlebench_id") != ident:
                        raise ValueError(f"UMA geometry pointer mismatch for {ident}")
                    yield ident, atoms


def verify_file(path, expected, name):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"UMA manifest checksum mismatch for {name}: {path}")


class BenchmarkSearchFrames:
    """Exact Search geometries published separately from the answer key."""
    def __init__(self, cache_dir=None, *, directory=None):
        self.cache_dir = cache_dir
        self.directory = directory
        self.manifest = json.loads(self.download("MANIFEST.json").read_text())
        if self.manifest["source_hf_revision"] != REVISION:
            raise ValueError("Benchmark Search supplement targets a different UMA revision")

    def download(self, name):
        if self.directory is not None:
            return self.directory / name
        if SEARCH_REVISION is None:
            raise ValueError("Benchmark Search supplement awaiting publication. Use --search-geometry-dir.")
        return Path(hf_hub_download(SEARCH_REPO, name, repo_type="dataset",
                                    revision=SEARCH_REVISION, cache_dir=self.cache_dir))

    def frames(self, parent):
        info = self.manifest["structures"][parent]
        name = info["shard"]
        path = self.download(name)
        compressed = path.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != info["sha256"]:
            raise ValueError(f"UMA manifest checksum mismatch for {name}: {path}")
        with Trajectory(BytesIO(gzip.decompress(compressed))) as shard:
            for atoms in shard:
                yield atoms.info["saddlebench_attempt_seq"], atoms


def select_endpoint_pair(pairs, minimum_id):
    """Return an original pair row, including its exported observation counts."""
    def order(pair):
        connected = any(seed["minimum_id"] == minimum_id for seed in pair["seed_barriers"])
        return (not connected, pair["energy_a_eV"] + pair["energy_b_eV"],
                pair["minimum_a"], pair["minimum_b"])

    return min(pairs, key=order, default=None)
