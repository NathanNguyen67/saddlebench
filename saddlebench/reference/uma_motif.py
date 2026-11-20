"""Retrieve exact cached SaddleMotif proposal frames pinned by a release manifest."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import gzip
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from huggingface_hub.constants import HF_HUB_CACHE, HF_HUB_OFFLINE

from saddlebench.reference.uma import REVISION, verify_file


def cache_proposals(record, *, revision, directory):
    method, case = record["method_id"], record["case_id"]
    name = f"{method}/{case}.traj"
    output = directory / name
    if output.is_file():
        try:
            verify_file(output, record["sha256"], name)
            return
        except ValueError:
            if HF_HUB_OFFLINE:
                raise
    if HF_HUB_OFFLINE:
        raise FileNotFoundError(f"SaddleMotif proposal missing from the offline cache: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/NathanNguyen67/saddlebench/{revision}"
    parts = record.get("parts", [f"{name}.gz"])
    for attempt in range(3):
        temp = None
        try:
            with NamedTemporaryFile(dir=output.parent, prefix=".download-", delete=False) as staging:
                temp = Path(staging.name)
                digest = hashlib.sha256()
                for part in parts:
                    with urlopen(f"{base}/{part}", timeout=45) as response, gzip.GzipFile(fileobj=response) as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
                            staging.write(block)
            if digest.hexdigest() != record["sha256"]:
                raise ValueError(f"SaddleMotif release checksum mismatch for {name}")
            os.replace(temp, output)
            return
        except (OSError, EOFError, ValueError):
            if attempt == 2:
                raise
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def default_motif_dir(cache_dir, cases):
    path = files("saddlebench.reference").joinpath("uma_motif.json")
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text())
    if manifest["hf_revision"] != REVISION:
        raise ValueError("Published motif data target a different UMA reference revision")
    revision = manifest["data_revision"]
    proposals = [row for row in manifest["proposals"] if row["case_id"] in cases]
    if not proposals:
        return None
    directory = Path(cache_dir or HF_HUB_CACHE) / "saddlebench-uma-proposals" / revision
    directory.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(partial(cache_proposals, revision=revision, directory=directory), proposals))
    timings = [{key: row[key] for key in ("method_id", "case_id", "generation_wall_s")}
               for row in manifest["proposals"] if row.get("generation_wall_s") is not None]
    with NamedTemporaryFile(mode="w", dir=directory, encoding="utf-8", delete=False) as temp:
        json.dump(timings, temp)
    os.replace(temp.name, directory / "timings.json")
    return directory
