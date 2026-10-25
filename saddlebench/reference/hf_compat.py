"""Resolve historical compatible public cases without relaxing the reference contract.

Only consulted if the current public cases.csv disagrees with the pinned
published private reference. Check full official verify_reference before use.
"""
from __future__ import annotations
import csv
import os
from pathlib import Path


def _compatible_cases(path, registry_sha, expected_count=None):
    try:
        with Path(path).open(newline='', encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        return (bool(rows) and (expected_count is None or len(rows)==expected_count)
                and len({row.get('case_id') for row in rows})==len(rows)
                and all(row.get('registry_sha256')==registry_sha for row in rows))
    except (OSError, ValueError, UnicodeError, TypeError):
        return False


def resolve_cases(private, first_casefile, cache, verify):
    """Return a strictly verified compatible cases.csv; never accept mismatches."""
    try:
        verify(private, first_casefile)
        return Path(first_casefile)
    except RuntimeError as exc:
        if str(exc) != 'PUBLIC_INPUTS_SNAPSHOT_MISMATCH':
            raise
    from huggingface_hub import HfApi, hf_hub_download
    import json
    private=Path(private)
    metadata=json.loads((private/'private_manifest.json').read_text())
    registry=metadata['source_registry_sha256']
    expected_count=metadata['case_count']
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
                if _compatible_cases(p,registry,expected_count):
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
        if _compatible_cases(candidate,registry,expected_count):
            verify(private,candidate)
            print('REFERENCE_CASES_HISTORICAL_REVISION='+rev,flush=True)
            return candidate
    raise RuntimeError('NO_VERIFIED_PUBLIC_CASES_MATCH_PINNED_REFERENCE')


def download_default_reference(cache, no_progress=False):
    """Hugging Face adapter: all dataset names and remote resolution live here.

    This function is deliberately outside the core scoring/matching layer.
    The caller validates the chosen snapshot against its own manifest.
    """
    import json
    from huggingface_hub import snapshot_download,hf_hub_download
    cache=Path(cache)
    if not no_progress:print('REFERENCE_DOWNLOAD=START',flush=True)
    private=Path(snapshot_download(repo_id='traingletrain/saddlebench-reference-private',
        repo_type='dataset',cache_dir=str(cache),token=False,
        allow_patterns=['private_manifest.json','reference/*','diagnostic_saddles.jsonl.gz','CHECKSUMS.json']))
    cases=Path(hf_hub_download(repo_id='traingletrain/saddlebench-prototype',
        repo_type='dataset',filename='cases.csv',cache_dir=str(cache),token=False))
    return private,cases
