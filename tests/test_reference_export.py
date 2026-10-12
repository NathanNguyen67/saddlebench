import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from examples.generate_fixture import frames

def make_source(tmp_path):
    reg=tmp_path/'pinned.sqlite'
    con=sqlite3.connect(reg)
    con.execute('CREATE TABLE publication_marker (sequence INTEGER PRIMARY KEY, note TEXT)')
    con.execute('INSERT INTO publication_marker VALUES (0,"synthetic")')
    con.commit();con.close()
    evidence=tmp_path/'evidence.jsonl'
    records,_=frames()
    evidence.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in records))
    return reg,evidence,hashlib.sha256(reg.read_bytes()).hexdigest()

def run(reg,evidence,digest,tmp_path,extra=()):
    return subprocess.run([sys.executable,'scripts/export_reference.py','--pinned-registry',str(reg),
      '--expected-registry-sha256',digest,'--registry-watermark','0',
      '--verified-evidence-jsonl',str(evidence),'--evidence-qualification','synthetic-fixture',
      '--producer-source-id','synthetic','--reference-version','synthetic-v1','--out',str(tmp_path/'exported'),*extra],
      cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)

def test_readonly_export_and_checksum(tmp_path):
    reg,evidence,digest=make_source(tmp_path)
    before=reg.read_bytes()
    res=run(reg,evidence,digest,tmp_path)
    assert res.returncode==0,res.stderr
    assert reg.read_bytes()==before
    assert not list(tmp_path.glob('*.sqlite-*'))
    from saddlebench.reference.snapshot import load_reference
    snap=load_reference(tmp_path/'exported')
    assert len(snap.records)==12
    assert snap.manifest['registry_sha256']==digest
    assert snap.manifest['coverage']['eligible_denominator_complete'] is False

def test_fails_closed_if_pin_stale(tmp_path):
    reg,evidence,digest=make_source(tmp_path)
    res=run(reg,evidence,'0'*64,tmp_path)
    assert res.returncode!=0
    assert not (tmp_path/'exported').exists()
    assert hashlib.sha256(reg.read_bytes()).hexdigest()==digest

def test_symlink_pointer_rejected(tmp_path):
    reg,evidence,digest=make_source(tmp_path)
    alias=tmp_path/'latest.sqlite';alias.symlink_to(reg)
    res=run(alias,evidence,digest,tmp_path)
    assert res.returncode!=0 and 'SYMLINK' in res.stderr

def test_no_unverified_real_coverage(tmp_path):
    reg,evidence,digest=make_source(tmp_path)
    proc=run(reg,evidence,digest,tmp_path,['--coverage-complete'])
    assert proc.returncode==0
    # Synthetic fixtures can explicitly define known coverage.
    from saddlebench.reference.snapshot import load_reference
    assert load_reference(tmp_path/'exported').manifest['coverage']['eligible_denominator_complete']
