"""Resolve historical compatible public cases without relaxing the reference contract.

Only consulted if the current public cases.csv disagrees with the pinned
published private reference. Check full official verify_reference before use.
"""
from __future__ import annotations
import csv
import os
from pathlib import Path


def _compatible_cases(path, registry_sha):
    try:
        with Path(path).open(newline='', encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        return (len(rows)==123 and all(row.get('registry_sha256')==registry_sha for row in rows))
    except (OSError, ValueError, UnicodeError, TypeError):
        return False


def resolve_cases(private, first_casefile, cache, verify):
    """Return a strictly verified compatible cases.csv; never accept mismatches."""
    from saddlebench.score_modes import REGISTRY_SHA
    from huggingface_hub import HfApi, hf_hub_download
    try:
        verify(private, first_casefile)
        return Path(first_casefile)
    except RuntimeError as exc:
        if str(exc) != 'PUBLIC_INPUTS_SNAPSHOT_MISMATCH':
            raise
    repo='traingletrain/saddlebench-prototype'
    bases=[Path(cache), Path.home()/'.cache/huggingface/hub']
    if os.environ.get('HF_HUB_CACHE'):
        bases.append(Path(os.environ['HF_HUB_CACHE']).expanduser())
    if os.environ.get('HF_HOME'):
        bases.append(Path(os.environ['HF_HOME']).expanduser()/'hub')
    seen=set()
    for base in bases:
        snapshots=base/('datasets--'+repo.replace('/','--'))/'snapshots'
        if snapshots.is_dir():
            for p in sorted(snapshots.glob('*/cases.csv')):
                if str(p) in seen:continue
                seen.add(str(p))
                if _compatible_cases(p,REGISTRY_SHA):
                    verify(private,p)
                    print('REFERENCE_CASES_COMPATIBLE_CACHE='+str(p),flush=True)
                    return p
    print('REFERENCE_CASES_HISTORICAL_LOOKUP=START',flush=True)
    api=HfApi()
    commits=api.list_repo_commits(repo_id=repo,repo_type='dataset',revision='main',token=False)
    for revision in commits:
        rev=revision.commit_id
        try:
            candidate=Path(hf_hub_download(repo_id=repo,repo_type='dataset',filename='cases.csv',
                revision=rev,cache_dir=str(cache),token=False))
        except Exception:
            continue
        if _compatible_cases(candidate,REGISTRY_SHA):
            verify(private,candidate)
            print('REFERENCE_CASES_HISTORICAL_REVISION='+rev,flush=True)
            return candidate
    raise RuntimeError('NO_VERIFIED_PUBLIC_CASES_MATCH_PINNED_REFERENCE')
