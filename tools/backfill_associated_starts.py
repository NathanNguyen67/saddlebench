#!/usr/bin/env python3
"""Produce a new immutable lean-export projection with derived structure associations.

NEVER claims that an association proves actual search-origin provenance.
No edits to the registry, original export, published Hugging Face records, or
observed_starting_minimum_ids. Fail closed on missing/ambiguous/contradictory IDs.
"""
from __future__ import annotations
import argparse,collections,hashlib,json,os,shutil,tempfile
from pathlib import Path

TABLES=('canonical_minima','saddle_registry','reaction_registry','reaction_observations')
TABLE_DIR={'canonical_minima':'minima','saddle_registry':'saddles',
           'reaction_registry':'reactions','reaction_observations':'triplets'}
BASIS='DERIVED_UNIQUE_REGISTERED_MINIMUM_FOR_STRUCTURE_NOT_OBSERVED_SEARCH_ORIGIN'

def fingerprint(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for part in iter(lambda:f.read(1<<20),b''):h.update(part)
    return h.hexdigest()

def safe_parts(root,manifest):
    by={t:[] for t in TABLES}
    seen=set()
    for row in manifest['parts']:
        t=row.get('table')
        if t not in by:continue
        name=row.get('part')
        rel=Path(name) if isinstance(name,str) else Path('')
        if rel.is_absolute() or '..' in rel.parts or len(rel.parts)!=2 or rel.parts[0]!=TABLE_DIR[t] or rel.suffix!='.jsonl':
            raise ValueError('UNSAFE_OR_MISMATCHED_PART:'+str(name))
        src=root/rel
        if name in seen or not src.is_file() or src.is_symlink():raise ValueError('DUPLICATE_OR_MISSING_PART:'+name)
        seen.add(name)
        n=row.get('rows')
        if type(n)!=int or n<0:raise ValueError('INVALID_PART_ROW_COUNT')
        by[t].append((rel,n))
    if any(not by[t] for t in TABLES):raise ValueError('MISSING_TABLE_SHARDS')
    return by

def stream(root,parts,table):
    for rel,expected in parts[table]:
        actual=0
        with (root/rel).open('rt',encoding='utf8') as f:
            for line in f:
                actual+=1
                obj=json.loads(line)
                if not isinstance(obj,dict):raise ValueError('ROW_NOT_OBJECT')
                yield rel,obj
        if actual!=expected:raise ValueError('PART_ROW_COUNT_MISMATCH:'+str(rel))

def _id(x):
    return x if isinstance(x,str) and x else None

def build(input_root,output_dir):
    root=Path(input_root).absolute();dst=Path(output_dir).absolute()
    if root.is_symlink() or not root.is_dir() or not (root/'EXPORT_COMPLETE').is_file() or (root/'EXPORT_COMPLETE').read_text().strip()!='PASS':
        raise ValueError('INCOMPLETE_OR_UNTRUSTED_SOURCE_EXPORT')
    mbytes=(root/'manifest.json').read_bytes();m=json.loads(mbytes)
    if m.get('schema_version')!='saddlebench-lean-registry-v1':raise ValueError('SOURCE_SCHEMA_UNSUPPORTED')
    parts=safe_parts(root,m)
    if dst.exists() or dst.is_symlink() or dst==root or root in dst.parents:
        raise ValueError('OUTPUT_ALREADY_EXISTS_OR_INSIDE_SOURCE')
    minima=collections.defaultdict(set);count=collections.Counter()
    for _,r in stream(root,parts,'canonical_minima'):
        sid=_id(r.get('parent_structure_id'));mid=_id(r.get('minimum_id'))
        if not sid or not mid:raise ValueError('INVALID_MINIMUM_RECORD')
        minima[sid].add(mid)
    if any(len(x)!=1 for x in minima.values()):raise ValueError('AMBIGUOUS_MINIMUM_STRUCTURE_MAPPING')
    by_structure={s:next(iter(ids)) for s,ids in minima.items()}
    # Immutable lookup tables, not inferred search histories.
    reaction_structure={};saddle_structure={}
    for _,r in stream(root,parts,'reaction_registry'):
        rid=_id(r.get('canonical_reaction_id'));s=_id(r.get('structure_id'))
        if not rid or rid in reaction_structure or not s:raise ValueError('INVALID_OR_DUPLICATE_REACTION')
        reaction_structure[rid]=s
    for _,r in stream(root,parts,'saddle_registry'):
        sid=_id(r.get('canonical_saddle_id'));s=_id(r.get('structure_id'))
        if not sid or sid in saddle_structure or not s:raise ValueError('INVALID_OR_DUPLICATE_SADDLE')
        saddle_structure[sid]=s
    dst.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.saddlebench_start_backfill_',dir=dst.parent))
    try:
        for table in TABLES:
            for rel,_ in parts[table]:
                (stage/rel).parent.mkdir(parents=True,exist_ok=True)
                # Each input shard is processed once. Write the equivalent path
                # only in this new derived projection, never over original.
                with (stage/rel).open('x',encoding='utf8') as f:
                    for rrel,r in stream(root,{table:[(rel,dict(parts[table])[rel])]},table):
                        count[table]+=1
                        if table=='canonical_minima':
                            s=_id(r.get('parent_structure_id'))
                        else:
                            s=_id(r.get('structure_id')) or _id(r.get('parent_structure_id'))
                            # Cross-check all available links, even when a row also
                            # supplies a direct structure ID. No topology inference.
                            if table=='reaction_observations':
                                rs=reaction_structure.get(_id(r.get('canonical_reaction_id')))
                                ss=saddle_structure.get(_id(r.get('canonical_saddle_id')))
                                seen={x for x in (s,rs,ss) if x}
                                if len(seen)>1:raise ValueError('OBSERVATION_STRUCTURE_LINK_CONFLICT')
                                s=next(iter(seen)) if seen else None
                        mid=by_structure.get(s) if s else None
                        if mid is not None:
                            if table=='saddle_registry':
                                observed=r.get('observed_starting_minimum_ids') or []
                                if not isinstance(observed,list) or any(_id(x)!=mid for x in observed):
                                    raise ValueError('OBSERVED_START_CONFLICT_WITH_UNIQUE_STRUCTURE_MINIMUM')
                            existing=_id(r.get('starting_minimum_id'))
                            if existing and existing!=mid:raise ValueError('EXISTING_STARTING_ID_CONFLICT:'+table)
                            assoc=_id(r.get('associated_starting_minimum_id'))
                            if assoc and assoc!=mid:raise ValueError('EXISTING_ASSOCIATED_ID_CONFLICT:'+table)
                            r['associated_starting_minimum_id']=mid
                            r['associated_starting_minimum_id_basis']=BASIS
                            count[table+'_annotated']+=1
                        else:
                            if table in ('reaction_registry','saddle_registry'):
                                raise ValueError('CANONICAL_STRUCTURE_MISSING_UNIQUE_MINIMUM:'+str(s))
                            count[table+'_unresolved']+=1
                        f.write(json.dumps(r,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
                    f.flush();os.fsync(f.fileno())
        expected=m.get('source_counts',{})
        for table in TABLES:
            if count[table]!=expected.get(table):raise ValueError('SOURCE_ROW_COUNT_MISMATCH:'+table)
        meta={'schema':'saddlebench-associated-start-projection-v1',
              'source_manifest_sha256':hashlib.sha256(mbytes).hexdigest(),
              'source_registry':m.get('source_registry'),
              'basis':BASIS,
              'meaning':'Unique registered minimum of structure, NOT observed search origin',
              'counts':dict(sorted(count.items())),
              'shard_sha256':{str(rel):fingerprint(stage/rel) for table in TABLES for rel,_ in parts[table]},
              'original_export_unchanged':True,'scoring_eligibility_recomputed':False}
        (stage/'projection_manifest.json').write_text(json.dumps(meta,sort_keys=True,indent=2)+'\n')
        (stage/'PROJECTION_COMPLETE').write_text('PASS\n')
        os.rename(stage,dst)
        return meta
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lean',default='/scratch/11588/ndn454/sm_anskey/saddlebench_diagnostics/saddlebench_lean_z2_j9rmh')
    p.add_argument('--output',required=True)
    args=p.parse_args();result=build(args.lean,args.output)
    print('ASSOCIATED_START_BACKFILL=PASS')
    print('COUNTS='+json.dumps(result['counts'],sort_keys=True))
    print('SOURCE_MANIFEST_SHA256='+result['source_manifest_sha256'])
    print('ORIGINAL_REGISTRY_MODIFIED=NO')
    print('OBSERVED_STARTS_SYNTHESIZED=NO')
    print('OUTPUT='+str(Path(args.output).absolute()))
if __name__=='__main__':main()
