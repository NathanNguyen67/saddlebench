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
