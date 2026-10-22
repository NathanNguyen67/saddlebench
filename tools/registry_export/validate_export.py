#!/usr/bin/env python3
"""Validate additive data links and checksums after RC14 and enrichment."""
from pathlib import Path
import argparse
import json
from enrich_scientific import check_evidence, hash_file


def validate(base,science):
    m=check_evidence(base)
    s=json.loads((science/'manifest_science.json').read_text())
    if s['source_registry_sha256']!=m['registry']['sha256'] or s['source_evidence_manifest_sha256']!=hash_file(base/'manifest.json'):
        raise ValueError('REGISTRY_SNAPSHOT_MISMATCH')
    for rel,sha in s['sha256'].items():
        if not rel or Path(rel).is_absolute() or '..' in Path(rel).parts or hash_file(science/rel)!=sha:
            raise ValueError('SCIENCE_SHA_MISMATCH:'+rel)
    for name,expected in [('saddles_enriched.jsonl',s['row_counts']['saddles_enriched']),('reaction_triplets.jsonl',s['row_counts']['reaction_triplets']),('hessian_modes.jsonl',s['row_counts']['hessian_mode_source_records'])]:
        with (science/name).open(encoding='utf-8') as file:
            actual=sum(1 for line in file if line.strip())
        if actual!=expected:
            raise ValueError('COUNT_MISMATCH:'+name)
    if s['row_counts']['saddles_enriched']!=m['record_counts']['saddles.jsonl']:
        raise ValueError('SADDLE_LOSS')
    if s['row_counts']['reaction_triplets']!=m['record_counts']['doublemin_observations.jsonl']:
        raise ValueError('OBSERVATION_LOSS')
    print('SCIENTIFIC_EXPORT_VALIDATION=PASS')
    print('BASE_REGISTRY_SHA='+s['source_registry_sha256'])
    print('SCIENTIFIC_QUALIFICATION=UNQUALIFIED')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--science',type=Path,required=True)
    a=p.parse_args();validate(a.evidence,a.science)
