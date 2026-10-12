#!/usr/bin/env python3
"""Create a standalone PUBLIC saddlebench repository from an audited allowlist."""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REPO='NathanNguyen67/saddlebench'

def public_files(root):
    root=Path(root).absolute()
    allow={'README.md','LICENSE','pyproject.toml','.gitignore'}
    data={}
    for p in root.rglob('*'):
        if p.is_symlink():raise ValueError('PUBLIC_SOURCE_SYMLINK:'+str(p))
        if not p.is_file():continue
        n=p.relative_to(root).as_posix()
        synthetic=(n.startswith('tests/fixtures/synthetic_evidence_v1/') and
                   (n.endswith('.jsonl') or n.endswith('.json') or n.endswith('.txt'))) or (
                   n.startswith('examples/') and (n.endswith(('.jsonl','.json','.csv','.extxyz','.py'))))
        if n in allow or (n.startswith(('saddlebench/','tests/','scripts/')) and n.endswith('.py')) or (n.startswith('docs/') and n.endswith('.md')) or synthetic:
            if p.stat().st_size>200000:raise ValueError('PUBLIC_SOURCE_FILE_TOO_LARGE:'+n)
            raw=p.read_bytes()
            if b'\x00' in raw:raise ValueError('BINARY_SOURCE_FORBIDDEN:'+n)
            data[n]=raw.decode('utf8')
        elif '/__pycache__/' in '/'+n or n.endswith('.pyc') or n.startswith('.pytest_cache/'):
            continue
        else:
            raise ValueError('UNEXPECTED_FILE_IN_PUBLIC_SOURCE:'+n)
    if not {'README.md','pyproject.toml','LICENSE'}.issubset(data):raise ValueError('PUBLIC_SOURCE_INCOMPLETE')
    if not any(k.endswith('release_mvp.py') for k in data):raise ValueError('MISSING_SCORER_IMPLEMENTATION')
    # A public synthetic test fixture is intentionally allowed. Never allow
    # files from the registry snapshot or a private reference bundle.
    fixture=data.get('tests/fixtures/synthetic_evidence_v1/manifest.json','')
    demo=data.get('examples/reference_v1/manifest.json','')
    if 'synthetic_fixture_v1' not in fixture or 'synthetic-test-only' not in demo:
        raise ValueError('SYNTHETIC_FIXTURE_PROVENANCE_MISSING')
    if any(n in data for n in ('reference.jsonl','private_manifest.json','diagnostic_saddles.jsonl.gz')):
        raise ValueError('FORBIDDEN_PRIVATE_DATA_FILENAME')
    return data

def credential():
    val=os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if val:return val
    gh=shutil.which('gh')
    if gh:
        result=subprocess.run([gh,'auth','token'],capture_output=True,text=True,check=False)
        if result.returncode==0 and result.stdout.strip():return result.stdout.strip()
    if not sys.stdin.isatty():raise RuntimeError('GITHUB_AUTH_REQUIRED: run in an interactive shell')
    print('GitHub authentication needed: use `gh auth login` or enter a fine-grained repository-creation token here. Never put the token in chat.')
    val=getpass('GitHub token (hidden): ').strip()
    if not val:raise RuntimeError('NO_GITHUB_TOKEN')
    return val

def request(token,method,path,data=None):
    url='https://api.github.com'+path
    payload=json.dumps(data,separators=(',',':')).encode() if data is not None else None
    req=urllib.request.Request(url,data=payload,method=method,headers={
        'Authorization':'Bearer '+token,
        'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28',
        'User-Agent':'saddlebench-prototype-publisher',
        'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=60) as r:
            out=r.read();return json.loads(out) if out else {}
    except urllib.error.HTTPError as e:
        body=e.read(3000).decode('utf8','replace')
        raise RuntimeError(f'GITHUB_API_HTTP_{e.code}: {body}') from None

def publish(files):
    token=credential()
    me=request(token,'GET','/user')
    if me.get('login')!='NathanNguyen67':raise RuntimeError('CONNECTED_GITHUB_USER_MISMATCH')
    try:
        repo=request(token,'GET','/repos/'+REPO)
    except RuntimeError as e:
        if not str(e).startswith('GITHUB_API_HTTP_404:'):raise
        repo=None
    if repo is not None:raise RuntimeError('TARGET_GITHUB_REPOSITORY_ALREADY_EXISTS: refuse overwrite')
    repo=request(token,'POST','/user/repos',{'name':'saddlebench','description':'Open-source SaddleBench reaction coverage and diagnostic scorer (reference data private)',
        'private':False,'auto_init':True,'has_wiki':False})
    if repo.get('private') is not False or repo.get('full_name')!=REPO:
        raise RuntimeError('CREATED_REPOSITORY_VISIBILITY_OR_OWNER_MISMATCH')
    branch=repo.get('default_branch') or 'main'
    ref=request(token,'GET',f'/repos/{REPO}/git/ref/heads/{branch}')
    head=ref['object']['sha']
    base=request(token,'GET',f'/repos/{REPO}/git/commits/{head}')
    elements=[{'path':n,'mode':'100644','type':'blob','content':s} for n,s in sorted(files.items())]
    tree=request(token,'POST',f'/repos/{REPO}/git/trees',{'base_tree':base['tree']['sha'],'tree':elements})
    commit=request(token,'POST',f'/repos/{REPO}/git/commits',{'message':'Publish SaddleBench prototype scorer RC10 (source only)',
        'tree':tree['sha'],'parents':[head]})
    request(token,'PATCH',f'/repos/{REPO}/git/refs/heads/{branch}',{'sha':commit['sha'],'force':False})
    result=request(token,'GET','/repos/'+REPO)
    if result.get('private') is not False:raise RuntimeError('PUBLIC_VISIBILITY_CHECK_FAILED')
    paths=request(token,'GET',f'/repos/{REPO}/git/trees/{tree["sha"]}?recursive=1')
    remote={x['path'] for x in paths.get('tree',[]) if x.get('type')=='blob'}
    if not set(files).issubset(remote):raise RuntimeError('GITHUB_SOURCE_UPLOAD_INCOMPLETE')
    print('GITHUB_PUBLIC_SOURCE_PUBLISH=PASS')
    print('SOURCE_FILES='+str(len(files)))
    print('COMMIT='+commit['sha'])
    print('URL=https://github.com/'+REPO)

def main():
    arg=argparse.ArgumentParser()
    arg.add_argument('--source',default=str(Path(__file__).resolve().parents[1]))
    arg.add_argument('--confirm-create-public-repo',action='store_true')
    a=arg.parse_args()
    files=public_files(a.source)
    print('PUBLIC_SOURCE_AUDIT=PASS')
    print('PUBLIC_SOURCE_FILES='+str(len(files)))
    print('PUBLIC_SOURCE_BYTES='+str(sum(len(x.encode()) for x in files.values())))
    print('TARGET_REPOSITORY=https://github.com/'+REPO)
    if a.confirm_create_public_repo:publish(files)
    else:print('DRY_RUN=PASS; rerun with --confirm-create-public-repo to create PUBLIC GitHub repository')

if __name__=='__main__':main()
