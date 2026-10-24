#!/usr/bin/env python3
"""O(1) read-only archive metadata check; never an integrity certification.

This standalone script imports only the standard library and does not open
producer registries, source trajectories, or full scientific data tables.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3


def peek(path: Path) -> dict:
    path = Path(path).absolute()
    if not path.is_file() or path.is_symlink():
        raise ValueError('ARCHIVE_MISSING_OR_SYMLINK')
    # query_only plus mode=ro mean no SQLite writes or automatic creation.
    con = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON')
        con.execute('PRAGMA busy_timeout=3000')
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            raise RuntimeError('ARCHIVE_META_TABLE_MISSING')
        keys = ('schema_version','completed','expected_counts','source_registry_signature')
        rows = con.execute('SELECT key,value FROM meta WHERE key IN (?,?,?,?)', keys).fetchall()
        values = {row['key']: row['value'] for row in rows}
        if any(key not in values for key in keys):
            raise RuntimeError('ARCHIVE_META_KEYS_INCOMPLETE')
        sig = json.loads(values['source_registry_signature'])
        return {
            'archive': str(path),
            'schema_version': values['schema_version'],
            'completed_marker': values['completed'],
            'expected_counts': json.loads(values['expected_counts']),
            'pinned_registry_path': sig.get('registry_path'),
            'pinned_registry_identity': sig.get('identity'),
            'check_kind': 'READ_ONLY_METADATA_ONLY',
            'full_integrity_verified': False,
            'actual_record_counts_verified': False,
        }
    finally:
        con.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive', type=Path)
    args = p.parse_args()
    print('ARCHIVE_METADATA='+json.dumps(peek(args.archive), sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
