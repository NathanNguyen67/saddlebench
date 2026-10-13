#!/usr/bin/env python3
"""Public-reference, CPU-only cross-cluster scorer; does not upload predictions."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

PUBLIC='traingletrain/saddlebench-prototype'
REFERENCE='traingletrain/saddlebench-reference-private'
REGISTRY_SHA='85d37e55448b544473b34e7bb244823e80ddc85091bbe6ae3e771c7beb4b0a41'


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
    if private_meta.get('case_count')!=123 or refmeta.get('reference_count')!=2387:
        raise RuntimeError('REFERENCE_COUNTS_MISMATCH')
    for path,sha in hashes.items():
        if not isinstance(sha,str) or len(sha)!=64 or not (root/path).is_file() or digest(root/path)!=sha:
            raise RuntimeError('REFERENCE_CHECKSUM_FAILED:'+str(path))
    if digest(root/'reference/reference.jsonl')!=refmeta['reference_sha256']:
        raise RuntimeError('REACTION_REFERENCE_HASH_MISMATCH')
    with Path(cases_path).open(newline='',encoding='utf8') as f:
        cases=list(csv.DictReader(f))
    if len(cases)!=123 or any(x['registry_sha256']!=REGISTRY_SHA for x in cases):
        raise RuntimeError('PUBLIC_INPUTS_SNAPSHOT_MISMATCH')
    return True


class Progress:
    """Progress goes to stderr only; native JSON reports remain byte-schema-compatible."""
    def __init__(self,enabled=True):
        self.enabled=enabled
        self.started=time.monotonic()
        self.last={}

    def __call__(self,case,phase,completed,total):
        if not self.enabled:return
        now=time.monotonic()
        key=(case,phase)
        last=self.last.get(key)
        done=total>0 and completed>=total
        if completed not in (0,1) and not done and last is not None and now-last<5.0:
            return
        if last is not None and not done and completed not in (0,1) and completed%10:
            return
        self.last[key]=now
        pct=f'{100*completed/total:.1f}' if total else 'n/a'
        print(f'SCORING_PROGRESS case={case} phase={phase} completed={completed} '
              f'total={total} pct={pct} elapsed_seconds={now-self.started:.1f}',
              file=sys.stderr,flush=True)


def main():
    a=argparse.ArgumentParser(description=__doc__)
    group=a.add_mutually_exclusive_group(required=True)
    group.add_argument('--case-id')
    group.add_argument('--submission-csv')
    a.add_argument('--submission',help='predicted saddles as ASE .traj')
    a.add_argument('--elapsed-seconds',type=float)
    a.add_argument('--output',required=True)
    a.add_argument('--cache-dir',default=str(Path.home()/'.cache/saddlebench_reference'))
    a.add_argument('--no-progress',action='store_true',help='silence stderr progress reports')
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
        if d.is_symlink():raise SystemExit('SYMLINK_IN_REFERENCE_CACHE_PATH')
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    cache.chmod(0o700)
    progress=Progress(not a.no_progress)
    from huggingface_hub import snapshot_download, hf_hub_download
    progress('all','reference_download',0,1)
    try:
        ref=snapshot_download(repo_id=REFERENCE,repo_type='dataset',cache_dir=str(cache),token=False,
            allow_patterns=['private_manifest.json','reference/*','diagnostic_saddles.jsonl.gz','CHECKSUMS.json'])
    except Exception as e:
        raise SystemExit('PUBLIC_HF_REFERENCE_UNAVAILABLE: verify reference dataset visibility and network; '+type(e).__name__)
    casefile=hf_hub_download(repo_id=PUBLIC,repo_type='dataset',filename='cases.csv',cache_dir=str(cache),token=False)
    progress('all','reference_download',1,1)
    progress('all','reference_integrity',0,1)
    verify_reference(ref,casefile)
    progress('all','reference_integrity',1,1)
    from saddlebench.prototype.release_mvp import grade_case, grade_batch
    with tempfile.TemporaryDirectory(prefix='saddlebench_score_') as tmp:
        p=Path(tmp);shutil.copyfile(casefile,p/'cases.csv')
        if a.case_id:
            result=grade_case(p,ref,a.case_id,Path(a.submission).absolute(),a.elapsed_seconds,
                              progress=lambda stage,n,total:progress(a.case_id,stage,n,total))
        else:
            result=grade_batch(p,ref,Path(a.submission_csv).absolute(),progress=progress)
    with out.open('x',encoding='utf8') as f:
        json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    print('SCORE_VERIFY=PASS')
    print('OUTPUT='+str(out))
    print('REFERENCE_ACCESS=PUBLIC_ANONYMOUS')
    if a.case_id:
        print('COVERAGE='+str(result['coverage']))
        print('PROVISIONAL_SCORE_PER_HOUR='+str(result['provisional_score_per_hour']))

if __name__=='__main__':main()
