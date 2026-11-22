from saddlebench.matching.geometry import core_drift_maxdev

def match(submission,snapshot):
    matches=[]; incompatible=set()
    candidates=(snapshot.by_numbers or {}).get(tuple(submission['geometry']['numbers']),()) if snapshot.by_numbers is not None else snapshot.records
    for r in candidates:
        try:distance=core_drift_maxdev(submission['geometry'],r['geometry'])
        except ValueError as e:
            incompatible.add(str(e));continue
        if distance<=0.2:
            matches.append({'reference_id':r['reference_id'],'identity_id':r['identity_id'],'maxdev_angstrom':distance})
    matches.sort(key=lambda m:(m['maxdev_angstrom'],m['reference_id']))
    identities={m['identity_id'] for m in matches}
    state=('NOT_IN_REFERENCE' if not matches else ('UNIQUE_REFERENCE_MATCH' if len(identities)==1 else 'AMBIGUOUS_REFERENCE_MATCH'))
    return {'state':state,'matches':matches,'incompatibilities':sorted(incompatible)}


class PreparedReferenceMatcher:
    """Reusable validated copies of immutable reference geometries.

    Context lasts for one scoring operation; no module-global cache. The public
    match() remains unchanged for applications that mutate input dictionaries.
    """

    def __init__(self, snapshot, *, prepare=None):
        from saddlebench.matching.geometry import prepare_geometry
        self.snapshot = snapshot
        self.prepared = {}
        self.prepare = prepare_geometry if prepare is None else prepare

    def match(self, submission, *, candidates=None, first=False, ordered_bounds=None, all_within=False):
        """With sorted lower bounds, only the nearest match is guaranteed, unless all_within: then the cutoff stays
        0.20 A and every reference within it is returned (candidates are still pruned by their lower bounds)."""
        from saddlebench.matching.geometry import core_drift_maxdev_prepared
        matches = []
        incompatible = set()
        if candidates is None:
            candidates = ((self.snapshot.by_numbers or {}).get(tuple(submission['geometry']['numbers']), ())
                          if self.snapshot.by_numbers is not None else self.snapshot.records)
        # Preserve original matching behavior for malformed query geometry.
        query = None
        query_error = None
        limit = 0.20
        candidates_with_bounds = (ordered_bounds if ordered_bounds is not None
                                  else ((0., row) for row in candidates))
        for lower, r in candidates_with_bounds:
            if lower > limit:
                break
            try:
                if query is None and query_error is None:
                    try:
                        query = self.prepare(submission['geometry'])
                    except ValueError as exc:
                        query_error = exc
                if query_error is not None:
                    raise query_error
                rid = id(r['geometry'])
                ref = self.prepared.get(rid)
                if ref is None:
                    ref = self.prepare(r['geometry'])
                    self.prepared[rid] = ref
                distance = core_drift_maxdev_prepared(query, ref)
            except ValueError as exc:
                incompatible.add(str(exc))
                continue
            if distance <= limit:
                if ordered_bounds is not None and not all_within:
                    limit = distance
                matches.append({'reference_id': r['reference_id'],
                                'identity_id': r['identity_id'],
                                'maxdev_angstrom': distance})
                if first:
                    break
        matches.sort(key=lambda m: (m['maxdev_angstrom'], m['reference_id']))
        identities = {m['identity_id'] for m in matches}
        state = ('NOT_IN_REFERENCE' if not matches else
                 ('UNIQUE_REFERENCE_MATCH' if len(identities) == 1 else 'AMBIGUOUS_REFERENCE_MATCH'))
        return {'state': state, 'matches': matches, 'incompatibilities': sorted(incompatible)}
