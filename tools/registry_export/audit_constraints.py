#!/usr/bin/env python3
"""Read-only metadata audit; makes no guess about unknown constraints."""
from collections import Counter
import argparse
import json
from pathlib import Path


def rows(path):
    with path.open('r',encoding='utf-8') as f:
        for line in f:
            yield json.loads(line)


def run(folder):
    ids = {}
    for name in ('saddles','minima'):
        mapping={}
        for row in rows(folder/(name+'.jsonl')):
            if row.get('geometry_id'):
                mapping[row['geometry_id']]=name
        ids[name]=mapping
    counts={name:Counter() for name in ids}
    for row in rows(folder/'geometries.jsonl'):
        gid=row.get('geometry_id')
        for name in ids:
            if gid not in ids[name]:continue
            known=row.get('constraints_known')
            bucket='verified' if known is True else 'unknown'
            counts[name][bucket]+=1
            if known is True and row.get('fixed_indices') is None:
                counts[name]['malformed_known_indices']+=1
            if known is not True:
                for reason in row.get('uncertainty_reasons',[]):
                    counts[name]['reason:'+reason]+=1
    return {'schema':'saddlebench-export-constraints-audit-v1',
            'qualification':'metadata_inventory_only_not_eligibility_or_geometric_match',
            'scope':'All published saddle and minimum registry records with emitted geometries',
            'counts':{name:dict(sorted(v.items())) for name,v in counts.items()},
            'warning':'Verified minima do not imply verified saddle constraints. Unknown geometry metadata is not unconstrained.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=run(a.evidence)
    with a.out.open('x',encoding='utf-8') as f:
        json.dump(result,f,sort_keys=True,indent=2);f.write('\n')
    print('CONSTRAINT_AUDIT=PASS')
    for name,counts in result['counts'].items():
        print(name.upper()+'_CONSTRAINTS_VERIFIED='+str(counts.get('verified',0)))
        print(name.upper()+'_CONSTRAINTS_UNKNOWN='+str(counts.get('unknown',0)))
if __name__=='__main__':main()
