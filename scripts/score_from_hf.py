#!/usr/bin/env python3
"""Authenticated, CPU-only cross-cluster scorer; does not upload predictions."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

PUBLIC='traingletrain/saddlebench-prototype'
PRIVATE='traingletrain/saddlebench-reference-private'
REGISTRY_SHA='e33f7ac55cec2bc208fd1bb4ee74d047224fa072a7a43be1effdb4891c495c88'

def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for part in iter(lambda:f.read(1<<20),b''):h.update(part)
    return h.hexdigest()

def verify_reference(root, cases_path):
    root=Path(root)
    private_meta=json.loads((root/'private_manifest.json').read_text())
    refmeta=json.loads((root/'reference/manifest.json').read_text())
    hashes=json.loads((root/'CHECKSUMS.json').read_text())
    if private_meta.get('source_registry_sha256')!=REGISTRY_SHA or refmeta.get('registry_sha256')!=REGISTRY_SHA:
        raise RuntimeError('REFERENCE_REGISTRY_SHA_MISMATCH')
    if private_meta.get('mode')!='all_saddles' or refmeta.get('mode')!='all_saddles':
        raise RuntimeError('REFERENCE_MODE_MISMATCH')
    if private_meta.get('case_count')!=123 or refmeta.get('reference_count')!=2231:
        raise RuntimeError('REFERENCE_COUNTS_MISMATCH')
    for path,sha in hashes.items():
        if not isinstance(sha,str) or len(sha)!=64 or not (root/path).is_file() or digest(root/path)!=sha:
            raise RuntimeError('PRIVATE_REFERENCE_CHECKSUM_FAILED:'+str(path))
    if digest(root/'reference/reference.jsonl')!=refmeta['reference_sha256']:
        raise RuntimeError('REACTION_REFERENCE_HASH_MISMATCH')
    with Path(cases_path).open(newline='',encoding='utf8') as f:
        cases=list(csv.DictReader(f))
    if len(cases)!=123 or any(x['registry_sha256']!=REGISTRY_SHA for x in cases):
        raise RuntimeError('PUBLIC_INPUTS_SNAPSHOT_MISMATCH')
    return True

def main():
    a=argparse.ArgumentParser(description=__doc__)
    group=a.add_mutually_exclusive_group(required=True)
    group.add_argument('--case-id')
    group.add_argument('--submission-csv')
    a.add_argument('--submission',help='predicted saddles as ASE .traj')
    a.add_argument('--elapsed-seconds',type=float)
    a.add_argument('--output',required=True)
    a.add_argument('--cache-dir',default=str(Path.home()/'.cache/saddlebench_private'))
    a=a.parse_args()
    if a.case_id and (not a.submission or a.elapsed_seconds is None):
        raise SystemExit('SINGLE_CASE_REQUIRES_SUBMISSION_AND_ELAPSED_SECONDS')
    if a.submission_csv and (a.submission or a.elapsed_seconds is not None):
        raise SystemExit('BATCH_DOES_NOT_TAKE_SINGLE_SUBMISSION')
    out=Path(a.output).absolute()
    if out.exists():raise SystemExit('OUTPUT_ALREADY_EXISTS: '+str(out))
    os.umask(0o077)
    cache=Path(a.cache_dir).expanduser().absolute()
    for d in (cache,*cache.parents):
        if d.is_symlink():raise SystemExit('SYMLINK_IN_PRIVATE_CACHE_PATH')
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    cache.chmod(0o700)
    from huggingface_hub import snapshot_download, hf_hub_download
    try:
        ref=snapshot_download(repo_id=PRIVATE,repo_type='dataset',cache_dir=str(cache),
            allow_patterns=['private_manifest.json','reference/*','diagnostic_saddles.jsonl.gz','CHECKSUMS.json'])
    except Exception as e:
        raise SystemExit('PRIVATE_HF_DOWNLOAD_DENIED_OR_FAILED: authenticate as owner with `hf auth login`; '+type(e).__name__)
    casefile=hf_hub_download(repo_id=PUBLIC,repo_type='dataset',filename='cases.csv',cache_dir=str(cache))
    verify_reference(ref,casefile)
    from saddlebench.prototype.release_mvp import grade_case, grade_batch
    with tempfile.TemporaryDirectory(prefix='saddlebench_score_') as tmp:
        p=Path(tmp);shutil.copyfile(casefile,p/'cases.csv')
        if a.case_id:
            result=grade_case(p,ref,a.case_id,Path(a.submission).absolute(),a.elapsed_seconds)
        else:
            result=grade_batch(p,ref,Path(a.submission_csv).absolute())
    with out.open('x',encoding='utf8') as f:
        json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    print('SCORE_VERIFY=PASS')
    print('OUTPUT='+str(out))
    print('PRIVATE_REFERENCE_USED_LOCALLY=YES')
    if a.case_id:
        print('COVERAGE='+str(result['coverage']))
        print('PROVISIONAL_SCORE_PER_HOUR='+str(result['provisional_score_per_hour']))

if __name__=='__main__':main()
