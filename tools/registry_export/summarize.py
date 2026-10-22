#!/usr/bin/env python3
"""Always package compact diagnostics, without copying potentially huge evidence JSONL."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import tarfile

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--exit-code',type=int,required=True)
    a=p.parse_args()
    manifest_file=a.out/'manifest.json'
    manifest=None
    if manifest_file.is_file():
        try: manifest=json.loads(manifest_file.read_text())
        except (OSError,ValueError): pass
    summary={
        'status':os.environ.get('MAIN_STATUS','BLOCKED'),
        'reason':os.environ.get('REASON','UNKNOWN'),
        'export_return_code':os.environ.get('EXPORT_RC','UNKNOWN'),
        'validation_return_code':os.environ.get('VALIDATION_RC','UNKNOWN'),
        'wrapper_exit_code':a.exit_code,
        'start_utc':os.environ.get('STARTED_UTC','UNKNOWN'),
        'end_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'registry_path':str(a.registry),
        'output_dir':str(a.out),
        'manifest_present':manifest is not None,
        'record_counts':manifest.get('record_counts') if manifest else None,
        'registry':manifest.get('registry') if manifest else None,
        'completeness':manifest.get('completeness') if manifest else None,
        'coverage':manifest.get('coverage') if manifest else None,
        'qualification':'UNQUALIFIED_DISCOVERY_ONLY_NOT_SCIENTIFIC_CERTIFICATION',
        'science_layer_manifest': (str(a.run_dir/'scientific_dataset_unqualified/manifest_science.json')
             if (a.run_dir/'scientific_dataset_unqualified/manifest_science.json').is_file() else None),
        'parallel_workers':os.environ.get('SBE_WORKERS','UNKNOWN'),
    }
    summary_file=a.run_dir/'compact_summary.json'
    summary_file.write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    timestamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    tarball=a.run_dir/f'sbe_export_report_{timestamp}.tar.gz'
    fnames=('compact_summary.json','constraint_audit.json','constraint_audit.log','source_at_submit.txt','export_stdout.log','export_stderr.log',
            'sha256_validation.log','manifest_validation.log','import_preflight.log',
            'science_stdout.log','science_stderr.log','science_hash_validation.log','science_validation.log')
    with tarfile.open(tarball,'w:gz') as tf:
        for filename in fnames:
            f=a.run_dir/filename
            if f.is_file(): tf.add(f,arcname=filename)
        if manifest_file.is_file(): tf.add(manifest_file,arcname='manifest.json')
        science_manifest=a.run_dir/'scientific_dataset_unqualified/manifest_science.json'
        if science_manifest.is_file():tf.add(science_manifest,arcname='manifest_science.json')
        # Avoid adding full source outputs: only report+manifest are for upload.
    digest=hashlib.sha256(tarball.read_bytes()).hexdigest()
    print('UPLOAD_THIS_FILE='+str(tarball))
    print('UPLOAD_SHA256='+digest)
    print('SCIENTIFIC_QUALIFICATION=NOT_ESTABLISHED')

if __name__=='__main__': main()
