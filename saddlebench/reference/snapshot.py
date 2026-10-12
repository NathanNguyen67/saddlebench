"""Versioned read-only portable reference; does not access producers at scoring time."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from dataclasses import dataclass
from .safe_output import AtomicDirectory

SCHEMA = 'saddlebench-reference-v1'
HEX = re.compile(r'^[0-9a-f]{64}$')
FIELDS = ('reference_id','identity_id','parent_structure_id','starting_minimum_id','pes_id','geometry','energy','hessian','doublemin','evidence_sources','uncertainty_reasons')

def canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def finite(x):
    return type(x) in (int,float) and math.isfinite(x)

def validate_geometry(g):
    if not isinstance(g,dict): raise ValueError('GEOMETRY_NOT_OBJECT')
    keys=('numbers','positions_angstrom','cell_angstrom','pbc','fixed_indices','constraints_known')
    if any(k not in g for k in keys): raise ValueError('GEOMETRY_FIELD_MISSING')
    n=g['numbers']
    if not isinstance(n,list) or not n or any(type(x)!=int or x<1 or x>118 for x in n):raise ValueError('INVALID_ATOMIC_NUMBERS')
    pos=g['positions_angstrom']
    if not isinstance(pos,list) or len(pos)!=len(n) or any(not isinstance(v,list) or len(v)!=3 or not all(finite(q) for q in v) for v in pos):raise ValueError('INVALID_POSITIONS')
    cell=g['cell_angstrom']
    if not isinstance(cell,list) or len(cell)!=3 or any(not isinstance(v,list) or len(v)!=3 or not all(finite(q) for q in v) for v in cell):raise ValueError('INVALID_CELL')
    if not isinstance(g['pbc'],list) or len(g['pbc'])!=3 or any(type(x)!=bool for x in g['pbc']):raise ValueError('INVALID_PBC')
    if type(g['constraints_known'])!=bool:raise ValueError('INVALID_CONSTRAINT_KNOWN')
    fix=g['fixed_indices']
    if not isinstance(fix,list) or any(type(x)!=int or x<0 or x>=len(n) for x in fix) or sorted(set(fix))!=fix:raise ValueError('INVALID_FIXED_INDICES')

def validate_record(r):
    if not isinstance(r,dict) or any(k not in r for k in FIELDS):raise ValueError('REFERENCE_FIELDS_MISSING')
    if any(not isinstance(r[k],str) or not r[k] for k in ('reference_id','identity_id','parent_structure_id')):raise ValueError('INVALID_REFERENCE_ID')
    if any(r[k] is not None and (not isinstance(r[k],str) or not r[k]) for k in ('starting_minimum_id','pes_id')):raise ValueError('INVALID_SCOPE')
    validate_geometry(r['geometry'])
    if not isinstance(r['evidence_sources'],dict) or not isinstance(r['uncertainty_reasons'],list) or not all(isinstance(s,str) for s in r['uncertainty_reasons']):raise ValueError('INVALID_EVIDENCE_METADATA')
    for typ in ('energy','hessian','doublemin'):
        if r[typ] is not None and not isinstance(r[typ],dict):raise ValueError('INVALID_EVIDENCE_'+typ)
    # No unsupported paths in the portable geometry or evidence records.
    if any(k.endswith('_path') or k.endswith('_uri') for k in r):raise ValueError('EXTERNAL_REFERENCE_PATH')

@dataclass(frozen=True)
class ReferenceSnapshot:
    manifest: dict
    records: tuple
    path: Path
    by_numbers: dict | None = None

    def eligible_scoped(self,start,pes,parent):
        # Delegate gate definitions to scorer; include only known complete scoped denominator.
        from saddlebench.scoring.engine import classify
        group={}
        for r in self.records:
            if r['starting_minimum_id']==start and r['pes_id']==pes and r['parent_structure_id']==parent:
                states=classify(r,start,pes,parent,self.manifest['registry_watermark'],self.manifest['event_cutoff'])
                group.setdefault(r['identity_id'],[]).append(states['eligible'])
        eligible={k for k,values in group.items() if values and all(values)}
        return eligible, bool(self.manifest['coverage'].get('eligible_denominator_complete',False))

def load_reference(path):
    p=Path(path)
    manifest_raw=(p/'manifest.json').read_bytes()
    manifest=json.loads(manifest_raw)
    if manifest.get('schema_version')!=SCHEMA:raise ValueError('UNSUPPORTED_REFERENCE_SCHEMA')
    for k in ('reference_version','registry_sha256','registry_watermark','producer_source_id','scientific_contract_id','scoring_policy_id','records_sha256','record_count','coverage','event_cutoff'):
        if k not in manifest:raise ValueError('INVALID_MANIFEST_'+k)
    if not HEX.fullmatch(manifest['registry_sha256']) or not HEX.fullmatch(manifest['records_sha256']):raise ValueError('INVALID_REFERENCE_SHA')
    if type(manifest['registry_watermark'])!=int or manifest['registry_watermark']<0:raise ValueError('INVALID_WATERMARK')
    if type(manifest['record_count'])!=int or manifest['record_count']<0:raise ValueError('INVALID_RECORD_COUNT')
    if manifest['event_cutoff'] is not None and (type(manifest['event_cutoff'])!=int or manifest['event_cutoff']<0):raise ValueError('INVALID_EVENT_CUTOFF')
    if not isinstance(manifest['coverage'],dict):raise ValueError('INVALID_COVERAGE')
    raw=(p/'records.jsonl').read_bytes()
    if sha(raw)!=manifest['records_sha256']:raise ValueError('REFERENCE_CHECKSUM_MISMATCH')
    lines=raw.splitlines()
    if len(lines)!=manifest['record_count']:raise ValueError('REFERENCE_COUNT_MISMATCH')
    records=[]
    for line in lines:
        r=json.loads(line)
        validate_record(r)
        if line.decode('utf-8')!=canonical(r):raise ValueError('NONCANONICAL_RECORD_JSON')
        records.append(r)
    ids=[r['reference_id'] for r in records]
    if ids!=sorted(set(ids)):raise ValueError('REFERENCE_IDS_NOT_UNIQUE_SORTED')
    _validate_groups(records)
    by_numbers={}
    for record in records:by_numbers.setdefault(tuple(record["geometry"]["numbers"]),[]).append(record)
    return ReferenceSnapshot(manifest,tuple(records),p,{k:tuple(v) for k,v in by_numbers.items()})

def export_snapshot(records, destination, metadata, *, protected_roots=()):
    """Publish an immutable snapshot via pinned no-follow dirfd, never overwrite."""
    dest = Path(destination)
    if not isinstance(metadata, dict): raise ValueError('INVALID_METADATA')
    rows = sorted(records, key=lambda x: x['reference_id'])
    ids = []
    for row in rows: validate_record(row); ids.append(row['reference_id'])
    if len(set(ids)) != len(ids): raise ValueError('DUPLICATE_REFERENCE_ID')
    _validate_groups(rows)
    raw = (''.join(canonical(x)+'\n' for x in rows)).encode('utf-8')
    required = ('reference_version','registry_sha256','registry_watermark','event_cutoff',
                'producer_source_id','scientific_contract_id','scoring_policy_id','coverage')
    if any(k not in metadata for k in required): raise ValueError('INVALID_EXPORT_METADATA')
    manifest = {**metadata,'schema_version':SCHEMA,'records_sha256':sha(raw),'record_count':len(rows)}
    if not HEX.fullmatch(manifest['registry_sha256']): raise ValueError('INVALID_REGISTRY_SHA')
    if not isinstance(manifest['coverage'],dict): raise ValueError('INVALID_COVERAGE')
    with AtomicDirectory(dest, protected_roots=protected_roots, create_parents=True,
                         prefix='.saddlebench-') as writer:
        writer.write_bytes('records.jsonl', raw)
        writer.write_bytes('manifest.json', (canonical(manifest)+'\n').encode('utf-8'))
        _ = load_reference(writer.path)
        writer.commit()
    return dest


def _validate_groups(rows):
    """No inferred transitive grouping. Verify every sharing identity is complete-link <=0.1 A."""
    from saddlebench.matching.geometry import complete_link_diameter
    groups={}
    for r in rows:groups.setdefault(r['identity_id'],[]).append(r)
    for identity,members in groups.items():
        if len({r['parent_structure_id'] for r in members})>1:
            raise ValueError('CROSS_PARENT_SHARING_IDENTITY')
        try:diameter=complete_link_diameter([r['geometry'] for r in members])
        except ValueError as exc:raise ValueError('INCOMPATIBLE_SHARING_GROUP') from exc
        if diameter>0.1:raise ValueError('INCOMPLETE_LINK_SHARING_GROUP')
