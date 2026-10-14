"""Read-only, scientifically DISTINCT first-hit coverage/classification experiment.

Not a drop-in replacement for W2 / FAST v3 scoring: it stops before complete
reaction ambiguity and diagnostic-group enumeration. A greedy known-bad flag
is not a certified W2 penalty. No canonical metrics are relabeled.
"""
from __future__ import annotations
import hashlib,json,os,tempfile,time
from collections import Counter,defaultdict
from pathlib import Path

MATCH_THRESHOLD=0.2
SCHEMA='saddlebench-greedy-first-hit-experiment-v1'
NONFO='KNOWN_NON_FIRST_ORDER'


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def _cached_index(members, cache_root, prefix):
    """Immutable per-scope index. Cache binding includes exact ordered geometry and group IDs.

    Source-hash validation is REQUIRED on load; no atomic overwrite or stale fallback.
    """
    from saddlebench.matching.group_radius_exists import source_identity,build_index,validate_index
    root=Path(cache_root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    identity=source_identity(members)
    path=root/(prefix+'_'+identity+'.json')
    if path.is_symlink():raise ValueError('GREEDY_INDEX_SYMLINK_UNTRUSTED')
    state='LOADED'
    if path.is_file():
        index=json.loads(path.read_text())
    else:
        state='BUILT'
        index=build_index(members,'first')
        encoded=(json.dumps(index,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
        fd,tmp=tempfile.mkstemp(dir=root,prefix='.pending_greedy_')
        try:
            with os.fdopen(fd,'wb') as f:f.write(encoded);f.flush();os.fsync(f.fileno())
            try:os.link(tmp,path)
            except FileExistsError:state='RACE_LOADED'
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        index=json.loads(path.read_text())
    validate_index(index,members)
    return index,{'state':state,'groups':len(index['groups']),'members':len(members),'sha256':_sha(path)}


class Stage:
    def __init__(self,members,cache,tag,pinned_index=None):
        from saddlebench.matching.group_radius_exists import GroupCatalog,validate_index
        self.members=members
        start=time.perf_counter()
        if pinned_index is None:
            index,self.provenance=_cached_index(members,cache,tag)
        else:
            validate_index(pinned_index,members)
            index=pinned_index
            self.provenance={'state':'VERIFIED_PRIOR_ELIGIBLE_CACHE',
                             'groups':len(index['groups']),'members':len(members)}
        self.catalog=GroupCatalog(members,index)
        self.provenance['load_seconds']=time.perf_counter()-start
        self.stats=Counter()

    def find(self,geometry,batch_size):
        hit=self.catalog.find_first(geometry,batch_size=batch_size,order='near',group_prune=True)
        for name,value in hit.items():
            if name not in ('found','match_index') and isinstance(value,int):self.stats[name]+=value
        self.stats['queries']+=1
        if hit.get('uncertain_scalar',0):
            raise ValueError('GREEDY_UNCERTAIN_SCALAR_COMPARISON')
        if bool(hit.get('found')):
            i=hit.get('match_index')
            if type(i) is not int or i<0 or i>=len(self.members):
                raise ValueError('GREEDY_MATCH_INDEX_MISSING')
            self.stats['hits']+=1
            return self.members[i][1],hit
        return None,hit


def _group_eligible(reactions,case):
    """Exact W2 structure/start/PES/numbers scoping, with original ref order."""
    by_scope=defaultdict(list)
    for r in reactions:
        if r['structure_id']!=case['structure_id'] or r['starting_minimum_id']!=case['starting_minimum_id'] or r['pes_id']!=case['pes_id']:
            continue
        for member in r['members']:
            rec=dict(member)
            rec['reaction_id']=r['reaction_id']
            by_scope[tuple(rec['geometry']['numbers'])].append((r,rec))
    return by_scope


def _group_diag(items):
    by_numbers=defaultdict(list)
    for rec in items:
        by_numbers[tuple(rec['geometry']['numbers'])].append((None,rec))
    return by_numbers


def _build_stages(groups,cache_root,tag,overrides=None):
    overrides=overrides or {}
    return {key:Stage(members,cache_root,f'{tag}_{hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]}',
                      pinned_index=overrides.get(key)) for key,members in groups.items()}


def _load_pinned_eligible_indices(case,groups):
    """Reuse previous certified first-center radii across the exact frozen scope."""
    from saddlebench.matching.ab_store import scope_id,geometry_digest
    filename=os.environ.get('SADDLEBENCH_GROUP_INDEX_FILE')
    if not filename:return {}
    path=Path(filename)
    if not path.is_file() or path.is_symlink():raise ValueError('PINNED_GROUP_CACHE_MISSING_OR_SYMLINK')
    data=json.loads(path.read_text())
    sha=os.environ.get('SADDLEBENCH_REFERENCE_SHA256')
    if data.get('schema')!='saddlebench-group-cache-v1' or data.get('mode')!='first' or data.get('refsha')!=sha:
        raise ValueError('PINNED_GROUP_CACHE_WRONG_PROVENANCE')
    override={}
    for numbers,members in groups.items():
        scope=(case['structure_id'],case['starting_minimum_id'],case['pes_id'],numbers)
        entry=data['scopes'].get(scope_id(scope))
        if entry is None:raise ValueError('PINNED_GROUP_SCOPE_MISSING')
        if entry.get('geometry_sha256')!=geometry_digest(members):raise ValueError('PINNED_GROUP_SCOPE_CHANGED')
        override[numbers]=entry['index']
    return override


def run_case(public,private,case,trajectory,seconds,nonfo_file,*,mode,batch_size,cache_root,diagnostic_order='nonfo-first'):
    """Return reduced greedy observations and evidence counters.

    Order: eligible -> known nonFO -> (full only) remainder diagnostics.
    No credit is given for repeated first-hit reaction IDs. Unique reaction
    results may differ from W2 if a prediction is ambiguous across reactions.
    No authoritative B or W2 score is computed.
    """
    if mode not in ('coverage','score','greedy_fast','greedy_full'):raise ValueError('INVALID_GREEDY_MODE')
    if diagnostic_order not in ('nonfo-first','catalog-first'):raise ValueError('INVALID_GREEDY_DIAGNOSTIC_ORDER')
    from saddlebench.prototype import reaction_mvp as rm,release_mvp as rel
    from saddlebench.matching.fast_reference import load_nonfo_for_case
    ref=Path(private)/'reference'
    manifest,reactions=rm.load_reference(ref)
    if json.loads((Path(private)/'private_manifest.json').read_text())['source_registry_sha256']!=case['registry_sha256']:
        raise ValueError('GREEDY_REGISTRY_IDENTITY_MISMATCH')
    preds=rm.read_submissions(trajectory,start=case['starting_minimum_id'],parent=case['structure_id'],pes=case['pes_id'])
    eligible_groups=_group_eligible(reactions,case)
    eligible=_build_stages(eligible_groups,cache_root,'eligible',
                           _load_pinned_eligible_indices(case,eligible_groups))
    nonfo_records=load_nonfo_for_case(nonfo_file,case) if mode!='coverage' else []
    nonfo=_build_stages(_group_diag(nonfo_records),cache_root,'known_nonfo')
    remaining={}
    all_diag={}
    if mode=='greedy_full':
        lookups=rel._prepare_diag_lookup(private,case)
        if diagnostic_order=='catalog-first':
            all_diag=_build_stages({tuple(k):[(None,v) for v in values] for k,values in lookups.items()},cache_root,'all_diagnostics')
        else:
            remaining=_build_stages({tuple(k):[(None,v) for v in values if v['diagnostic_kind']!=NONFO] for k,values in lookups.items()},cache_root,'remaining_diagnostics')
    stages={'eligible':eligible,'nonfo':nonfo,'remaining':remaining,'all_diagnostics':all_diag}
    entries=[]; credited=set(); seen_nonfo=set();seen_bad=set()
    status_count=Counter(); cached_preparation={};diag_kinds=Counter()
    for stage_name,scope_map in stages.items():
        cached_preparation[stage_name]={str(key):stage.provenance for key,stage in scope_map.items()}
    matching_started=time.perf_counter()
    for pred in preds:
        numbers=tuple(pred['geometry']['numbers'])
        rec={'submission_id':pred['submission_id'],'stage':None,'category':None,
             'matched_reaction_id':None,'status':'UNKNOWN_UNMATCHED',
             'exact_maxdev_threshold_A':MATCH_THRESHOLD}
        picked=None
        if numbers in eligible:
            picked,_=eligible[numbers].find(pred['geometry'],batch_size)
        if picked is not None:
            rid=picked['reaction_id'];rec['matched_reaction_id']=rid
            rec['stage']='eligible';rec['category']='CONNECTED_RATE_RELEVANT_REACTION'
            if rid in credited:rec['status']='DUPLICATE'
            else:rec['status']='CREDIT';credited.add(rid)
        else:
            if diagnostic_order=='catalog-first' and mode=='greedy_full':
                if numbers in all_diag:
                    picked,_=all_diag[numbers].find(pred['geometry'],batch_size)
                if picked is not None:
                    rec['stage']='diagnostic';rec['category']=picked['diagnostic_kind'];rec['status']='CATALOG_FIRST_HIT'
            else:
                if numbers in nonfo:picked,_=nonfo[numbers].find(pred['geometry'],batch_size)
                if picked is not None:
                    rec['stage']='known_nonfo';rec['category']=NONFO;rec['status']='NONFO_FIRST_HIT'
                elif mode=='greedy_full' and numbers in remaining:
                    picked,_=remaining[numbers].find(pred['geometry'],batch_size)
                    if picked is not None:
                        rec['stage']='remaining_diagnostics';rec['category']=picked['diagnostic_kind'];rec['status']='CATALOG_FIRST_HIT'
            if picked is not None:
                if rec['category']==NONFO:
                    rec['start_verified']=bool(picked.get('start_known'))
                    seen_nonfo.add(str(picked.get('saddle_group_id') or picked.get('saddle_id')))
                if rec['category'] in (NONFO,'KNOWN_CONNECTED_HIGH_BARRIER') and bool(picked.get('start_known')):
                    seen_bad.add(str(picked.get('saddle_group_id') or picked.get('saddle_id')))
                diag_kinds[str(rec['category'])]+=1
        status_count[rec['status']]+=1
        entries.append(rec)
    stage_stats={key:{str(k):dict(stage.stats) for k,stage in part.items()}
                 for key,part in stages.items()}
    total=Counter()
    for groups in stages.values():
        for stage in groups.values():
            for name,value in stage.stats.items():total[name]+=value
    k=int(case['known_reaction_count']);u=len(credited)
    if u>k:raise ValueError('GREEDY_CREDIT_EXCEEDS_CASE_K')
    bad_preview=len(seen_bad)
    t=float(seconds)
    coverage_preview=100*(u/k)**2*3600/t if k else None
    penalized_preview=(coverage_preview*(u/(u+2*bad_preview) if u else 0)
                        if coverage_preview is not None else None)
    matching_seconds=time.perf_counter()-matching_started
    return {'schema_version':SCHEMA,'mode':mode,'diagnostic_order':diagnostic_order,
            'case_id':case['case_id'],'structure_id':case['structure_id'],
            'known_eligible_reactions_K':k,'greedy_unique_reactions_U':u,
            'greedy_nonfo_first_hit_groups':len(seen_nonfo),
            'greedy_potential_bad_first_hit_groups':len(seen_bad),
            'known_bad_unique_B_full':None,'self_reported_elapsed_seconds':t,
            'greedy_coverage_only_preview_per_hour':coverage_preview,
            'greedy_first_hit_bad_preview_per_hour':penalized_preview,
            'entries':entries,'stage_counts':dict(status_count),'diagnostic_kinds':dict(diag_kinds),
            'matching_only_seconds':matching_seconds,
            'work_counts':dict(total),'stage_work_counts':stage_stats,'stage_preparation':cached_preparation,
            'warning':('Greedy first hit is a new experimental scoring definition. Cross-reaction ambiguity, '
                       'competing neutral diagnostics, other matched groups and W2 authoritative penalties '
                       'are NOT established. Potential bad counts are unverified indicators, not B.')}
