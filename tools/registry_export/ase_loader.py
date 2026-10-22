"""A minimal read-only, provenance-preserving ASE loader for exported triplets.

Usage (installed with SaddleBench checkout):

    from tools.registry_export.ase_loader import GeometryIndex, to_ase_atoms, load_mode
    with GeometryIndex('/path/to/evidence_unqualified/geometries.jsonl') as ix:
        minimum_A = to_ase_atoms(ix.get(triplet['geometry']['minimum_A']['geometry_id']))

Note: chemical direction and Hessian-mode equivalence are NOT inferred.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class GeometryIndex:
    """Disk-backed lookup. Index rebuilt in /tmp and deleted on close."""
    def __init__(self, geometry_jsonl):
        self.source = Path(geometry_jsonl)
        self._tmp = tempfile.TemporaryDirectory(prefix='sbe_geometry_index_')
        self._db = sqlite3.connect(Path(self._tmp.name)/'index.sqlite')
        self._db.execute('CREATE TABLE geometry(id TEXT PRIMARY KEY, row TEXT)')
        with self.source.open(encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    r=json.loads(line)
                    self._db.execute('INSERT INTO geometry VALUES (?,?)',(r['geometry_id'],line))
        self._db.commit()

    def get(self, geometry_id):
        result=self._db.execute('SELECT row FROM geometry WHERE id=?',(geometry_id,)).fetchone()
        if not result:
            raise KeyError(geometry_id)
        return json.loads(result[0])

    def close(self):
        self._db.close()
        self._tmp.cleanup()

    def __enter__(self):return self
    def __exit__(self, exc_type, exc, tb):self.close()


def to_ase_atoms(row):
    from ase import Atoms
    from ase.constraints import FixAtoms
    atoms=Atoms(numbers=row['ordered_numbers'], positions=row['positions_angstrom'],
                cell=row['cell_angstrom'], pbc=row['pbc'])
    if row.get('constraints_known') is True:
        constraints=row.get('constraints') or []
        if any(x.get('type') != 'FixAtoms' for x in constraints):
            raise ValueError('UNSUPPORTED_CONSTRAINT_TYPE')
        indices=row.get('fixed_indices') or []
        if indices:atoms.set_constraint(FixAtoms(indices=indices))
    else:
        atoms.info['constraints_known'] = False
        atoms.info['export_uncertainty_reasons'] = row.get('uncertainty_reasons') or []
    atoms.info['source_geometry_id'] = row['geometry_id']
    return atoms


def load_mode(science_directory, mode_row):
    """Read the native stored array; caller must inspect mode_row['mode'] status."""
    import numpy as np
    pointer=mode_row.get('mode_array')
    if pointer is None:
        raise ValueError('MODE_ARRAY_UNAVAILABLE')
    root=Path(science_directory).resolve()
    relative=Path(pointer['file'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('UNSAFE_ARRAY_PATH')
    path=root/relative
    with np.load(path, allow_pickle=False) as z:
        arr=z[pointer['key']].copy()
    if _sha(arr.tobytes(order='C')) != pointer['sha256_float_array']:
        raise ValueError('MODE_ARRAY_SHA256_MISMATCH')
    return arr
