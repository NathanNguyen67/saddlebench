"""Local, derived geometry arrays for verified UMA inputs."""
from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

from huggingface_hub.constants import HF_HUB_CACHE
import numpy as np


def geometry(atoms, *, arrays=False):
    return {
        "numbers": atoms.numbers.tolist(),
        "pbc": atoms.pbc.tolist(),
        "cell_angstrom": atoms.cell.array.copy() if arrays else atoms.cell.array.tolist(),
        "positions_angstrom": atoms.get_positions() if arrays else atoms.positions.tolist(),
        "fixed_indices": [],
        "constraints_known": False,
    }


def cache_path(cache_dir, kind, input_digest, scope):
    digest = sha256(f"v1\0{kind}\0{input_digest}\0{scope}".encode()).hexdigest()
    return Path(cache_dir or HF_HUB_CACHE) / "saddlebench-uma-arrays" / f"{kind}-{digest}.npz"


def load(path, expected=None):
    try:
        with np.load(path, allow_pickle=False) as arr:
            ids = arr["ids"].tolist()
            if expected is not None and ids != expected:
                return None
            positions, numbers, offsets = arr["positions"], arr["numbers"], arr["offsets"]
            cells, pbc = arr["cells"], arr["pbc"]
            records = {
                ident: {
                    "positions_angstrom": positions[lo:hi],
                    "numbers": numbers[lo:hi].tolist(),
                    "cell_angstrom": cell,
                    "pbc": periodic.tolist(),
                    "fixed_indices": [],
                    "constraints_known": False,
                }
                for ident, lo, hi, cell, periodic in zip(ids, offsets[:-1], offsets[1:], cells, pbc)
            }
            if len(records) != len(ids):
                return None
            profile = {key.removeprefix("profile_"): arr[key]
                       for key in arr.files if key.startswith("profile_")}
        return records, profile
    except (OSError, ValueError, KeyError, BadZipFile, EOFError):
        return None


def save(path, records, profile=None):
    if not records:
        return
    rows = list(records.values())
    arrays = {
        "ids": np.asarray(list(records)),
        "positions": np.concatenate([r["positions_angstrom"] for r in rows]),
        "numbers": np.concatenate([r["numbers"] for r in rows]),
        "offsets": np.r_[0, np.cumsum([len(r["numbers"]) for r in rows])],
        "cells": np.asarray([r["cell_angstrom"] for r in rows]),
        "pbc": np.asarray([r["pbc"] for r in rows]),
    }
    arrays.update({f"profile_{key}": values for key, values in (profile or {}).items()})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix=".stage-") as temporary:
            np.savez(temporary, **arrays)
            temporary.flush()
            try:
                os.link(temporary.name, path)
            except FileExistsError:
                pass
    except OSError:
        # A shared or read-only cache can still score from the source files.
        pass
