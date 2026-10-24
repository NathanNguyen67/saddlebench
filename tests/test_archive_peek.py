"""Fast metadata introspection must never do a database-wide integrity scan."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from tools.reaction_archive.peek_archive import peek


def test_peek_readonly_metadata_only(tmp_path):
    p = tmp_path/'archive.sqlite'
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE observations (id INTEGER PRIMARY KEY, data BLOB)')
        db.executemany('INSERT INTO meta VALUES (?,?)', [
            ('schema_version', 'saddlebench-reaction-archive-v1'),
            ('completed', 'NO'),
            ('expected_counts', json.dumps({'saddles':217716,'triplets':202884})),
            ('source_registry_signature', json.dumps({'identity':'abc','registry_path':'/immutable/file.sqlite'})),
        ])
        db.execute('INSERT INTO observations VALUES (1,?)', (b'preserve me',))
    before = p.read_bytes()
    report = peek(p)
    assert report['completed_marker'] == 'NO'
    assert report['expected_counts']['triplets'] == 202884
    assert report['pinned_registry_identity'] == 'abc'
    assert report['full_integrity_verified'] is False
    assert report['actual_record_counts_verified'] is False
    assert p.read_bytes() == before
    script = Path(__file__).resolve().parents[1]/'tools/reaction_archive/peek_archive.py'
    result = subprocess.run([sys.executable,str(script),str(p)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout.split('=',1)[1]) == report


def test_peek_missing_or_symlink_does_not_create_archive(tmp_path):
    p = tmp_path/'missing.sqlite'
    with pytest.raises(ValueError,match='ARCHIVE_MISSING_OR_SYMLINK'):
        peek(p)
    assert not p.exists()
    p.write_bytes(b'not a database')
    link = tmp_path/'link.sqlite'
    link.symlink_to(p)
    with pytest.raises(ValueError,match='ARCHIVE_MISSING_OR_SYMLINK'):
        peek(link)
