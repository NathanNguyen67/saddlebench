#!/usr/bin/env python3
"""Versioned, non-promoting Hugging Face candidate upload; dry-run by default."""
import argparse
import json
from pathlib import Path
from enrich_scientific import check_evidence, hash_file


def inspect(evidence, science):
    base=check_evidence(evidence)
    sm=json.loads((science/'manifest_science.json').read_text())
    if sm['source_registry_sha256']!=base['registry']['sha256']:
        raise ValueError('SCIENCE_REGISTRY_MISMATCH')
    if sm['source_evidence_manifest_sha256'] != hash_file(evidence/'manifest.json'):
        raise ValueError('SCIENCE_BASE_MANIFEST_MISMATCH')
    for name,digest in sm['sha256'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('UNSAFE_MANIFEST_PATH')
        if hash_file(science/name)!=digest:
            raise ValueError('SCIENCE_FILE_SHA256_MISMATCH:'+name)
    return base,sm


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--science',type=Path,required=True)
    p.add_argument('--repo',default='traingletrain/saddlebench-reference-private')
    p.add_argument('--upload-candidate',action='store_true')
    args=p.parse_args()
    base,science=inspect(args.evidence,args.science)
    sh=base['registry']['sha256']
    prefix=f'candidates/registry_{sh[:16]}'
    print('BASE_SCHEMA='+str(base['schema_version']))
    print('SNAPSHOT_SHA256='+sh)
    print('PUBLISHED_THROUGH='+str(base['registry']['published_through']))
    print('CANDIDATE_PREFIX='+prefix)
    print('TRIPLETS='+str(science['row_counts']['reaction_triplets']))
    print('RECORDED_MODES='+str(science['mode_status_counts'].get('RECORDED_MODE_UNQUALIFIED',0)))
    print('SCIENTIFIC_QUALIFICATION=NOT_ESTABLISHED')
    if not args.upload_candidate:
        print('UPLOAD_STATUS=DRY_RUN_ONLY')
        return
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise SystemExit('HUGGINGFACE_HUB_NOT_INSTALLED_IN_SELECTED_PYTHON') from exc
    api=HfApi()
    # Fail closed: never create a different repo by typo, and never promote latest.
    repo=api.repo_info(args.repo,repo_type='dataset')
    if not repo.private:
        raise ValueError('REFUSE_UPLOAD_TO_PUBLIC_DATASET')
    files=api.list_repo_files(args.repo,repo_type='dataset')
    if any(s.startswith(prefix+'/') for s in files):
        raise ValueError('IMMUTABLE_CANDIDATE_PREFIX_ALREADY_EXISTS')
    api.upload_folder(folder_path=str(args.evidence),path_in_repo=prefix+'/evidence',
                      repo_id=args.repo,repo_type='dataset',commit_message='Stage pinned registry evidence '+sh[:16])
    api.upload_folder(folder_path=str(args.science),path_in_repo=prefix+'/scientific_data',
                      repo_id=args.repo,repo_type='dataset',commit_message='Stage triplets and recorded modes '+sh[:16])
    print('CANDIDATE_UPLOAD=PASS')
    print('ACTIVE_SCORING_REFERENCE=UNCHANGED')


if __name__=='__main__':
    main()
