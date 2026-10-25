#!/usr/bin/env python3
"""Build immutable *candidate* score catalog from derived full-registry projection.

No missing force/Hessian evidence is manufactured. Registry-reported pathway
is available only via an explicit exploratory scorer flag. This export does
NOT assert that 71,647 reactions satisfy the old fmax/certification protocol.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,os,shutil,sqlite3,tempfile
from pathlib import Path
from saddlebench.reference.catalog_v2 import digest,SCHEMA

EXPECTED={'canonical_minima':125,'reaction_observations':202884,'reaction_registry':71647,'saddle_registry':217716}
TABLES={'canonical_minima':'minima','reaction_observations':'triplets','reaction_registry':'reactions','saddle_registry':'saddles'}
def rows(root,kind):
    for rel in sorted((root/TABLES[kind]).glob('part-*.jsonl')):
        if rel.is_symlink():raise ValueError('UNTRUSTED_SYMLINK')
        with rel.open(encoding='utf8') as f:
            for line in f:
                if line.strip():yield json.loads(line)
def geom(g):
    if not isinstance(g,dict) or not g.get('positions_angstrom') or not g.get('cell_angstrom') or not g.get('pbc'):return None
    n=g.get('ordered_numbers',g.get('numbers'))
    if not isinstance(n,list) or len(n)!=len(g['positions_angstrom']):return None
    return {'numbers':n,'positions_angstrom':g['positions_angstrom'],'cell_angstrom':g['cell_angstrom'],
            'pbc':g['pbc'],'constraints_known':False,'fixed_indices':[]}
def build(projection,lean,destination,pes_id):
    projection=Path(projection).absolute();lean=Path(lean).absolute();dest=Path(destination).absolute()
    if not pes_id or pes_id=='UNVERIFIED_OPERATOR_PES':raise ValueError('EXPLICIT_PES_SCOPE_REQUIRED')
    if dest.exists() or dest.is_symlink() or lean in dest.parents or projection in dest.parents:raise ValueError('UNSAFE_OUTPUT')
    pm=json.loads((projection/'projection_manifest.json').read_text());lm_bytes=(lean/'manifest.json').read_bytes()
    if pm.get('schema')!='saddlebench-associated-start-projection-v1' or pm.get('source_manifest_sha256')!=hashlib.sha256(lm_bytes).hexdigest():
        raise ValueError('PROJECTION_WRONG_SOURCE')
    if (projection/'PROJECTION_COMPLETE').read_text().strip()!='PASS' or (lean/'EXPORT_COMPLETE').read_text().strip()!='PASS':
        raise ValueError('SOURCE_INCOMPLETE')
    if any(pm['counts'].get(k)!=v or pm['counts'].get(k+'_annotated')!=v for k,v in EXPECTED.items()):
        raise ValueError('SOURCE_COUNTS_UNEXPECTED')
    if set(pm['shard_sha256'])!={str(x.relative_to(projection)) for kind in TABLES for x in (projection/TABLES[kind]).glob('part-*.jsonl')}:
        raise ValueError('PROJECTED_SHARD_SET_MISMATCH')
    for relative,sha in pm['shard_sha256'].items():
        if digest(projection/relative)!=sha:raise ValueError('PROJECTION_HASH_MISMATCH:'+relative)
    dest.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(dir=dest.parent,prefix='.sb_candidate_catalog_'))
    try:
        db=sqlite3.connect(stage/'_joins.sqlite')
        db.execute('CREATE TABLE saddles(id TEXT PRIMARY KEY,structure TEXT,grp TEXT,datum TEXT)')
        db.execute('CREATE TABLE observations(rx TEXT,id TEXT,saddle TEXT,grp TEXT,structure TEXT,minus TEXT,plus TEXT,outcome TEXT)')
        db.execute('CREATE INDEX obsrx ON observations(rx)')
        for s in rows(projection,'saddle_registry'):
            db.execute('INSERT INTO saddles VALUES(?,?,?,?)',
                (s['canonical_saddle_id'],s.get('structure_id'),s.get('saddle_group_id'),json.dumps(s,separators=(',',':'))))
        unassigned_observation_count=0
        for o in rows(projection,'reaction_observations'):
            if not o.get('canonical_reaction_id'):
                unassigned_observation_count+=1
            if o.get('canonical_reaction_id'):
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)',
                    (o['canonical_reaction_id'],o['observation_id'],o.get('canonical_saddle_id'),
                     o.get('saddle_group_id'),o.get('structure_id'),(o.get('minus') or {}).get('status'),
                     (o.get('plus') or {}).get('status'),o.get('outcome')))
        db.commit()
        minima={};by_structure={}
        for m in rows(projection,'canonical_minima'):
            sid=m.get('parent_structure_id');mid=m.get('minimum_id')
            if not sid or not mid or sid in by_structure:raise ValueError('AMBIGUOUS_CASE_STRUCTURE')
            by_structure[sid]=mid;minima[mid]=m
        stats={'total':0,'catalog_with_members':0,'registry_reported_connected':0,'science_verified':0,
               'no_usable_member':0,'missing_barrier':0,'observations_unassigned':unassigned_observation_count,
               'member_links_without_saddle_group_metadata':0,'member_links_with_saddle_group_metadata_checked':0}
        with (stage/'reference.jsonl').open('w',encoding='utf8') as out:
            for r in rows(projection,'reaction_registry'):
                stats['total']+=1
                rid=r['canonical_reaction_id']; sid=r.get('structure_id');group=r.get('saddle_group_id')
                if sid not in by_structure:raise ValueError('REACTION_WITHOUT_STRUCTURE_MINIMUM')
                seen=set();members=[];observations=[];endpoints_reported=False
                for oid,ssid,ogroup,osid,minus,plus,outcome in db.execute(
                    'SELECT id,saddle,grp,structure,minus,plus,outcome FROM observations WHERE rx=?',(rid,)):
                    if osid and osid!=sid or ogroup!=group:raise ValueError('OBSERVATION_SCOPE_CONFLICT')
                    observations.append(oid)
                    if not (minus=='OK' and plus=='OK') or str(outcome).upper() in ('NULL_SAME_BASIN','AMBIGUOUS','UNCONNECTED','ERROR'):
                        continue
                    endpoints_reported=True
                    if not ssid or ssid in seen:continue
                    item=db.execute('SELECT structure,grp,datum FROM saddles WHERE id=?',(ssid,)).fetchone()
                    if item is None:continue
                    sstructure,sgrp,raw=item
                    # The pinned lean-export saddle schema has no group column.
                    # A DoubleMin observation explicitly links the saddle ID to its
                    # reaction and its group. Validate any *available* saddle-group
                    # membership evidence, but never interpret a missing saddle
                    # group as a scientific contradiction or invent membership.
                    if sstructure!=sid:
                        raise ValueError('SADDLE_STRUCTURE_CONFLICT:'+rid+':'+str(ssid))
                    sadd=json.loads(raw)
                    registered_groups=sadd.get('registered_saddle_group_ids')
                    if registered_groups is not None:
                        if not isinstance(registered_groups,list) or group not in registered_groups:
                            raise ValueError('SADDLE_GROUP_MEMBERSHIP_CONFLICT:'+rid+':'+str(ssid))
                    elif sgrp is not None and sgrp!=group:
                        raise ValueError('SADDLE_GROUP_CONFLICT:'+rid+':'+str(ssid))
                    if registered_groups is None and sgrp is None:
                        stats['member_links_without_saddle_group_metadata']+=1
                    else:
                        stats['member_links_with_saddle_group_metadata_checked']+=1
                    g=geom(sadd.get('geometry'))
                    if g is None or sadd.get('geometry_status')!='OK':continue
                    if str(sadd.get('hessian_status','')).upper() not in ('FIRST_ORDER','FIRST_ORDER_SADDLE','FO_CERTIFIED'):continue
                    seen.add(ssid)
                    members.append({'canonical_saddle_id':ssid,'saddle_group_id':group,'geometry':g})
                # Note: no force convergence evidence exists in this lean schema.
                reported=(bool(members) and endpoints_reported and r.get('connected_to_canonical_minimum') in (1,True))
                if reported:stats['registry_reported_connected']+=1
                if members:stats['catalog_with_members']+=1
                else:stats['no_usable_member']+=1
                if r.get('barrier_eV') is None:stats['missing_barrier']+=1
                item={'reaction_id':rid,'structure_id':sid,'pes_id':pes_id,
                      'starting_minimum_id':by_structure[sid],
                      'starting_minimum_id_basis':pm['basis'],
                      'barrier_eV':r.get('barrier_eV'),
                      'science_verified':False,
                      'scientific_evidence_status':'NOT_ASSESSED_NO_MEASURED_SADDLE_AND_ENDPOINT_FMAX_IN_LEAN_EXPORT',
                      'registry_recorded_connected':reported,
                      'historical_registry_rate_relevant_flag':r.get('rate_relevant_registry_flag'),
                      'members':members,'confirming_observation_ids':sorted(observations)}
                out.write(json.dumps(item,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
        if stats['total']!=EXPECTED['reaction_registry']:raise ValueError('REACTION_COUNT_MISMATCH')
        with (stage/'cases.csv').open('w',encoding='utf8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['case_id','structure_id','starting_minimum_id','pes_id']);w.writeheader()
            for sid,mid in sorted(by_structure.items()):
                w.writerow({'case_id':sid,'structure_id':sid,'starting_minimum_id':mid,'pes_id':pes_id})
        # Per-structure shards are a performance index; the full catalog is authoritative.
        (stage/'case_shards').mkdir()
        sid_paths={}
        handles={}
        sid_counts={}
        try:
            with (stage/'reference.jsonl').open('rt',encoding='utf8') as full:
                for line in full:
                    record=json.loads(line)
                    sid=record['structure_id']
                    if sid not in handles:
                        key=hashlib.sha256(sid.encode()).hexdigest()[:24]
                        sid_paths[sid]='case_shards/'+key+'.jsonl'
                        handles[sid]=(stage/sid_paths[sid]).open('x',encoding='utf8')
                        sid_counts[sid]=0
                    handles[sid].write(line);sid_counts[sid]+=1
        finally:
            for handle in handles.values():handle.close()
        shards={sid:{'path':sid_paths[sid],'sha256':digest(stage/sid_paths[sid]),'count':sid_counts[sid]}
                for sid in sid_paths}
        manifest={'schema_version':SCHEMA,'reference_sha256':digest(stage/'reference.jsonl'),
                  'reference_count':stats['total'],'cases_sha256':digest(stage/'cases.csv'),
                  'case_count':len(by_structure),'source_manifest_sha256':pm['source_manifest_sha256'],
                  'source_registry':pm.get('source_registry'),
                  'evidence_standard':'lean_registry_reported_not_force_verified',
                  'no_science_verified_rows_claimed':True,'stats':stats,
                  'policy':'Cutoff-neutral. Barrier <1.0 eV is an OPTIONAL SCORER POLICY, not canonical identity.',
                  'pes_id_operator_assigned':pes_id,'case_shards':shards}
        (stage/'catalog_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        db.close();(stage/'_joins.sqlite').unlink()
        (stage/'COMPLETE').write_text('PASS\n')
        os.rename(stage,dest)
        return manifest
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--projection',required=True)
    p.add_argument('--lean',required=True)
    p.add_argument('--destination',required=True)
    p.add_argument('--pes-id',required=True)
    a=p.parse_args();r=build(a.projection,a.lean,a.destination,a.pes_id)
    print('CUTOFF_NEUTRAL_CATALOG_BUILD=PASS')
    print('CATALOG_STATS='+json.dumps(r['stats'],sort_keys=True))
    print('SCIENCE_VERIFIED_REACTIONS=0 (lean evidence insufficient; not a scientific failure count)')
    print('CATALOG_DESTINATION='+str(Path(a.destination).absolute()))
if __name__=='__main__':main()
