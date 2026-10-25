"""Validated cutoff-neutral reaction catalog; no dataset identity in scoring code.

Producer owns scientific-evidence status. Scoring uses that supplied status,
not registry flags or topology, and applies the caller's energy policy.
"""
from __future__ import annotations
import csv, hashlib, json, math
from pathlib import Path

SCHEMA='saddlebench-reaction-catalog-v2'

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()

def load_catalog(root,casefile,*,barrier_max_eV=1.0,allow_registry_reported=False,case_id=None):
    root=Path(root)
    if barrier_max_eV is not None and (type(barrier_max_eV) not in (float,int) or not math.isfinite(barrier_max_eV) or barrier_max_eV<=0):
        raise ValueError('INVALID_BARRIER_POLICY')
    manifest=json.loads((root/'catalog_manifest.json').read_text())
    if manifest.get('schema_version')!=SCHEMA:raise ValueError('WRONG_CATALOG_SCHEMA')
    if manifest.get('evidence_standard') not in ('scientific_verified','lean_registry_reported_not_force_verified'):
        raise ValueError('UNKNOWN_CATALOG_EVIDENCE_STANDARD')
    scoped=manifest.get('case_shards') if case_id is not None else None
    if scoped is not None:
        entry=scoped.get(case_id)
        if not entry or not isinstance(entry.get('path'),str):raise ValueError('CASE_NOT_IN_CATALOG_SHARDS')
        rel=Path(entry['path'])
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='case_shards' or (root/rel).is_symlink():
            raise ValueError('UNSAFE_CATALOG_CASE_SHARD')
        source=root/rel
        if digest(source)!=entry['sha256']:raise ValueError('CATALOG_CASE_SHARD_CHECKSUM_MISMATCH')
    else:
        source=root/'reference.jsonl'
        if digest(source)!=manifest.get('reference_sha256'):
            raise ValueError('CATALOG_REFERENCE_CHECKSUM_MISMATCH')
    if digest(casefile)!=manifest.get('cases_sha256'):
        raise ValueError('CATALOG_CASES_CHECKSUM_MISMATCH')
    with Path(casefile).open(newline='',encoding='utf8') as f:
        cases=list(csv.DictReader(f))
    if len(cases)!=manifest.get('case_count') or not cases:
        raise ValueError('CATALOG_CASE_COUNT_MISMATCH')
    scopes=[(r.get('structure_id'),r.get('pes_id')) for r in cases]
    if any(not a or not b for a,b in scopes) or len(set(scopes))!=len(scopes):
        raise ValueError('DUPLICATE_OR_AMBIGUOUS_STRUCTURE_PES_CASE')
    counts={'seen':0,'geometry_missing':0,'scientifically_verified':0,'registry_reported':0,'selected':0,'barrier_unknown':0,'barrier_excluded':0,'science_excluded':0}
    rows=[];ids=set()
    with source.open(encoding='utf8') as f:
        for line in f:
            if not line.strip():continue
            r=json.loads(line)
            rid=r.get('reaction_id')
            if not isinstance(rid,str) or not rid or rid in ids:raise ValueError('INVALID_OR_DUPLICATE_REACTION_ID')
            ids.add(rid);counts['seen']+=1
            if not isinstance(r.get('structure_id'),str) or not isinstance(r.get('pes_id'),str):
                raise ValueError('INVALID_CATALOG_SCOPE')
            members=r.get('members')
            if not isinstance(members,list):raise ValueError('INVALID_CATALOG_MEMBERS')
            if not members:
                counts['geometry_missing']+=1;continue
            if r.get('science_verified') is True:
                counts['scientifically_verified']+=1
            elif r.get('registry_recorded_connected') is True:
                counts['registry_reported']+=1
            if not (r.get('science_verified') is True or (allow_registry_reported and r.get('registry_recorded_connected') is True)):
                counts['science_excluded']+=1;continue
            energy=r.get('barrier_eV')
            if energy is None:
                counts['barrier_unknown']+=1
                if barrier_max_eV is not None:continue
            else:
                if type(energy) not in (int,float) or not math.isfinite(energy):raise ValueError('INVALID_CATALOG_BARRIER')
                if energy<0 or (barrier_max_eV is not None and energy>=barrier_max_eV):
                    counts['barrier_excluded']+=1;continue
            for m in members:
                g=m.get('geometry') if isinstance(m,dict) else None
                if not isinstance(g,dict) or not isinstance(g.get('numbers'),list) or len(g['numbers'])==0:
                    raise ValueError('INVALID_CATALOG_MEMBER_GEOMETRY')
            rows.append(r);counts['selected']+=1
    expected=(scoped[case_id]['count'] if scoped is not None else manifest.get('reference_count'))
    if counts['seen']!=expected:raise ValueError('CATALOG_ROW_COUNT_MISMATCH')
    policy={'catalog_schema':SCHEMA,'catalog_sha256':manifest['reference_sha256'],
            'source_registry_manifest_sha256':manifest.get('source_manifest_sha256'),
            'evidence_policy':('allow_registry_reported_EXPLORATORY_NOT_SCIENTIFIC_CERTIFICATION' if allow_registry_reported else 'science_verified_only'),
            'barrier_max_eV_exclusive':barrier_max_eV,
            'missing_barrier_policy':('included_no_cutoff' if barrier_max_eV is None else 'excluded'),
            'cutoff_neutral_reference':True,'selection_counts':counts}
    return rows,policy

def download_catalog_subset(repo_id,prefix,cache_root,case_ids):
    """HF is only a transport adapter; scorer receives ordinary verified files."""
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo_id):
        raise ValueError('INVALID_HF_CATALOG_REPO')
    pp=Path(prefix)
    if pp.is_absolute() or '..' in pp.parts or not re.fullmatch(r'catalogs/[a-f0-9]{16}',pp.as_posix()):
        raise ValueError('INVALID_HF_CATALOG_PREFIX')
    from huggingface_hub import hf_hub_download
    root=Path(cache_root).expanduser().absolute()
    for x in (root,*root.parents):
        if x.is_symlink():raise ValueError('HF_CATALOG_CACHE_SYMLINK')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    def fetch(relative):
        f=pp/relative
        hf_hub_download(repo_id=repo_id,repo_type='dataset',filename=f.as_posix(),local_dir=root)
    fetch('catalog_manifest.json');fetch('cases.csv')
    catalog=root/pp
    manifest=json.loads((catalog/'catalog_manifest.json').read_text())
    if manifest.get('schema_version')!=SCHEMA:raise ValueError('REMOTE_CATALOG_SCHEMA_UNSUPPORTED')
    all_shards=manifest.get('case_shards',{})
    for cid in sorted(set(case_ids)):
        entry=all_shards.get(cid)
        if not isinstance(entry,dict):raise ValueError('REMOTE_CATALOG_CASE_ABSENT:'+cid)
        rel=Path(entry.get('path',''))
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='case_shards':
            raise ValueError('REMOTE_CATALOG_UNSAFE_PATH')
        fetch(rel.as_posix())
        if digest(catalog/rel)!=entry.get('sha256'):
            raise ValueError('REMOTE_CATALOG_CASE_SHA_MISMATCH')
    if digest(catalog/'cases.csv')!=manifest.get('cases_sha256'):
        raise ValueError('REMOTE_CATALOG_CASES_SHA_MISMATCH')
    return catalog
