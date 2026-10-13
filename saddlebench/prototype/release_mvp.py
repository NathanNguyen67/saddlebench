"""Public benchmark case staging and private read-only scoring/diagnostics.

MVP policy is a new benchmark metric, not a SaddleMill scientific-method change.
The published registry remains authoritative; no calculators are run.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil

from saddlebench.prototype import reaction_mvp as rm
from saddlebench.matching.geometry import core_drift_maxdev
from saddlebench.reference.snapshot import canonical

MATCH_A = 0.2
PENALTY_WEIGHT = 2.0
FO_STATES = frozenset(('FIRST_ORDER', 'FIRST_ORDER_SADDLE', 'FO_CERTIFIED'))
NON_FO_STATES = frozenset(('NON_FIRST_ORDER', 'HIGHER_ORDER', 'HIGHER_ORDER_SADDLE', 'NOT_FIRST_ORDER'))
CASE_COLUMNS = ('case_id','structure_id','starting_minimum_id','formula','n_atoms',
                'minimum_traj','known_reaction_count','n_redundant_tail_mean',
                'exploration_confidence_heuristic','confidence_basis','pes_id','registry_sha256')


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1<<20), b''): h.update(chunk)
    return h.hexdigest()


def verify_files(root, filenames):
    m=json.loads((Path(root)/'manifest.json').read_text())
    for fn in filenames:
        dig=(m.get('file_sha256') or {}).get(fn)
        p=Path(root)/fn
        if dig is None or p.is_symlink() or not p.is_file() or sha(p)!=dig:
            raise ValueError('BAD_EVIDENCE_HASH:'+fn)
    return m


def formula(numbers):
    from ase.data import chemical_symbols
    c=Counter(int(x) for x in numbers)
    if not c or any(z<1 or z>=len(chemical_symbols) for z in c): raise ValueError('INVALID_ATOMIC_NUMBER')
    return ''.join(chemical_symbols[z]+(str(c[z]) if c[z]>1 else '') for z in sorted(c))


def confidence_from_counts(counts, *, seed=0, shuffles=256):
    """Mean number of redundant *eligible observations* after last new identity.

    This does not claim to reconstruct original chronological Search attempts or
    establish a calibrated probabilistic confidence level.
    """
    data=[]
    for key,n in sorted(counts.items()):
        if not isinstance(n,int) or n<0:raise ValueError('INVALID_OBSERVATION_COUNT')
        data.extend([key]*n)
    if len(data)<2 or len(counts)<1:return None,None
    rng=random.Random(seed)
    tails=[]
    distinct=set(counts)
    for _ in range(shuffles):
        seq=data.copy();rng.shuffle(seq)
        observed=set();last_new=-1
        for i,val in enumerate(seq):
            if val not in observed:
                observed.add(val);last_new=i
                if observed==distinct:break
        tails.append(len(seq)-last_new-1)
    avg=sum(tails)/len(tails)
    # User's example: n_red=200 -> 1 - 1/(0.1*200) = 95%.
    conf=max(0., min(1., 1.-10./avg)) if avg>0 else None
    return avg,conf


def score_metric(unique_count, known_count, bad_count, seconds):
    if seconds<=0 or not math.isfinite(seconds):raise ValueError('INVALID_ELAPSED_SECONDS')
    if known_count<0 or not 0<=unique_count<=known_count or bad_count<0:
        raise ValueError('INVALID_SCORE_COUNTS')
    if known_count==0:return None
    coverage=unique_count/known_count
    precision=unique_count/(unique_count+PENALTY_WEIGHT*bad_count) if unique_count else 0.
    return 100.0*coverage*coverage*precision*3600.0/seconds


def safe_new_dir(path, protected):
    p=Path(path).absolute()
    for ancestor in (p,*p.parents):
        if ancestor.is_symlink():raise ValueError('SYMLINK_IN_DESTINATION')
    for root in protected:
        r=Path(root).absolute()
        if p==r or r in p.parents:raise ValueError('DESTINATION_OVER_SOURCE')
    p.mkdir(parents=True,exist_ok=False,mode=0o700)
    return p


def _case_key(parent,minimum):return (str(parent),str(minimum))


def _diag_kind(s, reactions_for_saddle):
    status=str(s.get('hessian_status') or '').upper()
    if status in NON_FO_STATES:return 'KNOWN_NON_FIRST_ORDER'
    if status not in FO_STATES:return 'UNKNOWN_HESSIAN_ORDER'
    # Only a registered connected outcome with a known barrier can be penalized
    # as too high energy. Known unconnected first-order is neutral.
    connected=[rx for rx in reactions_for_saddle if rx.get('connected_to_canonical_minimum') in (1,True)]
    if connected and all(isinstance(rx.get('barrier_eV'),(int,float)) and math.isfinite(rx['barrier_eV']) and rx['barrier_eV']>=1.5 for rx in connected):
        return 'KNOWN_CONNECTED_HIGH_BARRIER'
    if not connected and reactions_for_saddle:
        return 'KNOWN_FIRST_ORDER_DISCONNECTED'
    return 'FIRST_ORDER_UNQUALIFIED_OR_UNVERIFIED'


def stage(evidence, reference, inputs, output):
    from ase.io import write
    from saddlebench.prototype.reaction_mvp import _atoms_from_geometry
    src=Path(evidence);refroot=Path(reference);inroot=Path(inputs)
    em=verify_files(src, ('minima.jsonl','geometries.jsonl','saddles.jsonl','reactions.jsonl','doublemin_observations.jsonl'))
    rman,refs=rm.load_reference(refroot)
    iman=json.loads((inroot/'manifest.json').read_text())
    for fn,digest in iman['file_sha256'].items():
        if sha(inroot/fn)!=digest:raise ValueError('BAD_INPUTS_HASH:'+fn)
    regsha=em['registry']['sha256']
    if regsha!=rman['registry_sha256'] or regsha!=iman['source_registry_sha256']:
        raise ValueError('SNAPSHOT_IDENTITY_MISMATCH')
    if rman['mode']!='all_saddles':raise ValueError('NOT_ALL_SADDLES_PROTOTYPE')
    allmins=list(rm.rows(inroot,'all_minima.jsonl'))
    if len(allmins)!=len({m['minimum_id'] for m in allmins}):raise ValueError('DUPLICATE_MINIMA')
    if any(m['geometry']['constraints_known'] is not True for m in allmins):
        raise ValueError('NOT_ALL_MINIMA_HAVE_VERIFIED_CONSTRAINTS')
    rby=defaultdict(list)
    for r in refs:
        rby[_case_key(r['structure_id'],r['starting_minimum_id'])].append(r)
    obs_to_reaction=defaultdict(Counter)
    for case,items in rby.items():
        for r in items:
            for oid in set(r['confirming_observation_ids']):
                obs_to_reaction[case][r['reaction_id']]+=1
    # The reference contains all eligible obs IDs, but duplicates per reaction
    # are removed at the builder boundary. No attempt chronology is assumed.
    private=Path(output).absolute()
    safe_new_dir(private,(src,refroot,inroot))
    (private/'public'/'minima').mkdir(parents=True,mode=0o700)
    (private/'private'/'reference').mkdir(parents=True,mode=0o700)
    try:
        cases=[]
        for i,m in enumerate(sorted(allmins,key=lambda r:(r['parent_structure_id'],r['minimum_id'])),1):
            cid=f'case{i:04d}';g=m['geometry'];case=_case_key(m['parent_structure_id'],m['minimum_id'])
            atom=_atoms_from_geometry(g,{'case_id':cid,'parent_structure_id':case[0],
                    'starting_minimum_id':case[1],'pes_id':rman.get('pes_id','REGISTRY_PES_SCOPE_UNVERIFIED')})
            dest=private/'public'/'minima'/f'{cid}.traj'
            if dest.exists():raise FileExistsError(dest)
            write(str(dest),atom,format='traj')
            # Stable, reproducible simulation seed tied to source and case.
            seed=int(hashlib.sha256((regsha+cid).encode()).hexdigest()[:16],16)
            avg,conf=confidence_from_counts(dict(obs_to_reaction.get(case,{})),seed=seed)
            row=dict(case_id=cid,structure_id=case[0],starting_minimum_id=case[1],
                  formula=formula(g['numbers']),n_atoms=len(g['numbers']),minimum_traj=f'minima/{cid}.traj',
                  known_reaction_count=len(rby.get(case,[])),
                  n_redundant_tail_mean='' if avg is None else round(avg,4),
                  exploration_confidence_heuristic='' if conf is None else round(conf,6),
                  confidence_basis='SHUFFLED_ELIGIBLE_OBSERVATIONS_PROXY' if avg is not None else 'INSUFFICIENT_REPEAT_EVIDENCE',
                  pes_id='REGISTRY_PES_SCOPE_UNVERIFIED',registry_sha256=regsha)
            cases.append(row)
        with (private/'public'/'cases.csv').open('w',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=CASE_COLUMNS);w.writeheader();w.writerows(cases)
        (private/'public'/'submission_template.csv').write_text(
            'case_id,trajectory,elapsed_seconds\ncase0001,predictions_case0001.traj,3600\n',encoding='utf-8')
        shutil.copy2(refroot/'reference.jsonl',private/'private'/'reference'/'reference.jsonl')
        shutil.copy2(refroot/'manifest.json',private/'private'/'reference'/'manifest.json')
        shutil.copy2(refroot/'starting_minima.jsonl',private/'private'/'reference'/'starting_minima.jsonl')
        if sha(private/'private'/'reference'/'reference.jsonl')!=rman['reference_sha256']:
            raise ValueError('PRIVATE_REFERENCE_COPY_MISMATCH')
        saddles=list(rm.rows(src,'saddles.jsonl'))
        registered_groups=defaultdict(set)
        for saddle in saddles:
            if saddle.get('saddle_group_id') is not None:
                registered_groups[(saddle['structure_id'],saddle['saddle_group_id'])].add(saddle['canonical_saddle_id'])
        saddle_id=set(s['canonical_saddle_id'] for s in saddles)
        group_of={s['canonical_saddle_id']:s for s in saddles}
        rx_by_id={r['canonical_reaction_id']:r for r in rm.rows(src,'reactions.jsonl')}
        rx_for_s=defaultdict(dict)
        for obs in rm.rows(src,'doublemin_observations.jsonl'):
            sid=obs.get('canonical_saddle_id');rid=obs.get('canonical_reaction_id')
            if sid in saddle_id and rid in rx_by_id:
                rx_for_s[sid][rid]=rx_by_id[rid]
        target_geomids={s['geometry_id'] for s in saddles if s.get('geometry_id')}
        geo={}
        for g in rm.rows(src,'geometries.jsonl'):
            if g['geometry_id'] in target_geomids:geo[g['geometry_id']]=g
        counts=Counter()
        with gzip.open(private/'private'/'diagnostic_saddles.jsonl.gz','wt',encoding='utf8') as out:
            for s in saddles:
                g=geo.get(s.get('geometry_id'))
                if not g:counts['missing_geometry']+=1;continue
                kind=_diag_kind(s,list(rx_for_s[s['canonical_saddle_id']].values()))
                counts[kind]+=1
                obsstart=s.get('observed_starting_minimum_ids') or []
                line={'saddle_id':s['canonical_saddle_id'],'structure_id':s['structure_id'],
                      'starting_minimum_ids':obsstart,'saddle_group_id':s.get('saddle_group_id'),
                      'registered_saddle_group_size':(
                          len(registered_groups[(s['structure_id'],s['saddle_group_id'])])
                          if s.get('saddle_group_id') is not None else None),
                      'hessian_status':s.get('hessian_status'),'diagnostic_kind':kind,
                      'geometry':{'numbers':g['ordered_numbers'],'positions_angstrom':g['positions_angstrom'],
                          'cell_angstrom':g['cell_angstrom'],'pbc':g['pbc'],
                          'constraints_known':g.get('constraints_known') is True,
                          'fixed_indices':g.get('fixed_indices') if g.get('constraints_known') is True else []},
                      'linked_reaction_ids':sorted(rx_for_s[s['canonical_saddle_id']]),
                      'barrier_eV':sorted({r['barrier_eV'] for r in rx_for_s[s['canonical_saddle_id']].values() if isinstance(r.get('barrier_eV'),(int,float))})}
                out.write(canonical(line)+'\n')
        private_meta={'schema_version':'saddlebench-hf-preview-v1','source_registry_sha256':regsha,
              'source_publication_watermark':em['registry'].get('published_through'),
              'mode':'all_saddles','case_count':len(cases),'eligible_reactions':len(refs),
              'diagnostic_saddle_status_counts':dict(counts),'metric_formula':'100*(U/K)^2*(U/(U+2B))*(3600/seconds)',
              'scientific_limits':{'maxdev_A':0.2,'saddle_fmax_eV_A':0.005,'endpoint_fmax_eV_A':0.0025,
                                   'connected_required':True,'barrier_strictly_below_eV':1.5},
              'limits':['PES identity unverified. No authoritative leaderboard comparison across PES.',
                        'Negative penalties require unambiguous known bad reference matches.',
                        'Exploration confidence is an uncalibrated shuffled-eligible-observations heuristic.',
                        'Reference is private; public minima alone cannot perform hidden scoring.']}
        (private/'private'/'private_manifest.json').write_text(canonical(private_meta)+'\n',encoding='utf8')
        _write_public_readme(private/'public',private_meta)
        shutil.copy2(Path(__file__).parent/'select_case.py',private/'public'/'select_case.py')
        (private/'public'/'manifest.json').write_text(canonical({
             'schema_version':'saddlebench-public-inputs-v1','registry_sha256':regsha,
             'case_count':len(cases),'scorer_mode':'all_saddles',
             'case_index_sha256':sha(private/'public'/'cases.csv'),
             'inputs_traj_count':len(cases),'published_private_reference':False})+'\n',encoding='utf8')
        (private/'READY.json').write_text(canonical({'status':'STAGED_NOT_PUBLISHED',**private_meta,
                'public_folder':str(private/'public'),'private_folder':str(private/'private')})+'\n',encoding='utf8')
    except Exception:
        (private/'STAGE_INCOMPLETE').write_text('See traceback; do not upload.\n')
        raise
    return private_meta


def _write_public_readme(folder,meta):
    text='''---
task_categories:
- other
pretty_name: SaddleBench Prototype Starting Minima
---
# SaddleBench: starting-minimum inputs (prototype)

**Prototype, registry-derived; PES/calculator identity has NOT yet been independently established.**
This is a public starting-minimum dataset, **not a complete publicly released ground-truth saddle set**.

## Choose a minimum

Open `cases.csv`. Each row is one starting structure/minimum. To receive one ASE `.traj` file, either download `minima/<case_id>.traj` directly or run:

```bash
python select_case.py --case-id case0001 --out my_starting_minimum.traj
```

With ASE installed, read the trajectory as `ase.io.read('my_starting_minimum.traj')`.
The frames contain `case_id`, `parent_structure_id`, `starting_minimum_id`, and a placeholder `pes_id` in `atoms.info`. Exact ASE FixAtoms constraints are preserved. **Do not resolve or substitute a different PES identity silently.**

## Submit predictions

Prepare a `.traj` containing candidate saddle geometries for one case. Submit it with its case ID, the wall time for that case (seconds), and your method/PES details. A `submission_template.csv` shows the format. The private reference is needed to run the diagnostic grader; it is not included here.

## How to interpret reported scores

Credit requires a unique reaction match within core-drift maxdev 0.2 Å to the frozen known connected, rate-relevant (<1.5 eV) reference; saddle fmax <=0.005 eV/Å and both DoubleMin endpoints fmax <=0.0025 eV/Å in the reference. Historical DoubleMin protocols can qualify in `all_saddles`; `canonical_only` can be selected in a future reference release. Repeated submissions of the same reaction receive no additional credit.

For a case with K>0 known eligible reactions, let U be uniquely matched reactions, B be uniquely matched **known non-first-order** or **known connected high-barrier** saddle identities (counted only once even if both labels apply), and T the reported walltime in hours. The provisional score is `100*(U/K)^2*(U/(U+2*B))/T`. It emphasizes coverage and penalizes known bad predictions. Known disconnected first-order discoveries are neutral; unknown predictions do not automatically count as incorrect. The detailed diagnostic report distinguishes each outcome.

The `exploration_confidence_heuristic` is NOT a statistical confidence interval or guarantee of reaction completeness: it is `clip(1-10/n_red,0,1)` where `n_red` is the mean redundant tail from reproducibly shuffled *eligible recorded DoubleMin observations*. It is a surrogate for the original full Search attempt history, and may be biased; a 200-observation tail gives 95% by convention. Blank means unsupported/undefined.

**Leaderboard caveat:** walltime is self-reported in this prototype, so comparisons are descriptive. Case selection can also cherry-pick high-coverage structures; use a frozen, published case list when comparing methods. The calculator/PES is unresolved and should not be mixed across submissions. The included `known_reaction_count` is known reference coverage, not proof no undiscovered reactions exist.

Registry watermark: '''+str(meta['source_publication_watermark'])+'''. This dataset does not include unpublished Search results.
'''
    (folder/'README.md').write_text(text,encoding='utf8')


def _prepare_diag_lookup(root,case):
    rows_by=defaultdict(list)
    with gzip.open(root/'diagnostic_saddles.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            item=json.loads(line)
            if item['structure_id']!=case['structure_id']:continue
            starts=item.get('starting_minimum_ids') or []
            # We refuse to transfer a diagnostic saddle between explicitly
            # incompatible starts. Empty provenance can be reported only as
            # unresolved (not penalized).
            item['start_known']=bool(starts)
            if starts and case['starting_minimum_id'] not in starts:continue
            rows_by[tuple(item['geometry']['numbers'])].append(item)
    return rows_by


def diagnose(matched, prediction, lookup):
    if matched['status']!='NO_MATCH':return {**matched,'diagnostic':'ELIGIBLE_REFERENCE_'+matched['status'],'penalty_key':None}
    candidates=[]; uncomparable=0
    for item in lookup.get(tuple(prediction['geometry']['numbers']),[]):
        try:d=core_drift_maxdev(prediction['geometry'],item['geometry'])
        except ValueError:uncomparable+=1;continue
        if d<=MATCH_A+1e-12:candidates.append(item)
    if not candidates:
        return {**matched,'diagnostic':'UNKNOWN_UNMATCHED', 'uncomparable_sources':uncomparable,'penalty_key':None}
    groups={}
    for candidate in candidates:
        gid=candidate.get('saddle_group_id')
        if gid is not None:
            size=candidate.get('registered_saddle_group_size')
            if gid in groups and groups[gid]!=size:
                raise ValueError('CONFLICTING_REGISTERED_SADDLE_GROUP_SIZE')
            groups[gid]=size
    matched={**matched,'matched_saddle_groups':[
        {'saddle_group_id':gid,'registered_saddle_count':groups[gid]}
        for gid in sorted(groups)]}
    # Only assign a penalty if all equally plausible identities have a known-bad
    # classification and the match is unambiguous at identity/group level.
    keys={str(c['saddle_group_id'] or c['saddle_id']) for c in candidates}
    kinds={c['diagnostic_kind'] for c in candidates}
    if len(keys)>1:
        outcome='AMBIGUOUS_CATALOG_MATCH';penalty_key=None
    elif any(not c['start_known'] for c in candidates):
        outcome='UNKNOWN_START_PROVENANCE';penalty_key=None
    elif kinds.issubset({'KNOWN_NON_FIRST_ORDER','KNOWN_CONNECTED_HIGH_BARRIER'}):
        outcome='KNOWN_BAD_'+('NON_FIRST_ORDER' if 'KNOWN_NON_FIRST_ORDER' in kinds else 'CONNECTED_HIGH_BARRIER')
        penalty_key=next(iter(keys))
    elif kinds=={'KNOWN_FIRST_ORDER_DISCONNECTED'}:
        outcome='KNOWN_FIRST_ORDER_DISCONNECTED_NEUTRAL';penalty_key=None
    else:
        outcome='KNOWN_OTHER_OR_UNQUALIFIED_NEUTRAL';penalty_key=None
    return {**matched,'diagnostic':outcome,'matched_saddle_ids':sorted(c['saddle_id'] for c in candidates),
            'known_orders':sorted({str(c['hessian_status']) for c in candidates}),
            'barriers_eV':sorted({v for c in candidates for v in c.get('barrier_eV',[])}),
            'penalty_key':penalty_key}


def grade_case(public,private,case_id,submission,seconds):
    pub=Path(public);priv=Path(private)
    with (pub/'cases.csv').open(newline='',encoding='utf8') as f:
        cases={r['case_id']:r for r in csv.DictReader(f)}
    if case_id not in cases:raise ValueError('UNKNOWN_CASE_ID')
    case=cases[case_id]
    ref=priv/'reference'
    if json.loads((priv/'private_manifest.json').read_text())['source_registry_sha256']!=case['registry_sha256']:
        raise ValueError('PUBLIC_PRIVATE_SNAPSHOT_MISMATCH')
    # User-provided scope arguments override missing .traj tags, but never
    # silently contradict explicit tags in the trajectory.
    raw=rm.score(ref,submission,seconds,start=case['starting_minimum_id'],
                 parent=case['structure_id'],pes=case['pes_id'])
    preds=rm.read_submissions(submission,start=case['starting_minimum_id'],parent=case['structure_id'],pes=case['pes_id'])
    lookup=_prepare_diag_lookup(priv,case)
    results=[];bad=set()
    for matched,pred in zip(raw['submissions'],preds):
        d=diagnose(matched,pred,lookup)
        if d.get('penalty_key'):bad.add(d['penalty_key'])
        results.append(d)
    k=int(case['known_reaction_count']);u=int(raw['eligible_unique_count'])
    if u>k:raise ValueError('CREDIT_EXCEEDS_CASE_K')
    return {'schema_version':'saddlebench-prototype-graded-v1','case_id':case_id,
        'structure_id':case['structure_id'],'starting_minimum_id':case['starting_minimum_id'],
        'known_eligible_reactions_K':k,'unique_reactions_U':u,'known_bad_unique_B':len(bad),
        'coverage':u/k if k else None,
        'penalty_precision':u/(u+PENALTY_WEIGHT*len(bad)) if u else 0.,
        'self_reported_elapsed_seconds':seconds,'provisional_score_per_hour':score_metric(u,k,len(bad),seconds),
        'exploration_confidence_heuristic':case['exploration_confidence_heuristic'] or None,
        'scorable':bool(k),'entries':results,
        'warning':'Prototype registry-derived scoring; PES is unverified and walltime is self-reported.'}



def grade_batch(public,private,submission_csv):
    source=Path(submission_csv)
    results=[];seen=set()
    with source.open(newline='',encoding='utf8') as f:
        reader=csv.DictReader(f)
        if not {'case_id','trajectory','elapsed_seconds'}.issubset(reader.fieldnames or []):
            raise ValueError('SUBMISSION_CSV_COLUMNS_REQUIRED')
        for row in reader:
            cid=str(row['case_id'])
            if cid in seen:raise ValueError('DUPLICATE_SUBMITTED_CASE:'+cid)
            seen.add(cid)
            traj=Path(row['trajectory'])
            if not traj.is_absolute():traj=source.parent/traj
            if traj.is_symlink() or not traj.is_file():raise ValueError('UNSAFE_OR_MISSING_TRAJECTORY:'+cid)
            sec=float(row['elapsed_seconds'])
            results.append(grade_case(public,private,cid,traj,sec))
    if not results:raise ValueError('EMPTY_SUBMISSION_CSV')
    k=sum(x['known_eligible_reactions_K'] for x in results)
    u=sum(x['unique_reactions_U'] for x in results)
    b=sum(x['known_bad_unique_B'] for x in results)
    t=sum(x['self_reported_elapsed_seconds'] for x in results)
    scored=[x['coverage'] for x in results if x['coverage'] is not None]
    with (Path(public)/'cases.csv').open(newline='',encoding='utf8') as f:
        full=sum(1 for _ in csv.DictReader(f))
    return {'schema_version':'saddlebench-prototype-batch-v1',
            'selected_cases':len(results),'available_cases':full,
            'aggregate':{'reference_known_K':k,'unique_credited_U':u,'known_bad_unique_B':b,
                 'total_self_reported_seconds':t,'micro_coverage':u/k if k else None,
                 'macro_coverage_known_cases':sum(scored)/len(scored) if scored else None,
                 'provisional_score_per_hour':score_metric(u,k,b,t)},
            'case_results':results,
            'comparability_warning':'Aggregate applies ONLY to explicitly submitted cases; cherry-picked subsets, self-reported times and unverified PES are not controlled leaderboard comparisons.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sp=p.add_subparsers(dest='cmd',required=True)
    a=sp.add_parser('stage');a.add_argument('--evidence',required=True);a.add_argument('--reference',required=True)
    a.add_argument('--inputs',required=True);a.add_argument('--out',required=True)
    b=sp.add_parser('grade');b.add_argument('--public',required=True);b.add_argument('--private',required=True)
    b.add_argument('--case-id',required=True);b.add_argument('--submission',required=True)
    b.add_argument('--elapsed-seconds',required=True,type=float);b.add_argument('--out',required=True)
    c=sp.add_parser('grade-batch');c.add_argument('--public',required=True);c.add_argument('--private',required=True)
    c.add_argument('--submission-csv',required=True);c.add_argument('--out',required=True)
    args=p.parse_args(argv)
    if args.cmd=='stage':result=stage(args.evidence,args.reference,args.inputs,args.out)
    else:
        result=(grade_batch(args.public,args.private,args.submission_csv) if args.cmd=='grade-batch' else
                grade_case(args.public,args.private,args.case_id,args.submission,args.elapsed_seconds))
        out=Path(args.out)
        if out.exists():raise FileExistsError(out)
        out.write_text(canonical(result)+'\n',encoding='utf8')
    print(canonical(result))

if __name__=='__main__':main()
