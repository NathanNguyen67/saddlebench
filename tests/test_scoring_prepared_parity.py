"""Production score must preserve all legacy scientific fields and classifications."""
from __future__ import annotations
import copy
import json
import numpy as np
import pytest
import saddlebench.scoring.engine as engine
from saddlebench.matching.matcher import match as legacy_match
from examples.generate_fixture import geom


class LegacyMatcher:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def match(self, submission):
        return legacy_match(submission, self.snapshot)


def submission(geometry, i, *, start='min-A', pes='PES-A', parent='parent-A'):
    return {'geometry': geometry, 'submission_id': f'test-{i}',
            'starting_minimum_id': start, 'pes_id': pes,
            'parent_structure_id': parent}


def legacy_score(monkeypatch, submissions, refs):
    with monkeypatch.context() as m:
        m.setattr(engine, 'PreparedReferenceMatcher', LegacyMatcher)
        return engine.score(submissions, refs, 173.5)


def assert_same_report(actual, expected):
    assert actual.keys() == expected.keys()
    a = copy.deepcopy(actual)
    b = copy.deepcopy(expected)
    for row_a, row_b in zip(a['per_submission'], b['per_submission']):
        ma = row_a.pop('matches'); mb = row_b.pop('matches')
        assert len(ma) == len(mb)
        for x,y in zip(ma,mb):
            assert x.keys() == y.keys()
            assert x['reference_id'] == y['reference_id']
            assert x['identity_id'] == y['identity_id']
            assert abs(x['maxdev_angstrom']-y['maxdev_angstrom']) <= 1e-10
    assert a == b


def test_real_scoring_parity_all_states(monkeypatch, refs):
    queries = [
        submission(geom(pos),i) for i,pos in enumerate([0,0.05,0.1,2.,4.,6.,8.,10.,12.,14.,16.,18.15,30.,35.,100.])
    ]
    queries += [submission(geom(0), 'other-start', start='wrong'),
                submission(geom(0), 'other-pes', pes='wrong')]
    ref = legacy_score(monkeypatch, queries, refs)
    new = engine.score(queries, refs, 173.5)
    assert_same_report(new, ref)


def test_repeated_reference_snapshot_is_not_mutated(monkeypatch, refs):
    before = [json.dumps(r, sort_keys=True) for r in refs.records]
    queries = [submission(geom(float(i)*0.01),i) for i in range(50)]
    baseline = legacy_score(monkeypatch, queries, refs)
    actual = engine.score(queries, refs, 173.5)
    assert_same_report(actual, baseline)
    assert [json.dumps(r,sort_keys=True) for r in refs.records] == before


def test_periodic_skewed_and_boundary_parity(monkeypatch, refs):
    # Scope-independent invalid/periodic query behavior must match as well.
    queries = [submission(geom(0), 'good')]
    q = geom(0)
    q['pbc'] = [True, True, True]
    q['cell_angstrom'] = [[11.,0.,0.],[2.,10.,0.],[0.,1.1,8.]]
    queries.append(submission(q,'skew'))
    q2 = geom(2)
    q2['cell_angstrom'][0][0] += 0.06
    queries.append(submission(q2, 'cell'))
    assert_same_report(engine.score(queries, refs, 173.5), legacy_score(monkeypatch, queries, refs))
