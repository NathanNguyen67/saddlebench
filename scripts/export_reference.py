#!/usr/bin/env python3
"""Read-only *staging exporter* from explicitly validated Option-B portable evidence.

Producer joins must first be verified on actual LS6 source. This script refuses to
infer unknown production schema columns or silently upgrade historical flags.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from urllib.parse import quote

from saddlebench.reference.snapshot import export_snapshot

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pinned-registry',required=True,help='Immutable published SQLite file (read-only)')
    p.add_argument('--expected-registry-sha256',required=True)
    p.add_argument('--registry-watermark',required=True,type=int)
    p.add_argument('--verified-evidence-jsonl',required=True,help='Reviewed materialized per-row verified evidence, NOT raw registry')
    p.add_argument('--evidence-qualification',choices=['synthetic-fixture','externally-reviewed-producer-join'],required=True)
    p.add_argument('--producer-source-id',required=True)
    p.add_argument('--reference-version',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--coverage-complete',action='store_true',help='Only when enumeration/provenance completeness independently verified')
    args=p.parse_args(argv)
    reg=Path(args.pinned_registry)
    if reg.is_symlink():raise ValueError('PINNED_REGISTRY_MUST_NOT_BE_SYMLINK')
    if args.coverage_complete and args.evidence_qualification!='synthetic-fixture':
        raise ValueError('REAL_COVERAGE_REQUIRES_INDEPENDENT_PRODUCER_ATTESTATION')
    digest=hashlib.sha256()
    with reg.open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):digest.update(chunk)
    if digest.hexdigest()!=args.expected_registry_sha256:raise ValueError('PINNED_REGISTRY_SHA_MISMATCH')
    uri='file:'+quote(str(reg.resolve()))+'?mode=ro&immutable=1'
    con=sqlite3.connect(uri,uri=True)
    try:
        con.execute('PRAGMA query_only=ON')
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('PINNED_REGISTRY_INTEGRITY_FAILED')
        # We do not guess the publication metadata table; watermark is explicitly operator-pinned.
    finally:con.close()
    digest_after=hashlib.sha256(reg.read_bytes()).hexdigest()
    if digest_after!=digest.hexdigest():raise ValueError('REGISTRY_CHANGED_DURING_EXPORT')
    rows=[json.loads(s) for s in Path(args.verified_evidence_jsonl).read_text().splitlines() if s.strip()]
    coverage={'eligible_denominator_complete':args.coverage_complete,
              'producer_mapping':'SYNTHETIC_ONLY' if args.evidence_qualification=='synthetic-fixture' else 'EXTERNAL_REVIEW_REQUIRED',
              'input_evidence_sha256':hashlib.sha256(Path(args.verified_evidence_jsonl).read_bytes()).hexdigest(),
              'limitations':['Full historical Search coverage and source schema joins not verified by this collector']}
    export_snapshot(rows,args.out,{'reference_version':args.reference_version,
        'registry_sha256':digest.hexdigest(),'registry_watermark':args.registry_watermark,'event_cutoff':None,
        'producer_source_id':args.producer_source_id,'scientific_contract_id':'SCIENTIFIC_CONTRACT_20261002_v1',
        'scoring_policy_id':'SCORING_POLICY_20261011_v1','coverage':coverage})
    print(json.dumps({'snapshot':args.out,'count':len(rows),'registry_sha256':digest.hexdigest(),
        'qualification':args.evidence_qualification,'real_source_mapping_verified_by_script':False},sort_keys=True))
if __name__=='__main__':raise SystemExit(main())
