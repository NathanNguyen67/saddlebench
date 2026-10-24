"""Evidence-qualified independent scientific states. No user-supplied scientific claims are trusted."""
from __future__ import annotations
import hashlib
import math
import re
from saddlebench.matching.matcher import match, PreparedReferenceMatcher

SHA = re.compile(r'^[0-9a-f]{64}$')
ORDER=('CERTIFIED_FO','CERTIFIED_NON_FO','UNKNOWN')
BARRIER=('BELOW_1P5','AT_OR_ABOVE_1P5','UNKNOWN','NONCOMPARABLE_PES')
CONNECT=('CONNECTED_TO_START','DISCONNECTED_FROM_START','UNKNOWN')
PROVENANCE=('CANONICAL','NONCANONICAL','UNRESOLVED')

def _hashed(val):
    return isinstance(val,str) and SHA.fullmatch(val) is not None

def _finite(x):
    return type(x) in (int,float) and math.isfinite(x)

def _exact_setting(x,target):
    return _finite(x) and abs(float(x)-target)<1e-12

def _hessian_state(r):
    h=r.get('hessian')
    if not isinstance(h,dict):return 'UNKNOWN','HESSIAN_MISSING'
    if h.get('verified') is not True or h.get('schema')!='standalone_hessian_v2' or not _hashed(h.get('source_sha256')):
        return 'UNKNOWN','HESSIAN_UNVERIFIED'
    if h.get('certified_geometry_id')!=r['reference_id']:
        return 'UNKNOWN','HESSIAN_GEOMETRY_NOT_VERIFIED'
    if h.get('projection') not in ('canonical-translations','canonical-translations-and-constraints'):
        return 'UNKNOWN','HESSIAN_PROJECTION_UNVERIFIED'
    if not _exact_setting(h.get('negative_tolerance_ev_a2'),1e-6):
        return 'UNKNOWN','HESSIAN_TOLERANCE_UNVERIFIED'
    order=h.get('order')
    if type(order)!=int or order<0:return 'UNKNOWN','HESSIAN_ORDER_MISSING'
    return ('CERTIFIED_FO' if order==1 else 'CERTIFIED_NON_FO'),None

def _barrier_state(r,start,pes,parent):
    if r['pes_id'] is None or pes is None or r['pes_id']!=pes:
        return 'NONCOMPARABLE_PES',None,'PES_SCOPE_MISMATCH'
    if r['starting_minimum_id']!=start or r['parent_structure_id']!=parent or start is None:
        return 'UNKNOWN',None,'EXACT_START_SCOPE_UNVERIFIED'
    e=r.get('energy')
    if not isinstance(e,dict):return 'UNKNOWN',None,'ENERGY_MISSING'
    if any(not _finite(e.get(k)) for k in ('saddle_ev','minimum_ev')):
        return 'UNKNOWN',None,'ENERGY_UNVERIFIED'
    if not _hashed(e.get('source_sha256')):
        return 'UNKNOWN',None,'ENERGY_SOURCE_UNVERIFIED'
    if not e.get('saddle_pes_id') or not e.get('minimum_pes_id') or e['saddle_pes_id']!=pes or e['minimum_pes_id']!=pes:
        return 'NONCOMPARABLE_PES',None,'ENERGY_PES_MISMATCH'
    delta=float(e['saddle_ev'])-float(e['minimum_ev'])
    if not math.isfinite(delta):return 'UNKNOWN',None,'ENERGY_DIFFERENCE_UNVERIFIED'
    return ('BELOW_1P5' if delta<1.5 else 'AT_OR_ABOVE_1P5'),delta,None

def _canonical_doublemin(d):
    if not isinstance(d,dict) or d.get('executed') is not True:return False
    return (d.get('protocol')=='powell_damped_lbfgs' and d.get('powell_damped') is True
        and d.get('maximum_steps')==500 and type(d.get('maximum_steps'))==int
        and _exact_setting(d.get('displacement_angstrom'),0.25)
        and _exact_setting(d.get('target_fmax_ev_a'),0.0025)
        and d.get('certified_mode') is True and _hashed(d.get('source_sha256')))

def _connection_state(r,start,parent):
    d=r.get('doublemin')
    if not isinstance(d,dict) or d.get('executed') is not True:
        return 'UNKNOWN','UNRESOLVED','DOUBLEMIN_MISSING_OR_SKIPPED'
    if not _canonical_doublemin(d):
        return 'UNKNOWN','NONCANONICAL','DOUBLEMIN_NONCANONICAL_PROTOCOL'
    if start is None or r['starting_minimum_id']!=start or r['parent_structure_id']!=parent:
        return 'UNKNOWN','CANONICAL','DOUBLEMIN_START_UNVERIFIED'
    endpoints=d.get('endpoints')
    if not isinstance(endpoints,list) or len(endpoints)!=2:
        return 'UNKNOWN','CANONICAL','DOUBLEMIN_ENDPOINTS_MISSING'
    for item in endpoints:
        if not isinstance(item,dict) or item.get('converged') is not True or not _finite(item.get('final_fmax_ev_a')) or item['final_fmax_ev_a']>0.0025 or item['final_fmax_ev_a']<0:
            return 'UNKNOWN','CANONICAL','DOUBLEMIN_ENDPOINT_NOT_CERTIFIED'
    matches=[e.get('matched_minimum_id') for e in endpoints]
    if start in matches:return 'CONNECTED_TO_START','CANONICAL',None
    if all(isinstance(x,str) and x for x in matches):return 'DISCONNECTED_FROM_START','CANONICAL',None
    return 'UNKNOWN','CANONICAL','DOUBLEMIN_ENDPOINT_MAPPING_UNKNOWN'

def _published_event(r,watermark=None,cutoff=None):
    sources=r.get('evidence_sources')
    if not isinstance(sources,dict) or sources.get('published') is not True:
        return False
    if not isinstance(sources.get('producer_event_id'),str) or not sources['producer_event_id']:
        return False
    if not _hashed(sources.get('producer_event_sha256')):
        return False
    sequence=sources.get('event_sequence')
    if type(sequence)!=int or sequence<0:return False
    if watermark is not None and sequence>watermark:return False
    if cutoff is not None and sequence>cutoff:return False
    return True

def classify(r,start,pes,parent,watermark=None,cutoff=None):
    fo,fr=_hessian_state(r)
    barrier,delta,br=_barrier_state(r,start,pes,parent)
    conn,prov,cr=_connection_state(r,start,parent)
    reasons=[x for x in (fr,br,cr) if x]
    if delta is not None and delta<0:reasons.append('NEGATIVE_BARRIER_ANOMALY_REQUIRES_REVIEW')
    event_valid=_published_event(r,watermark,cutoff)
    if not event_valid:reasons.append('UNPUBLISHED_OR_UNVERIFIED_PRODUCER_EVENT')
    if r['pes_id']!=pes:reasons.append('REFERENCE_PES_SCOPE_MISMATCH')
    if r.get('uncertainty_reasons'):reasons.append('UNRESOLVED_REFERENCE_QUALIFICATION')
    if r['starting_minimum_id']!=start:reasons.append('REFERENCE_START_SCOPE_MISMATCH')
    eligible=(event_valid and (delta is None or delta>=0) and not r.get('uncertainty_reasons')
              and fo=='CERTIFIED_FO' and barrier=='BELOW_1P5' and conn=='CONNECTED_TO_START' and prov=='CANONICAL'
              and r['starting_minimum_id']==start and r['pes_id']==pes and r['parent_structure_id']==parent)
    return {'fo_state':fo,'barrier_state':barrier,'connection_state':conn,'provenance_state':prov,
            'barrier_ev':delta,'eligible':eligible,'reason_codes':sorted(set(reasons))}

def _empty_states():
    return {'fo_state':'UNKNOWN','barrier_state':'UNKNOWN','connection_state':'UNKNOWN',
            'provenance_state':'UNRESOLVED','barrier_ev':None,'eligible':False,'reason_codes':[]}

def score(submissions,snapshot,elapsed_seconds):
    if not _finite(elapsed_seconds) or elapsed_seconds<=0:
        raise ValueError('ELAPSED_SECONDS_MUST_BE_POSITIVE_FINITE')
    ids=set();results=[];credited=set();scopes=set();by_refid={r['reference_id']:r for r in snapshot.records}
    # One immutable reference snapshot per score invocation. Reuse validated
    # arrays across submissions; no process-global geometry cache or identity rule changes.
    matcher = PreparedReferenceMatcher(snapshot)
    # Each match event is independently classified, then dedup is applied per exact scope.
    for i,s in enumerate(submissions):
        subid=s.get('submission_id',f'submission-{i+1}')
        if not isinstance(subid,str) or not subid:raise ValueError('INVALID_SUBMISSION_ID')
        if subid in ids:raise ValueError('DUPLICATE_SUBMISSION_ID')
        ids.add(subid)
        for k in ('starting_minimum_id','pes_id','parent_structure_id'):
            if s.get(k) is not None and (not isinstance(s[k],str) or not s[k]):raise ValueError('INVALID_SUBMISSION_SCOPE')
        start=s.get('starting_minimum_id');pes=s.get('pes_id');parent=s.get('parent_structure_id')
        scopes.add((start,pes,parent))
        geom=matcher.match(s)
        states=_empty_states();matched_identity=None;matched_ref=None
        if geom['state']=='UNIQUE_REFERENCE_MATCH':
            matched_identity=geom['matches'][0]['identity_id']
            candidates=[by_refid[m['reference_id']] for m in geom['matches']]
            scoped=[r for r in candidates if r['starting_minimum_id']==start and r['pes_id']==pes and r['parent_structure_id']==parent]
            if scoped:
                evaluations=[classify(r,start,pes,parent,snapshot.manifest['registry_watermark'],snapshot.manifest['event_cutoff']) for r in scoped]
                # Conflicts among matched members of one sharing identity cannot be resolved by shortest distance.
                scientific_summaries={(x['fo_state'],x['barrier_state'],x['connection_state'],x['provenance_state'],x['eligible']) for x in evaluations}
                if len(scientific_summaries)==1:
                    states=dict(evaluations[0]);matched_ref=scoped[0]['reference_id']
                else:
                    states['reason_codes'].append('CONFLICTING_REFERENCE_GROUP_EVIDENCE')
            else:
                states=classify(candidates[0],start,pes,parent,snapshot.manifest['registry_watermark'],snapshot.manifest['event_cutoff'])
                states['eligible']=False
                states['reason_codes'].append('NO_REFERENCE_IN_EXACT_SCOPE')
        elif geom['state']=='AMBIGUOUS_REFERENCE_MATCH':states['reason_codes'].append('AMBIGUOUS_GEOMETRY_IDENTITIES')
        else:states['reason_codes'].append('NOT_IN_OBSERVED_REFERENCE')
        credit_key=(start,pes,parent,matched_identity)
        if states['eligible']:
            if credit_key in credited:
                states['eligible']=False
                states['reason_codes'].append('DUPLICATE_REFERENCE_IDENTITY')
            else:credited.add(credit_key)
        if start is None:states['reason_codes'].append('SUBMISSION_START_UNKNOWN')
        if pes is None:states['reason_codes'].append('SUBMISSION_PES_UNKNOWN')
        if parent is None:states['reason_codes'].append('SUBMISSION_PARENT_UNKNOWN')
        results.append({'submission_id':subid,'starting_minimum_id':start,'pes_id':pes,'parent_structure_id':parent,
                'geometry_state':geom['state'],'matched_identity_id':matched_identity,'matched_reference_id':matched_ref,
                'matches':geom['matches'],'incompatibilities':geom['incompatibilities'],**states,
                'reason_codes':sorted(set(states['reason_codes']))})
    counted=len(credited)
    coverage={'value':None,'numerator':counted,'denominator':None,'reason':'NO_FIXED_SCOPE'}
    if len(scopes)==1:
        start,pes,parent=next(iter(scopes))
        if start is not None and pes is not None and parent is not None:
            eligible,complete=snapshot.eligible_scoped(start,pes,parent)
            if not complete:coverage['reason']='REFERENCE_ELIGIBILITY_COVERAGE_UNVERIFIED'
            elif not eligible:coverage['reason']='NO_OBSERVED_ELIGIBLE_REFERENCE'
            else:coverage={'value':counted/len(eligible),'numerator':counted,'denominator':len(eligible),
                           'reason':'OBSERVED_REFERENCE_ONLY'}
    elif len(scopes)>1:coverage['reason']='MIXED_SCOPES'
    # FO fraction counted per identity, excluding ambiguities and duplicates.
    fo_identities={};fo_unknown=0
    for r in results:
        if r['geometry_state']!='UNIQUE_REFERENCE_MATCH' or r['matched_identity_id'] is None:continue
        key=(r['starting_minimum_id'],r['pes_id'],r['parent_structure_id'],r['matched_identity_id'])
        fo_identities.setdefault(key,set()).add(r['fo_state'])
    nfo=sum(v=={'CERTIFIED_FO'} for v in fo_identities.values())
    nnon=sum(v=={'CERTIFIED_NON_FO'} for v in fo_identities.values())
    fo_unknown=len(fo_identities)-nfo-nnon
    total=nfo+nnon+fo_unknown
    return {'schema_version':'saddlebench-score-v1','reference_version':snapshot.manifest['reference_version'],
        'reference_records_sha256':snapshot.manifest['records_sha256'],'registry_sha256':snapshot.manifest['registry_sha256'],
        'registry_watermark':snapshot.manifest['registry_watermark'], 'scoring_policy_id':snapshot.manifest['scoring_policy_id'],
        'elapsed_seconds':elapsed_seconds,'elapsed_wall_hours':elapsed_seconds/3600.0,
        'submission_count':len(results),'eligible_unique_count':counted,
        'primary_score_per_wall_hour':counted*3600.0/elapsed_seconds,
        'coverage':coverage,
        'fo_evaluated':{'certified_fo':nfo,'certified_non_fo':nnon,'unknown':fo_unknown,
                        'resolved_fraction_of_unique_matches':(nfo+nnon)/total if total else None,
                        'fo_fraction_among_evaluated':nfo/(nfo+nnon) if nfo+nnon else None},
        'per_submission':results}
