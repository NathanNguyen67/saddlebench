#!/usr/bin/env python3
"""Copy the SaddleMotif Explorer v2 viewer into saddlebench/explorer/vendor and record its provenance.

usage: python scripts/vendor_explorer.py /path/to/SaddleMotif [--check]

The vendored files are never edited here; change them in SaddleMotif (explorer_v2/web, explorer_v2/build) and re-run
this script. --check verifies that the vendored copy still matches VENDOR.json (used by the tests)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "saddlebench" / "explorer" / "vendor"
FILES = ["web/template.html", "web/styles.css", "web/geometry.js", "web/store.js", "web/renderer.js", "web/viewer.js",
         "web/ui.js", "web/inspector.js", "web/gallery.js", "web/workspace.js", "web/app.js", "web/index.js",
         "build/build_html.py", "build/build_index.py"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("saddlemotif", type=Path, nargs="?")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        record = json.loads((VENDOR / "VENDOR.json").read_text())
        bad = [name for name, sha in record["files"].items() if digest(VENDOR / name) != sha]
        raise SystemExit(f"vendored explorer files changed: {bad}" if bad else 0)
    source = a.saddlemotif / "explorer_v2"
    commit = subprocess.run(["git", "-C", str(a.saddlemotif), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(a.saddlemotif), "status", "--porcelain", "--", "explorer_v2"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit("commit the SaddleMotif explorer changes before vendoring")
    files = {}
    for name in FILES:
        (VENDOR / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, VENDOR / name)
        files[name] = digest(VENDOR / name)
    (VENDOR / "VENDOR.json").write_text(json.dumps({
        "source": "https://github.com/NathanNguyen67/SaddleMotif/tree/" + commit + "/explorer_v2",
        "commit": commit, "schema": "saddlemotif-explorer-v2", "files": files}, indent=1) + "\n")
    print(f"vendored {len(files)} files from SaddleMotif {commit[:7]}")


if __name__ == "__main__":
    main()
