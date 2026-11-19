"""Read the pinned public SaddleBench-UMA export."""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
from ase.io.trajectory import Trajectory
from huggingface_hub import hf_hub_download

REPO = "traingletrain/saddlebench-uma"
REVISION = "acdac15b98282a9137a76c060913fcc0ce08462b"


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
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest() if hasattr(
                    hashlib, "file_digest") else _sha256(handle)
            if actual != self.manifest["sha256"][name]:
                raise ValueError(f"UMA manifest checksum mismatch for {name}: {path}")
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


def _sha256(handle):
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def select_endpoint_pair(pairs, minimum_id):
    """Return an original pair row, including its exported observation counts."""
    def order(pair):
        connected = any(seed["minimum_id"] == minimum_id for seed in pair["seed_barriers"])
        return (not connected, pair["energy_a_eV"] + pair["energy_b_eV"],
                pair["minimum_a"], pair["minimum_b"])

    return min(pairs, key=order, default=None)
