"""Registry-derived reaction MVP (not independent scientific re-certification).

Both modes require: first-order saddle, saddle fmax<=0.005, both DoubleMin
endpoints with measured fmax<=0.0025 and converged status, a registered
connected, rate-relevant reaction, compatible starting minimum and geometry.
'all_saddles' permits historical/non-Powell DoubleMin; 'canonical_only' requires
the actual Powell protocol, not merely protocol_eligible=1.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path

from saddlebench.reference.snapshot import canonical, validate_geometry
from saddlebench.reference.safe_output import AtomicDirectory
from saddlebench.matching.geometry import core_drift_maxdev, cannot_match_by_anchor
from saddlebench.io.extxyz import read_extxyz

SADDLE_FMAX = 0.005
ENDPOINT_FMAX = 0.0025
BARRIER_EV = 1.5
MODES = ('all_saddles', 'canonical_only')
POWELL = 'standalone_hessian_lbfgs_powell_eta0p2_fmax0p0025_disp0p25_v2'

def rows(directory, file):
    with (Path(directory)/file).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))

def finite_range(x, maximum, *, inclusive=True):
    return type(x) in (int,float) and math.isfinite(x) and 0<=x and (x<=maximum if inclusive else x<maximum)

def endpoint_ok(obs, side):
    e=obs.get(side+'_endpoint_evidence') or {}
    f=e.get('stored_real_atomic_fmax_eV_A')
    status=str(e.get('terminal_status') or '').strip().lower()
    return (finite_range(f,ENDPOINT_FMAX) and status in ('', 'converged','success','completed_converged'))

def doublemin_ok(obs, mode):
    if not (obs.get('minus_geometry_id') and obs.get('plus_geometry_id')):
        return False,'MISSING_ENDPOINT_GEOMETRY'
    if mode=='canonical_only' and (obs.get('protocol')!=POWELL or obs.get('protocol_eligible_registry_flag') not in (1,True)):
        return False,'NOT_POWELL_CANONICAL'
    if not endpoint_ok(obs,'minus') or not endpoint_ok(obs,'plus'):
        return False,'ENDPOINT_CONVERGENCE_NOT_ESTABLISHED'
    ep=(obs.get('source_columns') or {}).get('reaction_endpoint_state') or {}
    if (ep.get('connected_to_canonical_minimum') is not None and ep['connected_to_canonical_minimum'] not in (1,True)) or (ep.get('rate_relevant') is not None and ep['rate_relevant'] not in (1,True)):
        return False,'OBSERVATION_ENDPOINT_STATE_INCONSISTENT'
    if not obs.get('canonical_reaction_id') or str(obs.get('outcome') or '').upper() in ('NULL_SAME_BASIN','AMBIGUOUS','UNCONNECTED','ERROR'):
        return False,'UNUSABLE_REACTION_OBSERVATION'
    return True,None

def saddle_ok(s):
    label=str(s.get('hessian_status') or '').upper()
    if label not in ('FIRST_ORDER','FIRST_ORDER_SADDLE','FO_CERTIFIED'):
        return False,'NOT_FIRST_ORDER'
    if not finite_range((s.get('source_columns') or {}).get('saddle_fmax_eV_A'),SADDLE_FMAX):
        return False,'SADDLE_FMAX_UNVERIFIED_OR_ABOVE_CUTOFF'
    return True,None

def reaction_ok(rx):
    barrier=rx.get('barrier_eV')
    if rx.get('connected_to_canonical_minimum') not in (1,True):return False,'NOT_CONNECTED'
    if rx.get('rate_relevant_registry_flag') not in (1,True):return False,'NOT_RATE_RELEVANT'
    if not finite_range(barrier,BARRIER_EV,inclusive=False):return False,'BARRIER_UNVERIFIED_OR_HIGH'
    return True,None

def _sha_file(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''): h.update(data)
    return h.hexdigest()

def build(directory,destination,mode='all_saddles',pes_id='UNVERIFIED_OPERATOR_PES',max_reactions=None):
    """Build a read-only, registry-derived prototype index, never producer state."""
    if mode not in MODES:raise ValueError('INVALID_MODE')
    source=Path(directory); out=Path(destination)
    if out.exists():raise FileExistsError('REF_OUTPUT_EXISTS')
    manifest=json.loads((source/'manifest.json').read_text())
    sha_map=manifest.get('file_sha256',{})
    for name,digest in sha_map.items():
        target=source/name
        if target.is_symlink() or not target.is_file() or _sha_file(target)!=digest:
            raise ValueError('SOURCE_DATASET_CHECKSUM_FAILED:'+name)
    registry=manifest['registry']; regsha=registry['sha256']
    stats=Counter(); minimum_by_parent=defaultdict(list)
    for m in rows(source,'minima.jsonl'):
        minimum_by_parent[m['parent_structure_id']].append(m)
    saddles={s['canonical_saddle_id']:s for s in rows(source,'saddles.jsonl')}
    # Full registered saddle-group population; not the eligible reaction-member count.
    group_ids=defaultdict(set)
    for saddle in saddles.values():
        if saddle.get('saddle_group_id') is not None:
            group_ids[(saddle['structure_id'],saddle['saddle_group_id'])].add(saddle['canonical_saddle_id'])
    group_size={k:len(v) for k,v in group_ids.items()}
    reaction={r['canonical_reaction_id']:r for r in rows(source,'reactions.jsonl')}
    candidates=defaultdict(list); required_geoms=set()
    for obs in rows(source,'doublemin_observations.jsonl'):
        stats['observations_seen']+=1
        rx=reaction.get(obs.get('canonical_reaction_id'))
        if rx is None:stats['observation_no_registry_reaction']+=1;continue
        rok,why=reaction_ok(rx)
        if not rok:stats[why]+=1;continue
        ok,why=doublemin_ok(obs,mode)
        if not ok:stats[why]+=1;continue
        s=saddles.get(obs.get('canonical_saddle_id'))
        if not s or s['structure_id']!=rx['structure_id'] or s['saddle_group_id']!=rx['saddle_group_id'] or obs['saddle_group_id']!=rx['saddle_group_id']:
            stats['SOURCE_IDENTITY_INCONSISTENT']+=1;continue
        if mode=='canonical_only' and rx.get('status')!='CANONICAL_PROTOCOL_OBSERVED':
            stats['REACTION_NOT_CANONICALLY_OBSERVED']+=1;continue
        sok,why=saddle_ok(s)
        if not sok:stats[why]+=1;continue
        gid=s.get('geometry_id')
        if not gid:stats['NO_SADDLE_GEOMETRY']+=1;continue
        # A unique authoritative starting minimum is required per case.
        starts=s.get('observed_starting_minimum_ids') or []
        if len(set(starts))==1:
            minimum=next((m for m in minimum_by_parent[rx['structure_id']] if m['minimum_id']==starts[0]),None)
        else:
            options=minimum_by_parent[rx['structure_id']]
            active=[m for m in options if m.get('active_in_published_minima_snapshot') is True]
            minimum=active[0] if len(active)==1 else (options[0] if len(options)==1 else None)
        if minimum is None:
            stats['STARTING_MINIMUM_UNRESOLVED']+=1;continue
        if not (obs.get('minus_endpoint_evidence') or {}).get('terminal_status') or not (obs.get('plus_endpoint_evidence') or {}).get('terminal_status'):
            stats['accepted_force_converged_but_optimizer_status_unknown']+=1
        candidates[rx['canonical_reaction_id']].append((s,obs,rx,minimum))
        required_geoms.add(gid)
    # Minimum geometries are part of the researcher-facing inputs.
    for items in candidates.values():
        for _,_,_,minimum in items:
            if minimum.get('geometry_id'):required_geoms.add(minimum['geometry_id'])
    geoms={}
    for g in rows(source,'geometries.jsonl'):
        if g['geometry_id'] in required_geoms:geoms[g['geometry_id']]=g
    ref=[]
    for rid,items in sorted(candidates.items()):
        if max_reactions is not None and len(ref)>=max_reactions:break
        # Each identity has an exact case scope; conflicts are excluded.
        starts={m['minimum_id'] for _,_,_,m in items}
        if len(starts)!=1:stats['MULTIPLE_STARTS_PER_REACTION']+=1;continue
        seen=set(); members=[]
        for s,obs,rx,m in items:
            g=geoms.get(s['geometry_id'])
            if not g:stats['MISSING_GEOMETRY']+=1;continue
            geo={'numbers':g['ordered_numbers'],'positions_angstrom':g['positions_angstrom'],
                 'cell_angstrom':g['cell_angstrom'],'pbc':g['pbc'],
                 'constraints_known':g['constraints_known'] is True,
                 'fixed_indices':g['fixed_indices'] if g['constraints_known'] is True else []}
            validate_geometry(geo)
            key=s['canonical_saddle_id']
            if key in seen:continue
            seen.add(key)
            members.append({'canonical_saddle_id':key,'saddle_group_id':s['saddle_group_id'],
                            'registered_saddle_group_size':group_size[(s['structure_id'],s['saddle_group_id'])],
                            'geometry':geo,'constraints_metadata_verified':g['constraints_known'] is True,
                            'saddle_fmax_eV_A':s['source_columns']['saddle_fmax_eV_A']})
        if not members:continue
        # Keep a reaction-level identity and all supporting saddle geometries, not
        # one artificially chosen medoid. Different reactions in one saddle group
        # remain different identities, hence ambiguous for saddle-only predictions.
        ref.append({'reaction_id':rid,'structure_id':items[0][2]['structure_id'],
                    'starting_minimum_id':next(iter(starts)),'pes_id':pes_id,
                    'barrier_eV':items[0][2]['barrier_eV'],'members':members,
                    'confirming_observation_ids':sorted({o['observation_id'] for _,o,_,_ in items}),
                    'supporting_protocols':sorted({o['protocol'] for _,o,_,_ in items})})
    # Publish exact starting minimum geometries separately from scored reactions.
    required_minimum_ids={r['starting_minimum_id'] for r in ref}
    minimum_records=[]
    for mins in minimum_by_parent.values():
        for m in mins:
            if m['minimum_id'] not in required_minimum_ids:continue
            g=geoms.get(m.get('geometry_id'))
            if g is None:raise ValueError('ELIGIBLE_MINIMUM_GEOMETRY_MISSING:'+m['minimum_id'])
            minimum_records.append({'minimum_id':m['minimum_id'],'parent_structure_id':m['parent_structure_id'],
                'pes_id':pes_id,'energy_eV':m.get('energy_eV'),
                'geometry':{'numbers':g['ordered_numbers'],'positions_angstrom':g['positions_angstrom'],
                            'cell_angstrom':g['cell_angstrom'],'pbc':g['pbc'],
                            'constraints_known':g['constraints_known'],
                            'fixed_indices':g['fixed_indices']}})
    minimum_records.sort(key=lambda m:m['minimum_id'])
    min_raw=(''.join(canonical(r)+'\n' for r in minimum_records)).encode('utf-8')
    stats['eligible_starting_minima']=len(minimum_records)
    stats['eligible_reaction_identities']=len(ref)
    stats['qualified_saddle_geometries']=sum(len(r['members']) for r in ref)
    stats['pes_scope_operator_assigned_not_independently_verified']=1
    raw=(''.join(canonical(r)+'\n' for r in ref)).encode('utf-8')
    manifest_out=canonical({'schema_version':'saddlebench-reaction-prototype-v1',
        'mode':mode,'registry_sha256':regsha,'registry_watermark':registry.get('published_through'),
        'reference_sha256':hashlib.sha256(raw).hexdigest(),'reference_count':len(ref),
        'starting_minima_sha256':hashlib.sha256(min_raw).hexdigest(),
        'starting_minima_count':len(minimum_records),
        'scientific_rules':{'saddle_fmax_max_eV_A':SADDLE_FMAX,'doublemin_endpoint_fmax_max_eV_A':ENDPOINT_FMAX,
                             'connected_required':True,'barrier_strictly_below_eV':BARRIER_EV,
                             'match_maxdev_A':0.2},
        'limitations':['Registry-derived trust; native science not independently recertified.',
             'Only observations with measured endpoint forces qualify; absent optimizer status is recorded separately.',
             'Source PES identity not independently established; operator-defined scope.',
             'Unknown source constraints are retained as provenance, not used for geometric matching.',
             'Only recorded registry reactions, not raw unpublished Search evidence.'],
        'counts':dict(stats)})+'\n'
    with AtomicDirectory(out,protected_roots=(source,),create_parents=True) as writer:
        writer.write_bytes('reference.jsonl',raw)
        writer.write_bytes('starting_minima.jsonl',min_raw)
        writer.write_bytes('manifest.json',manifest_out.encode('utf-8'))
        writer.commit()
    return dict(stats)

def _compare(a,b):
    # Compare geometry only. Constraint provenance is retained but not a matching gate.
    return core_drift_maxdev(a,b)

def load_reference(reference):
    ref=Path(reference); manifest=json.loads((ref/'manifest.json').read_text())
    if manifest.get('schema_version')!='saddlebench-reaction-prototype-v1':raise ValueError('WRONG_REFERENCE_FORMAT')
    if _sha_file(ref/'reference.jsonl')!=manifest['reference_sha256']:raise ValueError('TAMPERED_REFERENCE')
    return manifest,list(rows(ref,'reference.jsonl'))

def read_submissions(path,start=None,pes=None,parent=None):
    path=Path(path)
    if path.suffix.lower()=='.traj':
        try:
            from ase.io import read
            from ase.constraints import FixAtoms
        except ImportError as e:raise RuntimeError('ASE_REQUIRED_FOR_TRAJ') from e
        atomslist=read(str(path),index=':')
        if not isinstance(atomslist,list):atomslist=[atomslist]
        submissions=[]
        for i,a in enumerate(atomslist):
            fix=[];known=True
            for c in a.constraints:
                if isinstance(c,FixAtoms):fix.extend(int(j) for j in c.get_indices())
                else:known=False
            g={'numbers':[int(x) for x in a.numbers],
               'positions_angstrom':a.positions.tolist(),
               'cell_angstrom':a.cell.array.tolist(),'pbc':[bool(x) for x in a.pbc],
               'fixed_indices':sorted(set(fix)) if known else [],'constraints_known':known}
            validate_geometry(g)
            submissions.append({'submission_id':str(a.info.get('submission_id',f'frame-{i+1}')),
                 'starting_minimum_id':a.info.get('starting_minimum_id'),
                 'parent_structure_id':a.info.get('parent_structure_id'),
                 'pes_id':a.info.get('pes_id'),'geometry':g})
    else:submissions=read_extxyz(path)
    for row in submissions:
        for key,value in [('starting_minimum_id',start),('parent_structure_id',parent),('pes_id',pes)]:
            if value is not None:
                if row.get(key) not in (None,value):raise ValueError('CONFLICTING_SUBMISSION_SCOPE:'+key)
                row[key]=value
        if not all(row.get(k) for k in ('starting_minimum_id','parent_structure_id','pes_id')):
            raise ValueError('MISSING_SUBMISSION_SCOPE')
    return submissions

def score(reference,submission,elapsed_seconds,start=None,pes=None,parent=None,progress=None,prepared_reference=None):
    if not isinstance(elapsed_seconds,(int,float)) or not math.isfinite(elapsed_seconds) or elapsed_seconds<=0:
        raise ValueError('INVALID_WALLTIME')
    if progress:progress('reference_load',0,1)
    manifest,reactions=(load_reference(reference) if prepared_reference is None else prepared_reference)
    if progress:progress('reference_load',1,1)
    if progress:progress('submission_load',0,1)
    submissions=read_submissions(submission,start,pes,parent)
    if progress:progress('submission_load',1,1)
    by_scope=defaultdict(list)
    for r in reactions:
        for m in r['members']:
            by_scope[(r['structure_id'],r['starting_minimum_id'],r['pes_id'],tuple(m['geometry']['numbers']))].append((r,m))
    credited=set(); results=[];scope_counts=defaultdict(Counter)
    if progress:progress('eligible',0,len(submissions))
    for progress_i,sub in enumerate(submissions,1):
        scope=(sub['parent_structure_id'],sub['starting_minimum_id'],sub['pes_id'])
        ids=set(); errors=set(); matched_groups={}
        for r,m in by_scope.get((*scope,tuple(sub['geometry']['numbers'])),[]):
            if cannot_match_by_anchor(sub['geometry'],m['geometry'],0.2):continue
            try:d=_compare(sub['geometry'],m['geometry'])
            except ValueError as e:errors.add(str(e));continue
            if d<=0.2+1e-12:
                ids.add(r['reaction_id'])
                gid=m.get('saddle_group_id')
                if gid is not None:
                    size=m.get('registered_saddle_group_size')
                    if gid in matched_groups and matched_groups[gid]!=size:
                        raise ValueError('CONFLICTING_REGISTERED_SADDLE_GROUP_SIZE')
                    matched_groups[gid]=size
        if len(ids)>1: status='AMBIGUOUS'; target=None
        elif not ids:status='NO_MATCH';target=None
        else:
            target=next(iter(ids)); key=(*scope,target)
            if key in credited:status='DUPLICATE'
            else:status='CREDIT';credited.add(key)
        scope_counts[scope]['submitted']+=1
        scope_counts[scope][status.lower()]+=1
        results.append({'submission_id':sub['submission_id'],'parent_structure_id':scope[0],
                        'starting_minimum_id':scope[1],'pes_id':scope[2],
                        'status':status,'matched_reaction_id':target,
                        'matching_reaction_ids':sorted(ids),'incompatibilities':sorted(errors),
                        'matched_saddle_groups':[{'saddle_group_id':gid,
                            'registered_saddle_count':matched_groups[gid]}
                            for gid in sorted(matched_groups)]})
        if progress and (progress_i%10==0 or progress_i==len(submissions)):
            progress('eligible',progress_i,len(submissions))
    return {'schema_version':'saddlebench-reaction-prototype-score-v1','mode':manifest['mode'],
            'registry_sha256':manifest['registry_sha256'],'reference_sha256':manifest['reference_sha256'],
            'elapsed_seconds':elapsed_seconds,'eligible_unique_count':len(credited),
            'score_per_wall_hour':len(credited)*3600/elapsed_seconds,
            'submissions':results,'reference_eligible_reactions':len(reactions),
            'per_case':[{'parent_structure_id':scope[0],'starting_minimum_id':scope[1],
                'pes_id':scope[2],**dict(counter)} for scope,counter in sorted(scope_counts.items())],
            'scope_note':'Only exact (structure,start_minimum,PES) scope; ambiguous distinct reaction IDs get no credit.'}


def _atoms_from_geometry(geometry, extra_info):
    from ase import Atoms
    from ase.constraints import FixAtoms
    g=geometry
    atoms=Atoms(numbers=g['numbers'],positions=g['positions_angstrom'],
                cell=g['cell_angstrom'],pbc=g['pbc'])
    if g.get('constraints_known') is True and g.get('fixed_indices'):
        atoms.set_constraint(FixAtoms(indices=g['fixed_indices']))
    atoms.info.update(extra_info)
    if g.get('constraints_known') is not True:
        atoms.info['saddlebench_constraints_unknown']=True
    return atoms

def make_inputs(reference,output):
    """Write case starting minima as portable native ASE .traj frames."""
    from ase.io import write
    ref=Path(reference)
    manifest=json.loads((ref/'manifest.json').read_text())
    raw=(ref/'starting_minima.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['starting_minima_sha256']:
        raise ValueError('START_MINIMA_CHECKSUM_MISMATCH')
    atoms=[]
    for m in rows(ref,'starting_minima.jsonl'):
        g=m['geometry']
        if g.get('constraints_known') is not True:
            # Unknown fixed constraints are not safe executable starting inputs.
            continue
        atoms.append(_atoms_from_geometry(g,{'starting_minimum_id':m['minimum_id'],
             'parent_structure_id':m['parent_structure_id'],'pes_id':m['pes_id']}))
    if not atoms:raise ValueError('NO_START_MINIMA_WITH_KNOWN_CONSTRAINTS')
    target=Path(output)
    if target.exists():raise FileExistsError('INPUT_TRAJ_EXISTS')
    write(str(target),atoms,format='traj')
    return {'starting_minima_frames':len(atoms),'output':str(target),
            'omitted_unknown_constraints':manifest['starting_minima_count']-len(atoms)}

def smoke(reference,output):
    """Construct a real-file self-match from one frozen reference, then score it."""
    from ase.io import write
    manifest,rs=load_reference(reference)
    selected=None
    for r in rs:
        for m in r['members']:
            if m.get('constraints_metadata_verified'):
                selected=(r,m);break
        if selected:break
    if selected is None:raise ValueError('NO_SCORABLE_KNOWN_CONSTRAINT_GEOMETRY')
    r,m=selected
    a=_atoms_from_geometry(m['geometry'],{
        'submission_id':'selftest-1','starting_minimum_id':r['starting_minimum_id'],
        'parent_structure_id':r['structure_id'],'pes_id':r['pes_id']})
    b=a.copy();b.info['submission_id']='selftest-duplicate'
    target=Path(output)
    if target.exists():raise FileExistsError('SMOKE_TRAJ_EXISTS')
    write(str(target),[a,b],format='traj')
    result=score(reference,target,3600)
    return {'prediction_traj':str(target),'score':result}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--evidence',required=True);b.add_argument('--out',required=True)
    b.add_argument('--mode',choices=MODES,default='all_saddles');b.add_argument('--pes-id',required=True)
    s=sub.add_parser('score');s.add_argument('--reference',required=True);s.add_argument('--submission',required=True)
    s.add_argument('--elapsed-seconds',type=float,required=True);s.add_argument('--out',required=True)
    s.add_argument('--start-minimum-id');s.add_argument('--parent-structure-id');s.add_argument('--pes-id')
    i=sub.add_parser('make-inputs');i.add_argument('--reference',required=True);i.add_argument('--out',required=True)
    t=sub.add_parser('smoke');t.add_argument('--reference',required=True);t.add_argument('--out',required=True)
    a=p.parse_args(argv)
    if a.command=='build': result=build(a.evidence,a.out,a.mode,a.pes_id)
    elif a.command=='make-inputs': result=make_inputs(a.reference,a.out)
    elif a.command=='smoke': result=smoke(a.reference,a.out)
    else:
        result=score(a.reference,a.submission,a.elapsed_seconds,a.start_minimum_id,a.pes_id,a.parent_structure_id)
        dest=Path(a.out)
        if dest.exists():raise FileExistsError('SCORE_OUTPUT_EXISTS')
        dest.write_text(canonical(result)+'\n',encoding='utf-8')
    print(canonical(result))
    return 0

if __name__=='__main__':raise SystemExit(main())
