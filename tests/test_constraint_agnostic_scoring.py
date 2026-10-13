"""Regression tests for geometry-only saddle matching with retained provenance."""
import hashlib
import json
from pathlib import Path

import pytest

from saddlebench.prototype.reaction_mvp import build, score
from saddlebench.prototype.release_mvp import diagnose
from tests.test_reaction_prototype_modes import dataset
from saddlebench.io.extxyz import SYMBOLS


def test_reaction_reference_unknown_constraints_credits_known_geometry(tmp_path):
    source = dataset(tmp_path)
    path = source / 'geometries.jsonl'
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    for item in rows:
        if item['geometry_id'].startswith('gs'):
            item['constraints_known'] = False
            item['fixed_indices'] = []
    raw = ''.join(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n' for row in rows).encode()
    path.write_bytes(raw)
    manifest_path = source / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['file_sha256']['geometries.jsonl'] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    target = tmp_path / 'reference'
    summary = build(source, target, 'all_saddles', 'pes:test')
    assert summary['eligible_reaction_identities'] >= 1
    reaction = json.loads((target / 'reference.jsonl').read_text().splitlines()[0])
    member = reaction['members'][0]
    assert member['constraints_metadata_verified'] is False
    g = member['geometry']
    # No constraints_known / fixed_indices in the EXTXYZ submission header.
    header = ('Properties=species:S:1:pos:R:3 submission_id=p0'
              ' starting_minimum_id=m0 parent_structure_id=synthetic:p1 pes_id=pes:test'
              ' Lattice="' + ' '.join(map(str, (v for cell in g['cell_angstrom'] for v in cell))) + '"'
              ' pbc="F F F"')
    lines = [str(len(g['numbers'])), header]
    for number, position in zip(g['numbers'], g['positions_angstrom']):
        lines.append(SYMBOLS[number - 1] + ' ' + ' '.join(map(str, position)))
    submission = tmp_path / 'submission.extxyz'
    submission.write_text('\n'.join(lines) + '\n')
    result = score(target, submission, 3600)
    assert result['eligible_unique_count'] == 1
    assert result['submissions'][0]['status'] == 'CREDIT'
    assert 'UNKNOWN_CONSTRAINTS' not in result['submissions'][0]['incompatibilities']


def test_diagnostic_catalog_unknown_constraints_can_classify(tmp_path):
    from examples.generate_fixture import geom
    prediction = {'geometry': geom(0)}
    reference = geom(0)
    reference['constraints_known'] = False
    record = {'saddle_id':'s','saddle_group_id':'g','starting_minimum_ids':['m'],
              'start_known':True,'hessian_status':'NON_FIRST_ORDER',
              'diagnostic_kind':'KNOWN_NON_FIRST_ORDER','geometry':reference,'barrier_eV':[]}
    result = diagnose({'status':'NO_MATCH'}, prediction,
                      {tuple(prediction['geometry']['numbers']): [record]})
    assert result['diagnostic'] == 'KNOWN_BAD_NON_FIRST_ORDER'
    assert result['penalty_key'] == 'g'


def test_unknown_constraints_do_not_override_scope(tmp_path):
    source = dataset(tmp_path)
    target = tmp_path / 'reference'
    build(source, target, 'all_saddles', 'pes:test')
    r = json.loads((target / 'reference.jsonl').read_text().splitlines()[0])
    g = r['members'][0]['geometry']
    header = ('Properties=species:S:1:pos:R:3 submission_id=p0'
              ' starting_minimum_id=wrong parent_structure_id=synthetic:p1 pes_id=pes:test'
              ' Lattice="' + ' '.join(map(str, (v for cell in g['cell_angstrom'] for v in cell))) + '"'
              ' pbc="F F F"')
    lines = [str(len(g['numbers'])), header]
    for n, pos in zip(g['numbers'], g['positions_angstrom']):
        lines.append(SYMBOLS[n - 1] + ' ' + ' '.join(map(str,pos)))
    submission = tmp_path / 'scope.extxyz'
    submission.write_text('\n'.join(lines) + '\n')
    assert score(target,submission,3600)['eligible_unique_count'] == 0


def test_native_traj_without_fixed_atoms_against_unknown_reference(tmp_path, monkeypatch):
    """Repacked ASE trajectory can lose FixAtoms without losing a geometry match."""
    import sys
    import types
    import numpy as np

    source = dataset(tmp_path)
    geometry_path = source / 'geometries.jsonl'
    rows = [json.loads(line) for line in geometry_path.read_text().splitlines() if line.strip()]
    for row in rows:
        if row['geometry_id'].startswith('gs'):
            row['constraints_known'] = False
            row['fixed_indices'] = []
    raw = ''.join(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n' for row in rows).encode()
    geometry_path.write_bytes(raw)
    manifest_path = source / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['file_sha256']['geometries.jsonl'] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    reference_dir = tmp_path / 'reference'
    build(source, reference_dir, 'all_saddles', 'pes:test')
    reaction = json.loads((reference_dir / 'reference.jsonl').read_text().splitlines()[0])
    geom = reaction['members'][0]['geometry']
    assert geom['constraints_known'] is False

    class FixAtoms:
        def get_indices(self):
            return []

    class RepackedAtoms:
        numbers = np.array(geom['numbers'])
        positions = np.array(geom['positions_angstrom'])
        cell = types.SimpleNamespace(array=np.array(geom['cell_angstrom']))
        pbc = np.array(geom['pbc'])
        constraints = []
        info = {}  # Typical repacked trajectory drops original case metadata.

    ase = types.ModuleType('ase')
    io = types.ModuleType('ase.io')
    constraints = types.ModuleType('ase.constraints')
    io.read = lambda *args, **kwargs: [RepackedAtoms()]
    constraints.FixAtoms = FixAtoms
    monkeypatch.setitem(sys.modules, 'ase', ase)
    monkeypatch.setitem(sys.modules, 'ase.io', io)
    monkeypatch.setitem(sys.modules, 'ase.constraints', constraints)

    traj = tmp_path / 'repacked.traj'
    traj.write_bytes(b'ASE_TEST_DOUBLE')
    output = score(reference_dir, traj, 3600,
                   start='m0', pes='pes:test', parent='synthetic:p1')
    assert output['eligible_unique_count'] == 1
    assert output['submissions'][0]['status'] == 'CREDIT'
    assert not output['submissions'][0]['incompatibilities']
