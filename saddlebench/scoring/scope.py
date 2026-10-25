"""Dataset-independent reaction scope for a pre-adjudicated reference catalog.

Canonical identity is not defined by a search origin. Structure/PES scope is
required; starting-minimum scoping is opt-in for controlled historical studies.
This module neither classifies Hessians nor changes scientific eligibility.
"""
from __future__ import annotations


def matches(reaction, case, *, require_starting_minimum=False):
    if reaction.get('structure_id') != case.get('structure_id'):
        return False
    if reaction.get('pes_id') != case.get('pes_id'):
        return False
    if require_starting_minimum:
        start = case.get('starting_minimum_id')
        if not isinstance(start, str) or not start:
            raise ValueError('STARTING_MINIMUM_REQUIRED_FOR_SCOPED_SCORING')
        return reaction.get('starting_minimum_id') == start
    return True


def scoped_ids(reactions, case, *, require_starting_minimum=False):
    ids = set()
    for reaction in reactions:
        if matches(reaction, case, require_starting_minimum=require_starting_minimum):
            rid = reaction.get('reaction_id')
            if not isinstance(rid, str) or not rid:
                raise ValueError('INVALID_REFERENCE_REACTION_ID')
            ids.add(rid)
    return ids
