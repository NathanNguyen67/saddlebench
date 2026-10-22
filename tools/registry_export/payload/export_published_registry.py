#!/usr/bin/env python3
"""Immutable published-registry -> saddlemill-evidence-v1 (unqualified raw evidence).

Only reads source SQLite with mode=ro&immutable=1, and optional filesystem
artifacts. Never executes calculators or makes scientific eligibility decisions.
The output, including local spool DB, is new diagnostic state outside Pipeline.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import heapq
import shutil
from collections import Counter
import hashlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import zlib

import numpy as np

SCHEMA = 'saddlemill-evidence-v1'
VERSION = 'w1-pinned-source-20261012-v2-npz-source-fallback-parallel-rc14'
SOURCE_REPO = 'NathanNguyen67/saddlemill-workflow'
SOURCE_COMMIT = 'ba87a9e57e493fc1414fb74463aa33d9a84bc1e9'
DEFAULT_POINTER = Path('/scratch/11588/ndn454/sm_anskey/pipeline/state/latest_canonical_registry_v1.txt')
FILES = {
    'saddle_registry': 'saddles.jsonl',
    'reaction_observations': 'doublemin_observations.jsonl',
    'reaction_registry': 'reactions.jsonl',
    'canonical_minima': 'minima.jsonl',
}
EXTRA_FILES = ('geometries.jsonl', 'source_artifacts.jsonl')
REQUIRED = {
    'registry_meta': {'key', 'value'},
    'saddle_registry': {'canonical_saddle_id', 'structure_id', 'geometry_blob', 'geometry_sha256',
                        'energy_eV', 'hessian_status', 'reaction_eligible', 'source_result_path'},
    'saddle_group_members': {'canonical_saddle_id', 'saddle_group_id'},
    'reaction_observations': {'observation_id', 'canonical_saddle_id', 'structure_id',
                              'saddle_group_id', 'canonical_reaction_id', 'protocol',
                              'protocol_eligible', 'outcome', 'minus_path', 'minus_frame',
                              'plus_path', 'plus_frame', 'read_error', 'source_kind'},
    'reaction_registry': {'canonical_reaction_id', 'structure_id', 'saddle_group_id', 'status',
                          'connected_to_canonical_minimum', 'rate_relevant', 'barrier_eV'},
    'canonical_minima': {'minimum_id', 'parent_structure_id', 'geometry_blob',
                         'geometry_sha256', 'energy_eV', 'certification_status', 'object_path'},
    'canonical_event_application': {'event_id', 'event_sequence'},
    'canonical_event_publications': {'publication_id', 'published_through'},
}
OPTIONAL_TABLES = {
    'reaction_observation_provenance': ('observation_id',),
    'canonical_doublemin_protocol_provenance': ('observation_id',),
    'reaction_endpoint_state': ('observation_id',),
    'canonical_reaction_endpoint_state': ('canonical_reaction_id',),
    'canonical_minima_snapshot_members': ('snapshot_id', 'minimum_id'),
    'minimum_certification': ('minimum_id',),
    'incremental_occurrences': ('occurrence_key', 'canonical_saddle_id'),
}


def fail(message: str) -> None:
    raise ValueError(message)


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def stable_id(sha: str, category: str, key: str) -> str:
    return hashlib.sha256(f'{sha}|{category}|{key}'.encode('utf-8')).hexdigest()


def normalized(obj):
    if isinstance(obj, dict):
        return {str(k): normalized(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [normalized(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        obj = float(obj)
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, bytes):
        return {'blob_sha256': hashlib.sha256(obj).hexdigest(), 'blob_size_bytes': len(obj)}
    if isinstance(obj, (str, float, int, bool, type(None))):
        return obj
    return str(obj)


def jd(obj: dict) -> str:
    return json.dumps(normalized(obj), sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=False)


def q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def reject_symlink_ancestors(path: Path) -> None:
    """Reject every symlink component, including an alias to an input source.

    Used with descriptor-pinned output creation for defense against path swaps.
    """
    path = path.absolute()
    parts = path.parts
    walk = Path(parts[0])
    for part in parts[1:]:
        walk = walk / part
        if walk.is_symlink():
            fail('SYMLINK_ANCESTOR_UNTRUSTED:' + str(walk))


def pin_output_directory(out: Path, db: Path) -> tuple[int, int]:
    """Create output only through a pinned, no-follow parent directory fd.

    Once created, all generated files use the opened destination inode under
    /proc/self/fd, never a mutable caller-supplied path or ancestor symlink.
    """
    out = out.absolute()
    if out == out.parent:
        fail('INVALID_OUTPUT_DIRECTORY')
    reject_symlink_ancestors(out.parent)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in out.parent.parts[1:]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        pinned_parent = Path(os.readlink(f'/proc/self/fd/{fd}'))
        # Check the *physical opened directory*, not the original path alias.
        protected_output(pinned_parent / out.name, db)
        if out.exists() or out.is_symlink():
            raise FileExistsError('REFUSE_OVERWRITE')
        os.mkdir(out.name, dir_fd=fd)
        try:
            dfd = os.open(out.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except BaseException:
            os.rmdir(out.name, dir_fd=fd)
            raise
        return fd, dfd
    except BaseException:
        os.close(fd)
        raise


def verify_output_locator(out: Path, dfd: int):
    reject_symlink_ancestors(out)
    expected = os.fstat(dfd)
    actual = os.stat(out, follow_symlinks=False)
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        fail('OUTPUT_LOCATOR_CHANGED_DURING_EXPORT')


def pointer_target(pointer: Path) -> tuple[Path, bytes]:
    reject_symlink_ancestors(pointer)
    if pointer.is_symlink() or not pointer.is_file():
        fail('POINTER_NOT_REGULAR_OR_SYMLINK')
    raw = pointer.read_bytes()
    value = raw.decode('utf-8').strip()
    if not value or '\n' in value or '\r' in value:
        fail('INVALID_POINTER')
    p = Path(value)
    if not p.is_absolute():
        fail('POINTER_TARGET_MUST_BE_ABSOLUTE')
    return p, raw


def source_check(path: Path):
    reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file():
        fail('REGISTRY_NOT_REGULAR_OR_SYMLINK')
    if (Path(str(path) + '-wal').exists() or Path(str(path) + '-shm').exists()):
        fail('REFUSE_SQLITE_WAL_OR_SHM')
    with path.open('rb') as f:
        if f.read(16) != b'SQLite format 3\0':
            fail('INVALID_SQLITE_HEADER')


def protected_output(out: Path, db: Path) -> None:
    # Resolve parent(s) and do not follow a symlink at the final component.
    reject_symlink_ancestors(out)
    if out.is_symlink():
        fail('OUTPUT_SYMLINK')
    dest = out.resolve()
    for root in (db.parent, Path('/scratch/11588/ndn454/sm_anskey/pipeline'),
                 Path('/work/11588/ndn454/ls6/saddlemill_workflow'),
                 Path('/work/11588/ndn454/ls6/saddlemill-personal')):
        root = root.resolve()
        if dest == root or root in dest.parents:
            fail('UNSAFE_OUTPUT_PRODUCTION_TREE')
    if dest == db:
        fail('OUTPUT_EQUALS_INPUT')


def get_columns(con) -> dict[str, set[str]]:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = sorted(set(REQUIRED) - tables)
    if missing:
        fail('BLOCKED_REAL_SCHEMA:MISSING_TABLES:' + ','.join(missing))
    columns = {name: {r[1] for r in con.execute('PRAGMA table_info(' + q(name) + ')')}
               for name in tables}
    for table, req in {**REQUIRED, **{k: set(v) for k, v in OPTIONAL_TABLES.items() if k in tables}}.items():
        absent = sorted(req - columns[table])
        if absent:
            fail('BLOCKED_REAL_SCHEMA:MISSING_COLUMNS:' + table + ':' + ','.join(absent))
    return columns


def metadata(con):
    meta = dict(con.execute('SELECT key,value FROM registry_meta ORDER BY key'))
    if 'canonical_events_published_through' not in meta:
        fail('BLOCKED_PUBLICATION_WATERMARK:METADATA_MISSING')
    try:
        cutoff = int(meta['canonical_events_published_through'])
    except (ValueError, TypeError):
        fail('BLOCKED_PUBLICATION_WATERMARK:INVALID_METADATA')
    if cutoff < 0:
        fail('BLOCKED_PUBLICATION_WATERMARK:NEGATIVE')
    applied = con.execute('SELECT count(*),coalesce(max(event_sequence),0) FROM canonical_event_application').fetchone()
    if applied[1] > cutoff:
        fail('BLOCKED_PUBLICATION_WATERMARK:UNPUBLISHED_APPLICATION')
    pub = con.execute('SELECT published_through FROM canonical_event_publications ORDER BY published_through DESC LIMIT 1').fetchone()
    if pub is None and cutoff != 0:
        fail('BLOCKED_PUBLICATION_WATERMARK:NO_PUBLICATION_ROW')
    if pub and int(pub[0]) != cutoff:
        fail('BLOCKED_PUBLICATION_WATERMARK:INCONSISTENT_PUBLICATION_ROW')
    return meta, cutoff, int(applied[0]), int(applied[1])


def decode_geometry(blob):
    if not isinstance(blob, bytes):
        fail('NOT_GEOMETRY_BLOB')
    with np.load(io.BytesIO(zlib.decompress(blob)), allow_pickle=False) as arr:
        numbers = np.asarray(arr['numbers'], dtype=np.int16)
        positions = np.asarray(arr['positions'], dtype=np.float64)
        cell = np.asarray(arr['cell'], dtype=np.float64).reshape(3, 3)
        pbc = np.asarray(arr['pbc'], dtype=np.uint8)
    if numbers.ndim != 1 or len(numbers) == 0 or (numbers <= 0).any():
        fail('INVALID_ORDERED_NUMBERS')
    if positions.shape != (len(numbers), 3) or pbc.shape != (3,):
        fail('INVALID_GEOMETRY_SHAPE')
    if not np.isfinite(positions).all() or not np.isfinite(cell).all() or not np.isin(pbc, (0, 1)).all():
        fail('INVALID_GEOMETRY_VALUES')
    return numbers, positions, cell, pbc


def pipeline_geometry_sha(nums, xyz, cell, pbc):
    h = hashlib.sha256()
    h.update(np.asarray(nums, dtype=np.int16).tobytes(order='C'))
    h.update(np.round(np.asarray(xyz, dtype=np.float64), 10).tobytes(order='C'))
    h.update(np.round(np.asarray(cell, dtype=np.float64), 10).tobytes(order='C'))
    h.update(np.asarray(pbc, dtype=np.uint8).tobytes(order='C'))
    return h.hexdigest()


def geometry_dict(gid, regsha, category, key, parts, source_refs,
                  known=None, fixed=None, constraints=None, extra=None):
    nums, pos, cell, pbc = parts
    payload = {'ordered_numbers': nums.astype(int).tolist(),
               'positions_angstrom': pos.tolist(), 'cell_angstrom': cell.tolist(),
               'pbc': pbc.astype(bool).tolist()}
    return {'geometry_id': gid, 'source_registry_sha256': regsha,
            'source_primary_key': key, 'source_record_id': stable_id(regsha, category, key),
            'source_status': 'OBSERVED_GEOMETRY', 'geometry_sha256': pipeline_geometry_sha(*parts),
            **payload, 'constraints_known': known, 'fixed_indices': fixed,
            'constraints': constraints, 'source_refs': source_refs,
            'uncertainty_reasons': ['CONSTRAINTS_UNKNOWN'] if known is None else [],
            'source_columns': extra or {}}


def atoms_data(atoms):
    nums = np.asarray(atoms.numbers, dtype=np.int16)
    pos = np.asarray(atoms.positions, dtype=np.float64)
    cell = np.asarray(atoms.cell, dtype=np.float64).reshape(3, 3)
    pbc = np.asarray(atoms.pbc, dtype=np.uint8)
    # Reuse the same geometry validation for native decoded blobs.
    buf = io.BytesIO()
    np.savez(buf, numbers=nums, positions=pos, cell=cell, pbc=pbc)
    parts = decode_geometry(zlib.compress(buf.getvalue()))
    constraints = []
    fixed = []
    known = True if atoms.constraints else None
    for c in atoms.constraints:
        typ = type(c).__name__
        descriptor = {'type': typ}
        if typ == 'FixAtoms':
            indices = sorted(int(i) for i in c.get_indices())
            descriptor['indices'] = indices
            fixed.extend(indices)
        else:
            known = None
            descriptor['repr'] = repr(c)[:500]
        constraints.append(descriptor)
    return parts, known, sorted(set(fixed)) if known else None, constraints


class Store:
    """Disk-backed deterministic rows, no complete registry or geometry set in RAM."""
    def __init__(self, work: Path, regsha: str, registry_path: Path | None = None):
        self.regsha = regsha
        # Permit only paths from recorded SaddleMill science roots. Paths outside
        # are kept as references, never opened. The fixture parent supports tests.
        self.artifact_roots = [Path('/scratch/11588/ndn454/sm_anskey'),
                               Path('/work/11588/ndn454/ls6')]
        if registry_path is not None:
            self.artifact_roots.append(registry_path.parent.parent)
        self.db = sqlite3.connect(work)
        self.db.execute('CREATE TABLE geometries(k TEXT PRIMARY KEY, row TEXT NOT NULL)')
        self.db.execute('CREATE TABLE artifacts(k TEXT PRIMARY KEY, row TEXT NOT NULL)')
        self.artifact_cache: dict[str, tuple[str, str | None, int | None]] = {}
        self.stats = Counter()

    def put_geom(self, category, key, blob, expected_hash=None, refs=None, atoms=None, source_atoms=None):
        gid = stable_id(self.regsha, category, str(key))
        try:
            if atoms is not None:
                parts, known, fixed, constr = atoms_data(atoms)
            else:
                parts = decode_geometry(blob)
                known, fixed, constr = None, None, None
                if source_atoms is not None:
                    try:
                        a_parts, a_known, a_fixed, a_constr = atoms_data(source_atoms)
                        if pipeline_geometry_sha(*a_parts) == pipeline_geometry_sha(*parts):
                            known, fixed, constr = a_known, a_fixed, a_constr
                        else:
                            self.stats['source_geometry_disagreements'] += 1
                    except Exception:
                        self.stats['artifact_atom_metadata_unreadable'] += 1
            row = geometry_dict(gid, self.regsha, category, str(key), parts, refs or [],
                                known, fixed, constr)
            if source_atoms is not None and known is None:
                row['uncertainty_reasons'].append('ARTIFACT_CONSTRAINTS_UNVERIFIED')
            discrepancy = None
            if expected_hash and row['geometry_sha256'] != expected_hash:
                discrepancy = 'SOURCE_GEOMETRY_HASH_MISMATCH'
                row['uncertainty_reasons'].append('SOURCE_GEOMETRY_HASH_MISMATCH')
                row['source_columns']['source_geometry_sha256'] = expected_hash
                self.stats['geometry_hash_mismatches'] += 1
        except Exception as exc:
            self.stats['unreadable_geometries'] += 1
            return None, 'GEOMETRY_UNREADABLE:' + type(exc).__name__
        js = jd(row)
        old = self.db.execute('SELECT row FROM geometries WHERE k=?', (str(key),)).fetchone()
        if old is not None and old[0] != js:
            fail('GEOMETRY_KEY_COLLISION:' + str(key))
        self.db.execute('INSERT OR IGNORE INTO geometries VALUES(?,?)', (str(key), js))
        self.stats['decoded_geometries'] += 1
        return gid, discrepancy

    def artifact(self, table: str, pk: str, role: str, path_value, frame=None):
        if not path_value:
            self.stats['artifact_path_absent'] += 1
            return None, 'ARTIFACT_PATH_ABSENT'
        path = str(path_value)
        if role == 'saddle_result' and '#' in path:
            a, sep, suffix = path.rpartition('#')
            if sep and suffix.lstrip('-').isdigit():
                path, frame = a, int(suffix)
        rolekey = f'{table}:{pk}:{role}'
        aid = stable_id(self.regsha, 'source_artifacts', rolekey)
        try:
            p = Path(path)
            if 'benchmark_0_1_20' in path.lower() or 'benchmark 0.1.20' in path.lower():
                status, digest, n = 'PROTECTED_UNPUBLISHED_BENCHMARK', None, None
            elif not p.is_absolute():
                status, digest, n = 'NONABSOLUTE', None, None
            elif any(part.is_symlink() for part in (p, *p.parents)):
                status, digest, n = 'SYMLINK_UNTRUSTED', None, None
            elif not any(p == root or root in p.parents for root in self.artifact_roots):
                status, digest, n = 'OUTSIDE_APPROVED_ARTIFACT_ROOT', None, None
            elif not p.is_file():
                status, digest, n = 'MISSING', None, None
            else:
                if path in self.artifact_cache:
                    status, digest, n = self.artifact_cache[path]
                else:
                    original = p.stat()
                    n = original.st_size
                    digest = digest_file(p)
                    after = p.stat()
                    if p.is_symlink() or (original.st_size, original.st_mtime_ns, original.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        status, digest = 'CHANGED_DURING_HASH', None
                    else:
                        status = 'HASHED_READABLE'
                    self.artifact_cache[path] = (status, digest, n)
        except (OSError, PermissionError):
            status, digest, n = 'UNREADABLE', None, None
        self.stats['artifact_' + status] += 1
        row = {'source_registry_sha256': self.regsha, 'source_primary_key': rolekey,
               'source_record_id': aid, 'source_status': status,
               'artifact_id': aid, 'source_table': table, 'owner_source_primary_key': str(pk),
               'source_role': role, 'path': path, 'frame': frame,
               'sha256': digest, 'size_bytes': n,
               'source_refs': [stable_id(self.regsha, table, str(pk))],
               'uncertainty_reasons': [] if digest else ['SOURCE_ARTIFACT_' + status]}
        val = jd(row)
        old = self.db.execute('SELECT row FROM artifacts WHERE k=?', (rolekey,)).fetchone()
        if old is not None and old[0] != val:
            fail('ARTIFACT_KEY_COLLISION:' + rolekey)
        self.db.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?)', (rolekey, val))
        return aid, None if digest else 'SOURCE_ARTIFACT_' + status

    def read_artifact_atoms(self, path, frame, artifact_ok):
        if not artifact_ok or not path:
            return None
        try:
            from ase.io import read
            from ase.io.formats import UnknownFileTypeError
            try:
                return read(str(path), index=int(frame))
            except UnknownFileTypeError:
                # Saddle result artifacts may be scientific .npz containers,
                # not ASE-readable trajectories. They remain SHA-256-inventoried;
                # the registry geometry_blob is independently decoded/validated.
                # Never invent constraints or other source-atoms metadata.
                self.stats['source_atoms_unknown_ase_format'] += 1
                return None
        except (ImportError, OSError, ValueError, IndexError, TypeError):
            return None

    def endpoint(self, obsid, role, path, frame, artifact_ok, artifact_id=None):
        measurements = {'stored_energy_eV': None, 'stored_real_atomic_fmax_eV_A': None,
                        'terminal_status': None, 'convergence_reason': None,
                        'optimizer_steps': None, 'pes_identity': None,
                        'measurement_origin': None,
                        'independently_verified_convergence': False}
        if not artifact_ok or not path:
            return None, 'ENDPOINT_ARTIFACT_UNAVAILABLE', measurements
        try:
            # Only read serialized atoms. Never construct/execute a calculator.
            from ase.io import read  # pylint: disable=import-outside-toplevel
            atoms = read(str(path), index=int(frame))
            for key in ('terminal_status', 'status', 'convergence_reason',
                        'optimizer_steps', 'pes_identity', 'calculator_name'):
                value = atoms.info.get(key)
                if value is not None:
                    mapping = {'status': 'terminal_status', 'calculator_name': 'pes_identity'}
                    measurements[mapping.get(key, key)] = normalized(value)
            calc = getattr(atoms, 'calc', None)
            if calc is not None and type(calc).__name__ == 'SinglePointCalculator':
                stored = getattr(calc, 'results', {})
                if isinstance(stored, dict):
                    energy = stored.get('energy')
                    if energy is not None and math.isfinite(float(energy)):
                        measurements['stored_energy_eV'] = float(energy)
                    forces = stored.get('forces')
                    if forces is not None:
                        f = np.asarray(forces, dtype=float)
                        if f.shape == (len(atoms.numbers), 3) and np.isfinite(f).all():
                            measurements['stored_real_atomic_fmax_eV_A'] = float(np.linalg.norm(f, axis=1).max(initial=0.0))
                    measurements['measurement_origin'] = 'serialized_single_point_results'
            gid, err = self.put_geom('reaction_observations', f'{obsid}:{role}', None, refs=[artifact_id] if artifact_id else [], atoms=atoms)
            return gid, err, measurements
        except ImportError:
            return None, 'ASE_UNAVAILABLE_FOR_ENDPOINT', measurements
        except Exception as exc:
            return None, 'ENDPOINT_DECODE_FAILED:' + type(exc).__name__, measurements

    def verify_source_artifacts_unchanged(self):
        """Bind artifact hashes to completed output, including after endpoint decoding.

        A file can mutate between the original hash and atom decoding; it must
        never remain HASHED_READABLE under an old digest at export cutoff.
        """
        for name, (status, original_hash, original_size) in sorted(self.artifact_cache.items()):
            if status != 'HASHED_READABLE':
                continue
            p = Path(name)
            reject_symlink_ancestors(p)
            if not p.is_file() or p.is_symlink():
                fail('SOURCE_ARTIFACT_CHANGED_DURING_EXPORT:' + name)
            try:
                stat_before = p.stat()
                current_hash = digest_file(p)
                stat_after = p.stat()
            except OSError:
                fail('SOURCE_ARTIFACT_CHANGED_DURING_EXPORT:' + name)
            if (current_hash != original_hash or stat_before.st_size != original_size or
                    (stat_before.st_ino, stat_before.st_mtime_ns, stat_before.st_size) !=
                    (stat_after.st_ino, stat_after.st_mtime_ns, stat_after.st_size)):
                fail('SOURCE_ARTIFACT_CHANGED_DURING_EXPORT:' + name)

    def stream_spool(self, table, path):
        with path.open('w', encoding='utf-8', newline='\n') as f:
            for (record,) in self.db.execute('SELECT row FROM ' + q(table) + ' ORDER BY k'):
                f.write(record + '\n')
        return int(self.db.execute('SELECT count(*) FROM ' + q(table)).fetchone()[0])

    def close(self):
        self.db.close()


def rowmap(row):
    return {key: normalized(row[key]) for key in row.keys() if not isinstance(row[key], bytes)}


def optional_row(con, columns, table, col, key):
    if table not in columns or key is None:
        return None
    found = con.execute('SELECT * FROM ' + q(table) + ' WHERE ' + q(col) + '=? LIMIT 2', (key,)).fetchall()
    if len(found) > 1:
        fail('DUPLICATE_JOIN:' + table + ':' + str(key))
    return rowmap(found[0]) if found else None


def record_base(regsha, table, pk, status, references, uncertainty, raw):
    return {'source_registry_sha256': regsha, 'source_primary_key': str(pk),
            'source_record_id': stable_id(regsha, table, str(pk)),
            'source_status': status, 'source_refs': sorted(set(ref for ref in references if ref)),
            'uncertainty_reasons': sorted(set(uncertainty)), 'source_columns': raw}


def emit(con, columns, store: Store, output: Path, table: str, sha: str,
         cutoff: int, stats: Counter, active_snapshot: str | None,
         lower: str | None = None, upper: str | None = None) -> int:
    key_name = {'saddle_registry': 'canonical_saddle_id',
                'reaction_observations': 'observation_id',
                'reaction_registry': 'canonical_reaction_id',
                'canonical_minima': 'minimum_id'}[table]
    n = 0
    previous = None
    bounds = []
    values = []
    if lower is not None:
        bounds.append(q(key_name) + '>=?')
        values.append(lower)
    if upper is not None:
        bounds.append(q(key_name) + '<?')
        values.append(upper)
    where = (' WHERE ' + ' AND '.join(bounds)) if bounds else ''
    sql = 'SELECT * FROM ' + q(table) + where + ' ORDER BY ' + q(key_name)
    with output.open('w', encoding='utf-8', newline='\n') as w:
        for row in con.execute(sql, values):
            pk = str(row[key_name]) if row[key_name] is not None else ''
            if not pk or pk == previous:
                fail('DUPLICATE_OR_BLANK_PRIMARY_KEY:' + table)
            previous = pk
            raw = rowmap(row)
            reasons, refs = [], []
            if table == 'saddle_registry':
                groups = [str(z[0]) for z in con.execute(
                    'SELECT saddle_group_id FROM saddle_group_members WHERE canonical_saddle_id=? ORDER BY saddle_group_id', (pk,))]
                if len(groups) > 1:
                    reasons.append('MULTIPLE_REGISTERED_GROUPS_NO_UNIQUE_GROUP')
                starts = None
                if ('incremental_occurrences' in columns and
                        'starting_minimum_id' in columns['incremental_occurrences']):
                    starts = [r[0] for r in con.execute(
                        'SELECT DISTINCT starting_minimum_id FROM incremental_occurrences '
                        'WHERE canonical_saddle_id=? AND starting_minimum_id IS NOT NULL '
                        "AND starting_minimum_id<>'' ORDER BY starting_minimum_id", (pk,))]
                if not starts: reasons.append('EXACT_START_MINIMUM_UNVERIFIED')
                aid, err = store.artifact(table, pk, 'saddle_result', row['source_result_path'])
                if aid: refs.append(aid)
                if err: reasons.append(err)
                source_path = str(row['source_result_path'] or '')
                source_frame = 0
                if '#' in source_path:
                    ppart, _, fpart = source_path.rpartition('#')
                    if fpart.lstrip('-').isdigit(): source_path, source_frame = ppart, int(fpart)
                atoms = store.read_artifact_atoms(source_path, source_frame, err is None)
                gid, geom_err = store.put_geom(table, pk, row['geometry_blob'], row['geometry_sha256'], refs, source_atoms=atoms)
                if geom_err: reasons.append(geom_err)
                if atoms is None: reasons.append('SADDLE_SOURCE_ATOMS_UNAVAILABLE')
                reasons.extend(['HESSIAN_PROJECTION_AND_ORDER_UNVERIFIED',
                                'PES_COMPATIBILITY_UNVERIFIED'])
                result = {'structure_id': row['structure_id'], 'canonical_saddle_id': pk,
                          'saddle_group_id': groups[0] if len(groups) == 1 else None,
                          'registered_saddle_group_ids': groups,
                          'observed_starting_minimum_ids': starts, 'geometry_id': gid,
                          'energy_eV': row['energy_eV'], 'hessian_status': row['hessian_status'],
                          'reaction_eligible_registry_flag': row['reaction_eligible']}
                status = row['hessian_status']
            elif table == 'reaction_observations':
                for suffix in ('reaction_observation_provenance', 'canonical_doublemin_protocol_provenance',
                               'reaction_endpoint_state'):
                    r = optional_row(con, columns, suffix, 'observation_id', pk)
                    if r is not None: raw[suffix] = r
                    else: reasons.append('NO_' + suffix.upper())
                endpoints = {}
                for side in ('minus', 'plus'):
                    path = row[side + '_path']
                    frame = row[side + '_frame']
                    aid, err = store.artifact(table, pk, side + '_endpoint', path, frame)
                    if aid: refs.append(aid)
                    geom_id, geom_err, measured = store.endpoint(pk, side, path, frame, err is None, aid)
                    endpoints[side + '_geometry_id'] = geom_id
                    endpoints[side + '_endpoint_evidence'] = measured
                    if err: reasons.append(err)
                    if geom_err: reasons.append(side.upper() + '_' + geom_err)
                prov = raw.get('reaction_observation_provenance')
                if prov and prov.get('hessian_source_path'):
                    aid, err = store.artifact(table, pk, 'hessian_source', prov['hessian_source_path'], prov.get('hessian_source_frame'))
                    if aid: refs.append(aid)
                    if err: reasons.append(err)
                if row['canonical_reaction_id'] is None or row['canonical_reaction_id'] == '':
                    reasons.append('NO_CANONICAL_REACTION_ASSIGNMENT')
                reasons.extend(['ENDPOINT_CONVERGENCE_UNVERIFIED', 'EXACT_START_MINIMUM_UNVERIFIED',
                                'PES_COMPATIBILITY_UNVERIFIED'])
                result = {'observation_id': pk, 'structure_id': row['structure_id'],
                          'canonical_saddle_id': row['canonical_saddle_id'],
                          'saddle_group_id': row['saddle_group_id'],
                          'canonical_reaction_id': row['canonical_reaction_id'],
                          'protocol': row['protocol'],
                          'protocol_eligible_registry_flag': row['protocol_eligible'],
                          'outcome': row['outcome'], **endpoints,
                          'publication_status': 'IN_PINNED_PUBLISHED_REGISTRY',
                          'source_kind': row['source_kind']}
                status = row['outcome']
            elif table == 'reaction_registry':
                representative = raw.get('representative_observation_id')
                r = optional_row(con, columns, 'canonical_reaction_endpoint_state', 'canonical_reaction_id', pk)
                if r is not None: raw['canonical_reaction_endpoint_state'] = r
                else: reasons.append('NO_CANONICAL_REACTION_ENDPOINT_STATE')
                if representative:
                    refs.append(stable_id(sha, 'reaction_observations', str(representative)))
                reasons.extend(['CONNECTION_NOT_INDEPENDENTLY_VERIFIED', 'EXACT_START_MINIMUM_UNVERIFIED'])
                result = {'canonical_reaction_id': pk, 'structure_id': row['structure_id'],
                          'saddle_group_id': row['saddle_group_id'], 'status': row['status'],
                          'connected_to_canonical_minimum': row['connected_to_canonical_minimum'],
                          'rate_relevant_registry_flag': row['rate_relevant'],
                          'barrier_eV': row['barrier_eV']}
                status = row['status']
            else:
                aid, err = store.artifact(table, pk, 'minima_object', row['object_path'])
                if aid: refs.append(aid)
                if err: reasons.append(err)
                atoms = store.read_artifact_atoms(row['object_path'], -1, err is None)
                gid, geom_err = store.put_geom(table, pk, row['geometry_blob'], row['geometry_sha256'], refs, source_atoms=atoms)
                if atoms is None: reasons.append('MINIMUM_SOURCE_ATOMS_UNAVAILABLE')
                if geom_err: reasons.append(geom_err)
                snapshot_id = None
                if 'canonical_minima_snapshot_members' in columns:
                    rs = con.execute('SELECT snapshot_id FROM canonical_minima_snapshot_members WHERE minimum_id=? ORDER BY snapshot_id', (pk,)).fetchall()
                    snapshot_id = [r[0] for r in rs]
                cert = optional_row(con, columns, 'minimum_certification', 'minimum_id', pk)
                if cert is not None: raw['minimum_certification'] = cert
                else: reasons.append('NO_MINIMUM_CERTIFICATION_ROW')
                reasons.append('MINIMUM_PES_AND_CONSTRAINTS_UNVERIFIED')
                result = {'minimum_id': pk, 'parent_structure_id': row['parent_structure_id'],
                          'geometry_id': gid, 'energy_eV': row['energy_eV'],
                          'certificate_status': row['certification_status'],
                          'member_snapshot_ids': snapshot_id,
                          'active_in_published_minima_snapshot':
                              (active_snapshot in snapshot_id if active_snapshot is not None and snapshot_id is not None else None),
                          'exact_minimum_identity': pk}
                status = row['certification_status']
            if isinstance(status, str) and status:
                stats[f'{table}:status:{status}'] += 1
            else:
                stats[f'{table}:status:NULL_OR_EMPTY'] += 1
            stats['rows:' + table] += 1
            out_row = {**record_base(sha, table, pk, status, refs, reasons, raw), **result}
            w.write(jd(out_row) + '\n')
            n += 1
    expected = con.execute('SELECT count(*) FROM ' + q(table) + where, values).fetchone()[0]
    if n != expected:
        fail('ROW_COUNT_MISMATCH:' + table)
    return n



# Parallel implementation only changes the execution schedule. Every worker has an
# independent read-only SQLite connection and an independent spool; the merger
# writes exactly the serial exporter record streams in key order.
TABLE_KEYS = {'saddle_registry': 'canonical_saddle_id',
              'reaction_observations': 'observation_id',
              'reaction_registry': 'canonical_reaction_id',
              'canonical_minima': 'minimum_id'}


def plan_shards(con, workers):
    """Deterministic contiguous key ranges, never rowid/host-dependent hashing."""
    plans = []
    for table, filename in FILES.items():
        key = TABLE_KEYS[table]
        n = int(con.execute('SELECT count(*) FROM ' + q(table)).fetchone()[0])
        nshards = min(workers, n) if n else 1
        starts = []
        for j in range(1, nshards):
            offset = j * n // nshards
            row = con.execute('SELECT ' + q(key) + ' FROM ' + q(table) +
                              ' ORDER BY ' + q(key) + ' LIMIT 1 OFFSET ?',
                              (offset,)).fetchone()
            if row is None or not isinstance(row[0], str) or not row[0]:
                fail('INVALID_SHARD_BOUNDARY:' + table)
            starts.append(row[0])
        if len(starts) != len(set(starts)):
            fail('DUPLICATE_SHARD_BOUNDARY:' + table)
        edges = [None] + starts + [None]
        for j in range(nshards):
            plans.append({'table': table, 'filename': filename,
                          'idx': j, 'low': edges[j], 'high': edges[j + 1]})
    return plans


def worker_shard(registry, registry_sha, task, work, cutoff, active_snapshot):
    # ProcessPoolExecutor process: no SQLite connections, Store or descriptors
    # shared with the parent or with other workers.
    db = Path(registry)
    shard_dir = Path(work) / ('shard_' + task['table'] + '_' + str(task['idx']))
    shard_dir.mkdir(mode=0o700)
    con = sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    try:
        columns = get_columns(con)
        store = Store(shard_dir/'spool.sqlite', registry_sha, db)
        try:
            stats = Counter()
            count = emit(con, columns, store, shard_dir/'rows.jsonl', task['table'],
                         registry_sha, cutoff, stats, active_snapshot,
                         lower=task['low'], upper=task['high'])
            store.verify_source_artifacts_unchanged()
            for key in ('geometries', 'artifacts'):
                store.stream_spool(key, shard_dir/(key+'.jsonl'))
            stats.update(store.stats)
        finally:
            store.close()
    finally:
        con.close()
    return {'table':task['table'], 'idx':task['idx'], 'count':count, 'stats':dict(stats)}


def _merge_sorted_spools(shard_dirs, kind, target):
    """Merge key-sorted spools, de-duplicating identical IDs, fail on conflict."""
    handles = []
    try:
        for root in shard_dirs:
            handles.append((root / (kind+'.jsonl')).open('r', encoding='utf-8'))
        def entries(handle):
            previous = None
            for line in handle:
                row = json.loads(line)
                key = row['source_primary_key']
                if previous is not None and key <= previous:
                    fail('UNSORTED_WORKER_SPOOL:' + kind)
                previous = key
                yield key, line
        merged = heapq.merge(*(entries(h) for h in handles))
        count = 0
        previous = None
        previous_line = None
        with target.open('w', encoding='utf-8', newline='\n') as f:
            for key, line in merged:
                if key == previous:
                    if line != previous_line:
                        fail('PARALLEL_SPOOL_KEY_COLLISION:' + kind + ':' + key)
                    continue
                if previous is not None and key < previous:
                    fail('MERGE_KEY_ORDER_ERROR:' + kind)
                f.write(line)
                previous, previous_line = key, line
                count += 1
        return count
    finally:
        for h in handles:
            h.close()


def parallel_emit(con, out, selected, sha, cutoff, active_snapshot, work, workers):
    tasks = plan_shards(con, workers)
    tasks_by_table = {table: [t for t in tasks if t['table']==table] for table in FILES}
    results = {}
    # No production source modifications; any worker failure blocks the manifest.
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers,
            mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {pool.submit(worker_shard, str(selected), sha, task, str(work),
                               cutoff, active_snapshot): task for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            result = future.result()  # fail closed
            results[(task['table'], task['idx'])] = result
    counts = {}
    stats = Counter()
    for table, filename in FILES.items():
        with (out / filename).open('wb') as dest:
            for task in tasks_by_table[table]:
                result = results[(table, task['idx'])]
                counts[filename] = counts.get(filename, 0) + result['count']
                stats.update(result['stats'])
                path = Path(work)/('shard_'+table+'_'+str(task['idx']))/'rows.jsonl'
                with path.open('rb') as f:
                    shutil.copyfileobj(f, dest, length=1024*1024)
        total = con.execute('SELECT count(*) FROM ' + q(table)).fetchone()[0]
        if counts[filename] != total:
            fail('PARALLEL_TOTAL_COUNT_MISMATCH:' + table)
    dirs = [Path(work)/('shard_'+t['table']+'_'+str(t['idx'])) for t in tasks]
    counts['geometries.jsonl'] = _merge_sorted_spools(dirs, 'geometries', out/'geometries.jsonl')
    counts['source_artifacts.jsonl'] = _merge_sorted_spools(dirs, 'artifacts', out/'source_artifacts.jsonl')
    return counts, stats

def export(pointer: Path | None, registry: Path | None, out: Path, workers: int = 1) -> dict:
    if type(workers) is not int or workers < 1 or workers > 16:
        fail('INVALID_WORKER_COUNT')
    if (pointer is None) == (registry is None):
        fail('REQUIRE_EXACTLY_ONE_INPUT')
    selected, pbytes = pointer_target(pointer) if pointer is not None else (registry, None)
    assert selected is not None
    if not selected.is_absolute():
        fail('REGISTRY_MUST_BE_ABSOLUTE')
    source_check(selected)
    db = selected.resolve(strict=True)
    protected_output(out, db)
    if out.exists():
        raise FileExistsError('REFUSE_OVERWRITE')
    before = digest_file(db)
    dbstat = db.stat()
    uri = db.as_uri() + '?mode=ro&immutable=1'
    con = sqlite3.connect(uri, uri=True)
    parent_fd = None
    output_fd = None
    out_requested = out.absolute()
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    try:
        columns = get_columns(con)
        meta, cutoff, applied, highest_applied = metadata(con)
        # Preflight source validation before creating an output directory.
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            fail('REGISTRY_QUICK_CHECK_FAILED')
        if out.exists():
            raise FileExistsError('REFUSE_OVERWRITE')
        parent_fd, output_fd = pin_output_directory(out_requested, db)
        out = Path(f'/proc/self/fd/{output_fd}')
        try:
            with tempfile.TemporaryDirectory(prefix='saddlemill_evidence_', dir=os.environ.get('TMPDIR') or None) as tmp:
                active_snapshot = meta.get('canonical_minima_snapshot_id') or None
                if workers > 1:
                    counts, stats = parallel_emit(con, out, db, before, cutoff,
                                                  active_snapshot, Path(tmp), workers)
                else:
                    store = Store(Path(tmp) / 'spool.sqlite', before, db)
                    try:
                        stats = Counter()
                        counts = {}
                        for table, filename in FILES.items():
                            counts[filename] = emit(con, columns, store, out / filename, table, before, cutoff, stats, active_snapshot)
                        store.verify_source_artifacts_unchanged()
                        counts['geometries.jsonl'] = store.stream_spool('geometries', out / 'geometries.jsonl')
                        counts['source_artifacts.jsonl'] = store.stream_spool('artifacts', out / 'source_artifacts.jsonl')
                        stats.update(store.stats)
                    finally:
                        store.close()
            con.close()
            # Source source-stat/full digest/pointer and WAL/SHM must still agree.
            source_check(db)
            after = digest_file(db)
            if before != after or dbstat.st_size != db.stat().st_size:
                fail('REGISTRY_CHANGED_DURING_EXPORT')
            if pointer is not None:
                target2, raw2 = pointer_target(pointer)
                if raw2 != pbytes or target2 != selected or target2.resolve(strict=True) != db:
                    fail('POINTER_CHANGED_DURING_EXPORT')
            hashes = {p.name: digest_file(p) for p in sorted(out.glob('*.jsonl'))}
            manifest = {
                'schema_version': SCHEMA, 'export_version': VERSION,
                'scientific_contract_id': 'SCIENTIFIC_CONTRACT_20261002_v1',
                'source_repository': SOURCE_REPO, 'source_commit': SOURCE_COMMIT,
                'source_commit_is_live_installed_identity_verified': False,
                'registry': {'absolute_path': str(db), 'sha256': before,
                             'size_bytes': dbstat.st_size, 'published_through': cutoff,
                             'registry_version': meta.get('registry_version'),
                             'applied_event_count': applied,
                             'max_applied_event_sequence': highest_applied,
                             'pointer_path': str(pointer) if pointer else None},
                'file_sha256': hashes, 'record_counts': counts,
                'coverage': dict(sorted(stats.items())),
                'completeness': {'registry_rows_enumerated': True,
                                 'raw_search_coverage_verified': False,
                                 'unpublished_event_scope_included': False,
                                 'artifact_provenance_coverage_verified': False},
                'limitations': ['Published registry is not exhaustive Search attempt evidence.',
                                'Unpublished canonical event ledger is outside this bundle.',
                                'Scientific qualification, exact-start, PES identity and Hessian order are not independently proven.',
                                'Missing/unreadable artifacts and unknown constraints are not negative scientific evidence.',
                                'Pinned GitHub source identity is not an independently verified installed-LS6 source identity.'],
            }
            (out / 'manifest.json').write_text(jd(manifest) + '\n', encoding='utf-8')
            (out / 'SHA256SUMS.txt').write_text(''.join(
                f'{digest_file(path)}  {path.name}\n' for path in sorted(out.glob('*')) if path.name != 'SHA256SUMS.txt'),
                encoding='utf-8')
            verify_output_locator(out_requested, output_fd)
            return manifest
        except BaseException:
            # Only remove files newly created by this invocation; never clean production.
            for file in out.iterdir():
                if file.is_file(): file.unlink()
            if parent_fd is not None and output_fd is not None:
                os.rmdir(out_requested.name, dir_fd=parent_fd)
            raise
    finally:
        con.close()
        if output_fd is not None: os.close(output_fd)
        if parent_fd is not None: os.close(parent_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--pointer', type=Path)
    source.add_argument('--registry', type=Path)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=1, help='Number of read-only exporter processes (1-16)')
    args = parser.parse_args(argv)
    try:
        result = export(args.pointer, args.registry, args.out_dir, workers=args.workers)
    except (ValueError, RuntimeError, OSError, sqlite3.Error, UnicodeError, FileExistsError) as exc:
        print(f'BLOCK: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    print(jd({'status': 'EXPORTED_UNQUALIFIED', 'directory': str(args.out_dir),
              'registry_sha256': result['registry']['sha256'],
              'published_through': result['registry']['published_through'],
              'counts': result['record_counts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
