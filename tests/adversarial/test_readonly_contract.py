from pathlib import Path
import ast

def test_scoring_has_no_producer_write_sql():
    root=Path(__file__).resolve().parents[2]
    package=root/'saddlebench'
    text='\n'.join(p.read_text() for p in package.rglob('*.py'))
    for token in ('sbatch','srun ','subprocess.run','sqlite3.connect','import torch','import fairchem'):
        assert token not in text

def test_reference_export_has_queryonly_immutable():
    root=Path(__file__).resolve().parents[2]
    raw=(root/'scripts/export_reference.py').read_text()
    assert 'mode=ro&immutable=1' in raw and 'PRAGMA query_only=ON' in raw
