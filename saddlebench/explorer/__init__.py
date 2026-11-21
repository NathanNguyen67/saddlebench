"""Offline structure explorer for scored UMA cases.

The viewer and its page builder are vendored, unmodified, from SaddleMotif Explorer v2 (see vendor/VENDOR.json and
scripts/vendor_explorer.py). SaddleBench supplies only data the explorer already understands:

* structure payloads (schema ``saddlemotif-explorer-v2``) built by SaddleMotif from the same campaign database as the
  pinned UMA export, fetched and checksum-verified from a release manifest (``reference/uma_explorer.json``);
* a per-structure score overlay: reference groups (keyed by saddle ID) and, for each method and cutoff, the groups
  found. The scorer decides identity by geometry; the overlay only names the groups it matched. When
  ``reference/uma_symmetry.json`` matches the scorer's reference digest, reference groups also carry their symmetry
  family (saddles mapped onto each other by a symmetry of the seed) and a near-null flag. These are labels only.

A SaddleMotif viewer change therefore needs only a re-vendor, and a payload rebuild only a new manifest.
"""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import gzip
import hashlib
from importlib.resources import files
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from huggingface_hub.constants import HF_HUB_CACHE, HF_HUB_OFFLINE

from saddlebench.reference.uma import REVISION

VENDOR = Path(__file__).resolve().parent / "vendor"
SCHEMA = "saddlemotif-explorer-v2"


def _builder():
    """The vendored build_index module (stdlib only)."""
    name = "saddlebench_explorer_build_index"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, VENDOR / "build" / "build_index.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def slug(parent):
    return _builder().slug(parent)


def manifest():
    path = files("saddlebench.reference").joinpath("uma_explorer.json")
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if data["hf_revision"] != REVISION:
        raise ValueError("Published explorer payloads target a different UMA reference revision")
    return data


def literature(parent):
    """Literature notes for a lit_* structure, keyed by its family code (B01, C06, ...), or None."""
    table = json.loads(files("saddlebench.reference").joinpath("uma_literature.json").read_text())
    match = re.search(r":([BCLO]\d\d)", parent)
    if not match or match.group(1) not in table:
        return None
    code = match.group(1)
    entry = dict(table[code])
    name = parent.split(":", 1)[1]
    for prefix, override in table.get("_variants", {}).items():
        if name.startswith(prefix):
            entry.update(override)
    entry["title"] = f"{code} · {entry['system']}" if entry.get("system") else code
    entry["source"] = table.get("_source")
    if name != code:
        entry["source"] = f"{entry['source']} This seed: {name}."
    return {key: value for key, value in entry.items() if value}


def _verify(path, digest):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest() == digest


def _fetch(record, *, revision, directory):
    output = directory / record["path"].rsplit("/", 1)[-1]
    if output.is_file() and _verify(output, record["sha256"]):
        return
    if HF_HUB_OFFLINE:
        raise FileNotFoundError(f"Explorer payload missing from the offline cache: {output.name}")
    url = f"https://raw.githubusercontent.com/NathanNguyen67/saddlebench/{revision}/{record['path']}"
    for attempt in range(3):
        temp = None
        try:
            with NamedTemporaryFile(dir=directory, prefix=".download-", delete=False) as staging, \
                    urlopen(url, timeout=60) as response:
                temp = Path(staging.name)
                for block in iter(lambda: response.read(1 << 20), b""):
                    staging.write(block)
            if not _verify(temp, record["sha256"]):
                raise ValueError(f"Explorer payload checksum mismatch for {output.name}")
            os.replace(temp, output)
            return
        except (OSError, ValueError):
            if attempt == 2:
                raise
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def payload_paths(parents, *, cache_dir=None, directory=None):
    """parent -> local gzipped payload. directory overrides the published payloads (files named by slug)."""
    parents = sorted(set(parents))
    if directory is not None:
        found = {}
        for parent in parents:
            for name in (f"{slug(parent)}.json.gz", re.sub(r"[^A-Za-z0-9_.-]", "~", parent) + ".json.gz"):
                if (directory / name).is_file():
                    found[parent] = directory / name
                    break
        return found
    data = manifest()
    if data is None:
        return {}
    records = [data["payloads"][parent] for parent in parents if parent in data["payloads"]]
    target = Path(cache_dir or HF_HUB_CACHE) / "saddlebench-uma-explorer" / data["data_revision"]
    target.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(partial(_fetch, revision=data["data_revision"], directory=target), records))
    return {parent: target / data["payloads"][parent]["path"].rsplit("/", 1)[-1]
            for parent in parents if parent in data["payloads"]}


def symmetry(scoring):
    """Symmetry families and near-null products per seed (build_uma_symmetry.py), or {} when absent or stale."""
    path = files("saddlebench.reference").joinpath("uma_symmetry.json")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    if data.get("hf_revision") != REVISION or data.get("reference_digest") != scoring.revision_digest:
        return {}
    return data["seeds"]


def score_overlay(scoring, parent, seeds, matched, labels, payload, families=None):
    """Reference groups of the structure's scored seeds, aligned-slide exclusions, and found groups per method.

    families: symmetry() output; adds family/via/nearNull to reference groups (display only, scores unchanged)."""
    reference, excluded = {}, {}
    families = {} if families is None else families
    for mid in seeds:
        info = families.get(mid, {})
        family_of = {sid: members[0] for members in info.get("families", ()) for sid in members}
        operations = info.get("operations", [])
        for sid, row in scoring.references[mid].items():
            entry = {"barrier": round(row["barrier_eV"], 4)}
            if sid in family_of:
                entry["family"] = family_of[sid]
                link = (info.get("links", {}).get(sid) or [None])[0]
                if link is not None and link["operation"] < len(operations):
                    entry["via"] = operations[link["operation"]]["label"]
            if sid in info.get("near_null", {}):
                entry["nearNull"] = info["near_null"][sid]
            reference[sid] = entry
        for sid in scoring.aligned_ids.get(mid, ()):
            excluded[sid] = "Aligned slide, excluded from the benchmark"
    found = {}
    for (mid, method), by_cutoff in matched.items():
        if mid not in seeds:
            continue
        slot = found.setdefault(method, {})
        for cutoff, ids in by_cutoff.items():
            slot.setdefault(cutoff, set()).update(ids)
    present = {reaction["saddle"] for reaction in payload["reactions"]}
    absent = sorted(set(reference) - present)
    methods = [method for method in labels if method in found]
    notes = None
    if absent:
        notes = f"{len(absent)} reference saddles have no explorer reaction and are counted in the totals only."
    return {
        "methods": [{"id": method, "label": labels[method]} for method in methods],
        "cutoffs": ["0.20", "0.10", "0.05"],
        "seeds": sorted(seeds),
        "reference": reference,
        "excluded": excluded,
        "found": {method: {cutoff: sorted(ids) for cutoff, ids in found[method].items()} for method in methods},
        "absent": absent,
        "notes": notes,
    }


def write_explorer(directory, scoring, matched, labels, *, cache_dir=None, payload_dir=None, scoreboard_href=None):
    """Write directory/index.html and one page per scored structure. Returns parent -> page file name.

    matched maps (minimum_id, method_id) -> {cutoff: [reference saddle IDs found]}. Structures without a published
    payload are skipped (the scoreboard keeps their rows)."""
    seeds_by_parent = {}
    for mid, _ in matched:
        seeds_by_parent.setdefault(scoring.seeds[mid]["parent_id"], set()).add(mid)
    paths = payload_paths(seeds_by_parent, cache_dir=cache_dir, directory=payload_dir)
    if not paths:
        return {}
    builder = _builder()
    families = symmetry(scoring)
    raws, lits, scores = [], {}, {}
    for parent, path in sorted(paths.items()):
        raw = path.read_bytes()
        payload = json.loads(gzip.decompress(raw))
        if payload.get("schema") != SCHEMA or payload["structure"]["parent"] != parent:
            raise ValueError(f"Unexpected explorer payload for {parent}: {path}")
        raws.append(raw)
        scores[parent] = score_overlay(scoring, parent, seeds_by_parent[parent], matched, labels, payload, families)
        lit = literature(parent)
        if lit:
            lits[parent] = lit
    methods = [{"id": method, "label": label} for method, label in labels.items()
               if any(method in score["found"] for score in scores.values())]
    links = {"scoreboard": scoreboard_href} if scoreboard_href else None
    rows = builder.build_site(
        raws, Path(directory), mode="bench", lits=lits, scores=scores, methods=methods, links=links,
        title="Scored structures",
        lede="Each card animates a representative reaction on the structure's starting minimum. "
             "Open a structure to see its saddle classes, with reference saddles marked found or missed.",
        note="Identity is geometric (core-drift maximum deviation). DoubleMin endpoints and Hessian order are "
             "unverified; curvature is along the final Search mode.")
    return {row["parent"]: f"{row['slug']}.html" for row in rows}
