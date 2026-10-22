#!/usr/bin/env python3
"""Add an independently versioned scientific data layer to an immutable RC14 export.

Read-only w.r.t. original evidence and science artifacts. Never qualifies a
reaction or substitutes missing Hessian modes. Requires the six legacy outputs
and the source SHA256 manifest; creates a NEW sibling directory only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
import numpy as np

VERSION = 'saddlebench-scientific-layer-v1'
GEOM_TABLE = 'geometries.jsonl'
LEGACY = ('saddles.jsonl', 'doublemin_observations.jsonl', 'reactions.jsonl',
          'minima.jsonl', 'geometries.jsonl', 'source_artifacts.jsonl')
MODES_PER_SHARD = 128


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def compact(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'


def read_rows(path):
    with path.open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('INVALID_JSONL_ROW:' + str(path))
                yield row


def check_evidence(evidence):
    manifest = json.loads((evidence / 'manifest.json').read_text())
    if manifest.get('schema_version') != 'saddlemill-evidence-v1':
        raise ValueError('UNSUPPORTED_BASE_SCHEMA')
    regsha = manifest.get('registry', {}).get('sha256')
    if not isinstance(regsha, str) or len(regsha) != 64:
        raise ValueError('MISSING_REGISTRY_SHA')
    for filename in LEGACY:
        path = evidence / filename
        expected = manifest.get('file_sha256', {}).get(filename)
        if not path.is_file() or not expected or hash_file(path) != expected:
            raise ValueError('BASE_EVIDENCE_HASH_MISMATCH:' + filename)
    return manifest


def add_row(db, table, key, value):
    if key is None or key == '':
        raise ValueError('MISSING_SOURCE_KEY:' + table)
    try:
        db.execute('INSERT INTO ' + table + ' VALUES (?,?)', (key, compact(value)))
    except sqlite3.IntegrityError as e:
        raise ValueError('DUPLICATE_SOURCE_KEY:' + table + ':' + str(key)) from e


def build_indexes(db, evidence, regsha):
    db.execute('CREATE TABLE geom (id TEXT PRIMARY KEY, row TEXT NOT NULL)')
    db.execute('CREATE TABLE saddle (id TEXT PRIMARY KEY, row TEXT NOT NULL)')
    db.execute('CREATE TABLE reactions (id TEXT PRIMARY KEY, row TEXT NOT NULL)')
    db.execute('CREATE TABLE obs (id TEXT PRIMARY KEY, row TEXT NOT NULL)')
    db.execute('CREATE TABLE obs_by_saddle (sid TEXT, oid TEXT, PRIMARY KEY(sid,oid))')
    db.execute('CREATE INDEX obs_by_saddle_sid ON obs_by_saddle(sid)')
    for filename, table, key in [
        ('geometries.jsonl', 'geom', 'geometry_id'),
        ('saddles.jsonl', 'saddle', 'canonical_saddle_id'),
        ('reactions.jsonl', 'reactions', 'canonical_reaction_id'),
        ('doublemin_observations.jsonl', 'obs', 'observation_id'),
    ]:
        for row in read_rows(evidence / filename):
            if row.get('source_registry_sha256') != regsha:
                raise ValueError('CROSS_SNAPSHOT_DATA:' + filename)
            add_row(db, table, row.get(key), row)
            if table == 'obs' and row.get('canonical_saddle_id'):
                db.execute('INSERT INTO obs_by_saddle VALUES (?,?)', (str(row['canonical_saddle_id']), str(row['observation_id'])))
    db.commit()


def get(db, table, key):
    if not key:
        return None
    res = db.execute('SELECT row FROM ' + table + ' WHERE id=?', (str(key),)).fetchone()
    return json.loads(res[0]) if res else None


def geom_meta(db, gid):
    row = get(db, 'geom', gid)
    return None if row is None else {
        'geometry_id': gid, 'geometry_sha256': row.get('geometry_sha256'),
        'n_atoms': len(row.get('ordered_numbers', [])),
        'constraints_known': row.get('constraints_known'),
    }


def triplets(db, evidence, output, registry_sha):
    counts = Counter()
    with (output/'reaction_triplets.jsonl').open('w', encoding='utf-8') as out:
        for obs in read_rows(evidence/'doublemin_observations.jsonl'):
            sid = obs.get('canonical_saddle_id')
            rid = obs.get('canonical_reaction_id')
            saddle = get(db, 'saddle', sid)
            reaction = get(db, 'reactions', rid)
            roles = {
                'minimum_A': geom_meta(db, obs.get('minus_geometry_id')),
                'saddle': geom_meta(db, saddle.get('geometry_id') if saddle else None),
                'minimum_B': geom_meta(db, obs.get('plus_geometry_id')),
            }
            reasons = []
            for role, geo in roles.items():
                if geo is None:
                    reasons.append(role.upper()+'_GEOMETRY_UNAVAILABLE')
            if rid and reaction is None:
                reasons.append('CANONICAL_REACTION_NOT_IN_EXPORTED_REGISTRY')
            if not rid:
                reasons.append('NO_CANONICAL_REACTION_ASSIGNMENT')
            if saddle is None:
                reasons.append('SADDLE_NOT_IN_EXPORTED_REGISTRY')
            if reaction and sid and (reaction.get('structure_id') != obs.get('structure_id')):
                reasons.append('REACTION_STRUCTURE_MISMATCH')
            status = 'GEOMETRIES_PRESENT_UNQUALIFIED' if not reasons else 'INCOMPLETE_UNQUALIFIED'
            raw = obs.get('source_columns') or {}
            prov = raw.get('reaction_observation_provenance') or {}
            endpoint_state = raw.get('reaction_endpoint_state') or {}
            row = {
                'schema_version': VERSION, 'source_registry_sha256': registry_sha,
                'observation_id': obs['observation_id'],
                'canonical_saddle_id': sid, 'canonical_reaction_id': rid,
                'saddle_group_id': obs.get('saddle_group_id'),
                'structure_id': obs.get('structure_id'),
                'source_record_id': obs.get('source_record_id'),
                'protocol': obs.get('protocol'), 'outcome': obs.get('outcome'),
                'source_kind': obs.get('source_kind'),
                'protocol_eligible_registry_flag': obs.get('protocol_eligible_registry_flag'),
                'connected_registry_flag': reaction.get('connected_to_canonical_minimum') if reaction else None,
                'rate_relevant_registry_flag': reaction.get('rate_relevant_registry_flag') if reaction else None,
                'barrier_eV': reaction.get('barrier_eV') if reaction else None,
                'reaction_status': reaction.get('status') if reaction else None,
                'observed_starting_minimum_id': prov.get('starting_minimum_id') or raw.get('starting_minimum_id'),
                'minimum_A_is_doublemin_minus': True,
                'minimum_B_is_doublemin_plus': True,
                'chemical_forward_orientation_verified': False,
                'geometry': roles,
                'endpoint_evidence': {
                    'minus': obs.get('minus_endpoint_evidence'),
                    'plus': obs.get('plus_endpoint_evidence'),
                },
                'endpoint_state_provenance': endpoint_state,
                'source_refs': obs.get('source_refs', []),
                'source_uncertainty_reasons': obs.get('uncertainty_reasons', []),
                'status': status, 'missing_or_inconsistent': reasons,
                'independently_verified_connected': False,
            }
            out.write(compact(row))
            counts[status] += 1
    return counts


def _atom_count(db, saddle_id):
    saddle = get(db, 'saddle', saddle_id)
    if not saddle:
        return None, None
    geo = geom_meta(db, saddle.get('geometry_id'))
    if geo:
        return geo['n_atoms'], geo['geometry_sha256']
    return None, None


def _scalar(data, key):
    if key not in data.files:
        return None
    v = np.asarray(data[key]); return v.item() if v.size == 1 else None


def _is_allowed_path(path, extra_roots=()):
    # Match RC14 producer's read-only science roots. Fixture roots may be added
    # only in explicitly opt-in local testing, never from remote JSON fields.
    p = Path(path)
    if not p.is_absolute() or p.is_symlink() or any(a.is_symlink() for a in p.parents):
        return False
    roots = [Path('/scratch/11588/ndn454/sm_anskey'), Path('/work/11588/ndn454/ls6')]
    roots.extend(Path(x).resolve() for x in extra_roots)
    return any(p == root or root in p.parents for root in roots)


def read_mode(artifact, natoms, expected_geometry_sha, extra_roots=()):
    """Keep native eigenvector without reconstruction. Never assume mode/PES parity."""
    path = artifact.get('path')
    if (artifact.get('source_status') != 'HASHED_READABLE' or
            not artifact.get('sha256') or not path or not _is_allowed_path(path, extra_roots)):
        return None, {'status': 'SOURCE_ARTIFACT_UNAVAILABLE', 'reason': artifact.get('source_status')}
    p = Path(path)
    if p.suffix.lower() not in ('.npz', '.traj'):
        return None, {'status': 'UNSUPPORTED_MODE_SOURCE_FORMAT', 'reason': p.suffix.lower()}
    try:
        before = p.stat()
        if not p.is_file() or before.st_size > 1024 * 1024 * 1024:
            return None, {'status': 'SOURCE_TOO_LARGE_OR_MISSING'}
        if hash_file(p) != artifact['sha256']:
            return None, {'status': 'SOURCE_SHA256_MISMATCH'}
        if p.suffix.lower() == '.npz':
            with np.load(p, allow_pickle=False) as z:
                if 'lowest_eigenmode' not in z.files:
                    return None, {'status': 'MODE_NOT_IN_SOURCE_NPZ'}
                arr = np.asarray(z['lowest_eigenmode'])
                nneg = _scalar(z, 'negative_mode_count')
                tol = _scalar(z, 'negative_eigenvalue_tolerance')
                eigenvals = z['order_eigenvalues'] if 'order_eigenvalues' in z.files else z['eigenvalues'] if 'eigenvalues' in z.files else None
                eig = float(np.asarray(eigenvals).reshape(-1)[0]) if eigenvals is not None and np.asarray(eigenvals).size else None
                if eig is not None and not math.isfinite(eig):
                    eig = None
                proj = _scalar(z, 'order_projection')
                rawsha = _scalar(z, 'geometry_sha256')
                source_geometry_sha = str(rawsha) if rawsha is not None else None
        else:
            from ase.io import read  # no calculator execution
            frame = artifact.get('frame')
            atoms = read(str(p), index=int(frame) if frame is not None else 0)
            info = getattr(atoms, 'info', {})
            def find(k, root):
                if not isinstance(root, dict):
                    return None
                if k in root:
                    return root[k]
                for v in root.values():
                    if isinstance(v, dict):
                        r = find(k, v)
                        if r is not None:
                            return r
                return None
            rawmode = find('lowest_eigenmode', info)
            if rawmode is None:
                rawmode = find('eigenmode', info)
            if rawmode is None:
                return None, {'status': 'MODE_NOT_IN_SOURCE_TRAJ'}
            arr = np.asarray(rawmode)
            nneg = find('negative_mode_count', info)
            tol = find('negative_eigenvalue_tolerance', info)
            proj = find('order_projection', info)
            eig = find('lowest_eigenvalue', info)
            from hashlib import sha256
            hh = sha256()
            hh.update(np.asarray(atoms.numbers, dtype=np.int16).tobytes(order='C'))
            hh.update(np.round(np.asarray(atoms.positions, dtype=np.float64), 10).tobytes(order='C'))
            hh.update(np.round(np.asarray(atoms.cell, dtype=np.float64), 10).tobytes(order='C'))
            hh.update(np.asarray(atoms.pbc, dtype=np.uint8).tobytes(order='C'))
            source_geometry_sha = hh.hexdigest()
        if arr.ndim != 2 or arr.shape[1] != 3 or arr.dtype.kind not in 'fi' or not np.isfinite(arr).all():
            return None, {'status': 'INVALID_MODE_ARRAY'}
        if np.linalg.norm(arr) < 1e-14:
            return None, {'status': 'ZERO_MODE_ARRAY'}
        info = {
            'status': 'RECORDED_MODE_UNQUALIFIED',
            'negative_mode_count': int(nneg) if nneg is not None else None,
            'negative_eigenvalue_tolerance_eV_A2': float(tol) if tol is not None else None,
            'lowest_eigenvalue_eV_A2': (float(eig) if eig is not None and math.isfinite(float(eig)) else None),
            'order_projection': str(proj) if proj is not None else None,
            'source_geometry_sha256': source_geometry_sha,
            'exact_geometry_sha_match': (
                source_geometry_sha == expected_geometry_sha
                if source_geometry_sha is not None and expected_geometry_sha is not None else None),
            'full_atom_shape': arr.shape == (natoms, 3) if natoms is not None else None,
            'mode_dtype': str(arr.dtype), 'mode_shape': list(arr.shape),
            'mode_norm': float(np.linalg.norm(arr)),
        }
        after = p.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            return None, {'status': 'SOURCE_CHANGED_DURING_READ'}
        # Strictly do not make a numerical scientific certification here.
        return arr, info
    except (OSError, ValueError, KeyError, TypeError, OverflowError, EOFError, zipfile.BadZipFile) as e:
        return None, {'status': 'MODE_DECODE_FAILED', 'reason': type(e).__name__}


def modes(db, evidence, out, regsha, shard_size=MODES_PER_SHARD, extra_roots=()):
    if shard_size < 1 or shard_size > 4096:
        raise ValueError('INVALID_SHARD_SIZE')
    dir_modes = out/'mode_arrays'
    dir_modes.mkdir()
    counts = Counter()
    db.execute('CREATE TABLE mode_by_saddle (sid TEXT, artifact_id TEXT, observation_id TEXT)')
    db.execute('CREATE INDEX mode_by_saddle_sid ON mode_by_saddle(sid)')
    shard = {}
    shard_no = 0
    row_no = 0
    unique_shas = set()

    def flush():
        nonlocal shard_no, shard
        if not shard:
            return
        shard_name = 'mode_arrays/modes_%06d.npz' % shard_no
        path = out / shard_name
        np.savez_compressed(path, **shard)
        shard_no += 1
        shard = {}

    with (out/'hessian_modes.jsonl').open('w', encoding='utf-8') as w:
        for artifact in read_rows(evidence/'source_artifacts.jsonl'):
            role = artifact.get('source_role')
            if role not in ('saddle_result', 'hessian_source'):
                continue
            if artifact.get('source_registry_sha256') != regsha:
                raise ValueError('CROSS_SNAPSHOT_ARTIFACT')
            if role == 'saddle_result':
                sid = artifact.get('owner_source_primary_key')
                obsid = None
            else:
                obsid = artifact.get('owner_source_primary_key')
                obs = get(db, 'obs', obsid)
                sid = obs.get('canonical_saddle_id') if obs else None
            n, geom_sha = _atom_count(db, sid)
            arr, info = read_mode(artifact, n, geom_sha, extra_roots)
            array_ref = None
            if arr is not None:
                key = 'm%04d' % len(shard)
                array_ref = {'file': 'mode_arrays/modes_%06d.npz' % shard_no,
                             'key': key, 'sha256_float_array': hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()}
                shard[key] = arr
                if len(shard) >= shard_size:
                    flush()
            row = {
                'schema_version': VERSION, 'source_registry_sha256': regsha,
                'artifact_id': artifact.get('artifact_id'), 'source_artifact_sha256': artifact.get('sha256'),
                'canonical_saddle_id': sid, 'observation_id': obsid,
                'source_role': role, 'mode': info, 'mode_array': array_ref,
                'saddle_geometry_sha256': geom_sha,
                'source_artifact_status': artifact.get('source_status'),
                'mode_scientific_certification': 'NOT_ESTABLISHED',
            }
            w.write(compact(row)); row_no += 1
            if sid:
                db.execute('INSERT INTO mode_by_saddle VALUES (?,?,?)', (sid, artifact.get('artifact_id'), obsid))
            counts[info['status']] += 1
        flush()
    db.commit()
    return counts, row_no, shard_no


def enriched_saddles(db, evidence, out, regsha):
    n = 0
    with (out/'saddles_enriched.jsonl').open('w', encoding='utf-8') as f:
        for sad in read_rows(evidence/'saddles.jsonl'):
            sid = sad['canonical_saddle_id']
            obs = [r[0] for r in db.execute('SELECT oid FROM obs_by_saddle WHERE sid=? ORDER BY oid', (sid,))]
            modes = [{'artifact_id': r[0], 'observation_id': r[1]} for r in db.execute(
                'SELECT artifact_id, observation_id FROM mode_by_saddle WHERE sid=? ORDER BY artifact_id', (sid,))]
            record = {
                'schema_version': VERSION, 'canonical_saddle_id': sid,
                'source_registry_sha256': regsha, 'structure_id': sad.get('structure_id'),
                'geometry_id': sad.get('geometry_id'),
                'energy_eV': sad.get('energy_eV'),
                'hessian_status_registry': sad.get('hessian_status'),
                'reaction_eligible_registry_flag': sad.get('reaction_eligible_registry_flag'),
                'saddle_group_ids': sad.get('registered_saddle_group_ids'),
                'starting_minimum_ids_observed': sad.get('observed_starting_minimum_ids'),
                'reaction_triplet_observation_ids': obs,
                'mode_source_artifact_links': modes,
                'scientific_certification': 'NOT_ESTABLISHED_BY_EXPORTER',
            }
            f.write(compact(record));n += 1
    return n


def build(evidence: Path, out: Path, shard_size=MODES_PER_SHARD, extra_roots=()):
    evidence = evidence.resolve(strict=True)
    out = out.absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError('REFUSE_REPLACE_EXISTING_SCIENCE_LAYER')
    # Prevent putting outputs within evidence or in trusted production trees.
    if evidence == out or evidence in out.parents:
        raise ValueError('OUTPUT_INSIDE_BASE_EVIDENCE')
    legacy = check_evidence(evidence)
    out.mkdir(parents=False)
    try:
        with tempfile.TemporaryDirectory(prefix='sb_science_index_') as td:
            db = sqlite3.connect(Path(td)/'index.sqlite')
            try:
                build_indexes(db, evidence, legacy['registry']['sha256'])
                triples = triplets(db, evidence, out, legacy['registry']['sha256'])
                mode_stats, mode_records, mode_shards = modes(db, evidence, out, legacy['registry']['sha256'], shard_size, extra_roots)
                enriched_count = enriched_saddles(db, evidence, out, legacy['registry']['sha256'])
            finally:
                db.close()
        hashes = {p.relative_to(out).as_posix(): hash_file(p) for p in sorted(out.rglob('*')) if p.is_file()}
        manifest = {
            'schema_version': VERSION, 'source_evidence_schema': legacy['schema_version'],
            'source_evidence_manifest_sha256': hash_file(evidence/'manifest.json'),
            'source_registry_sha256': legacy['registry']['sha256'],
            'registry_published_through': legacy['registry'].get('published_through'),
            'source_evidence_counts': legacy['record_counts'],
            'row_counts': {'saddles_enriched': enriched_count,
                           'reaction_triplets': sum(triples.values()),
                           'hessian_mode_source_records': mode_records,
                           'mode_array_shards': mode_shards},
            'triplet_status_counts': dict(sorted(triples.items())),
            'mode_status_counts': dict(sorted(mode_stats.items())),
            'sha256': hashes,
            'scientific_qualification': 'UNQUALIFIED_SOURCE_ASSOCIATIONS',
            'scoring_reference_changed': False,
            'limitations': [
                'Saddles-enriched records preserve all observed triplets/modes as references, without selecting one.',
                'Triplets are per DoubleMin observation, not one arbitrarily chosen reaction per saddle.',
                'Minimum A and minimum B are the source minus and plus endpoints; chemical direction is not verified.',
                'Complete geometry is not proof of connectedness or endpoint convergence.',
                'Some Hessian modes have no direct geometry identity attestation or have free-DOF shapes.',
                'Mode sign and orientation are unmodified source conventions.',
                'No changes to prior SaddleBench scoring-reference files or eligibility rules.',
            ],
        }
        (out/'manifest_science.json').write_text(compact(manifest))
        (out/'SHA256SUMS.txt').write_text(''.join(
            hash_file(p)+'  '+p.relative_to(out).as_posix()+'\n' for p in sorted(out.rglob('*'))
            if p.is_file() and p.name != 'SHA256SUMS.txt'))
        return manifest
    except BaseException:
        # Keep partial diagnostic data for investigation, never remove production.
        (out/'INCOMPLETE_SCIENCE_EXPORT').write_text('This science layer is incomplete. Do not publish.\n')
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--mode-shard-size', type=int, default=MODES_PER_SHARD)
    args = p.parse_args()
    m = build(args.evidence, args.out, args.mode_shard_size)
    print('SCIENTIFIC_LAYER_EXPORT=PASS')
    print('SCIENCE_LAYER_OUTPUT='+str(args.out))
    print('SCIENCE_TRIPLETS='+str(m['row_counts']['reaction_triplets']))
    print('SCIENCE_MODES_RECORDED='+str(m['mode_status_counts'].get('RECORDED_MODE_UNQUALIFIED', 0)))


if __name__ == '__main__':
    main()
