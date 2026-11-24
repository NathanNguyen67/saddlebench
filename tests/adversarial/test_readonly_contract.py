from pathlib import Path
import ast
import re

def test_scoring_has_no_producer_write_sql():
    root=Path(__file__).resolve().parents[2]
    package=root/'saddlebench'
    # coverage_image.py may shell out only to convert its own SVG to PNG (rsvg-convert / ImageMagick).
    image_ok={package/'coverage_image.py'}
    for path in package.rglob('*.py'):
        text=path.read_text()
        for token in ('sbatch','srun ','subprocess.run','sqlite3.connect','import torch','import fairchem'):
            if token=='subprocess.run' and path in image_ok:
                assert all(cmd in ('rsvg-convert','convert') for cmd in re.findall(r"subprocess\.run\(\['([^']+)'",text)),path
                continue
            assert token not in text,(token,path)

def test_reference_export_has_queryonly_immutable():
    root=Path(__file__).resolve().parents[2]
    raw=(root/'scripts/export_reference.py').read_text()
    assert 'mode=ro&immutable=1' in raw and 'PRAGMA query_only=ON' in raw
