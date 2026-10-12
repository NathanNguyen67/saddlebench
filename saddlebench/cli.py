"""Local-only SaddleBench CLI. Does not load SaddleMill/Benchmark or producer directories."""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
from saddlebench.io.extxyz import read_extxyz
from saddlebench.reference.snapshot import load_reference,canonical,sha
from saddlebench.scoring.engine import score

def _write_atomic(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.saddlebench-',delete=False) as f:
        temp=Path(f.name)
        try:
            f.write(raw)
            f.flush();os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True);raise
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def main(argv=None):
    p=argparse.ArgumentParser(prog='saddlebench')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('score',help='Score proposed extxyz saddles against immutable reference')
    s.add_argument('submission')
    s.add_argument('--reference',required=True)
    s.add_argument('--expected-manifest-sha256',help='Optional separately pinned reference manifest SHA-256')
    s.add_argument('--elapsed-seconds',type=float,required=True)
    s.add_argument('--out-prefix',default='saddlebench_score')
    s.add_argument('--start-minimum-id')
    s.add_argument('--pes-id')
    s.add_argument('--parent-structure-id')
    args=p.parse_args(argv)
    if args.command=='score':
        submissions=read_extxyz(args.submission)
        for row in submissions:
            for k,v in [('starting_minimum_id',args.start_minimum_id),('pes_id',args.pes_id),('parent_structure_id',args.parent_structure_id)]:
                if v is not None:
                    if row[k] not in (None,v):raise ValueError('CONFLICTING_CLI_AND_FRAME_SCOPE: '+k)
                    row[k]=v
        snap=load_reference(args.reference)
        actual_sha=sha((snap.path/'manifest.json').read_bytes())
        if args.expected_manifest_sha256 and args.expected_manifest_sha256!=actual_sha:
            raise ValueError('EXPECTED_REFERENCE_MANIFEST_SHA_MISMATCH')
        report=score(submissions,snap,args.elapsed_seconds)
        report['reference_manifest_sha256']=actual_sha
        prefix=Path(args.out_prefix)
        _write_atomic(Path(str(prefix)+'.json'),(canonical(report)+'\n').encode())
        fields=['submission_id','starting_minimum_id','pes_id','parent_structure_id','geometry_state',
            'matched_identity_id','matched_reference_id','fo_state','barrier_state','barrier_ev',
            'connection_state','provenance_state','eligible','matches','incompatibilities','reason_codes']
        import io
        buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for row in report['per_submission']:
            writer.writerow({k:canonical(row[k]) if k in ('matches','reason_codes','incompatibilities') else row[k] for k in fields})
        _write_atomic(Path(str(prefix)+'.csv'),buf.getvalue().encode('utf-8'))
        print(canonical({'score_per_wall_hour':report['primary_score_per_wall_hour'],
                         'eligible_unique_count':report['eligible_unique_count'],
                         'coverage':report['coverage'],'json':str(prefix)+'.json','csv':str(prefix)+'.csv'}))
        return 0
    return 2
if __name__=='__main__':raise SystemExit(main())
