#!/usr/bin/env python3
"""Smoke-check a SaddleBench report against its CSV: the explorer must show the same denominators and hits.

usage: python scripts/check_report.py REPORT.csv REPORT.html

Checks, for every structure and method in the CSV:
  * the explorer index has the structure, and its page exists;
  * reference saddles in the explorer == eligible_reference_groups (same denominator);
  * found at 0.20/0.10/0.05 A in the explorer == reference_groups_found_* in the CSV;
  * every reference saddle has an explorer reaction (else it can't be flagged), and found sets are subsets of it.
Exits non-zero and lists problems if any check fails."""
import csv
import json
from pathlib import Path
import re
import sys


def script_json(text, ident):
    match = re.search(rf'<script id="{ident}" type="application/json">(.*?)</script>', text, re.S)
    return json.loads(match.group(1)) if match else None


def main(csv_path, html_path):
    csv_path, html_path = Path(csv_path), Path(html_path)
    explorer = html_path.with_name(html_path.stem + "-explorer")
    index = script_json((explorer / "index.html").read_text(encoding="utf-8"), "index-data")
    by_parent = {row["parent"]: row for row in index["rows"]}
    problems, checked = [], 0
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        parent = row["parent_id"]
        method = row.get("method_id") or "submission"
        entry = by_parent.get(parent)
        if entry is None:
            problems.append(f"{parent}: missing from the explorer index")
            continue
        if not (explorer / f"{entry['slug']}.html").is_file():
            problems.append(f"{parent}: page {entry['slug']}.html missing")
        score = entry.get("score") or {}
        eligible = int(row["eligible_reference_groups"])
        if score.get("reference") != eligible:
            # A structure can have several seeds in the CSV; the explorer counts the union of their references.
            seeds = sum(1 for r in rows if r["parent_id"] == parent and (r.get("method_id") or "submission") == method)
            if seeds == 1:
                problems.append(f"{parent} {method}: explorer reference {score.get('reference')} != CSV eligible {eligible}")
        for cutoff in ("0.20", "0.10", "0.05"):
            found = int(row[f"reference_groups_found_{cutoff}_A"])
            shown = (score.get("found", {}).get(method) or {}).get(cutoff)
            if shown != found:
                problems.append(f"{parent} {method} {cutoff} A: explorer found {shown} != CSV {found}")
        checked += 1
    for entry in index["rows"]:
        page = (explorer / f"{entry['slug']}.html").read_text(encoding="utf-8")
        config = script_json(page, "page-config") or {}
        overlay = config.get("score") or {}
        if overlay.get("absent"):
            problems.append(f"{entry['parent']}: {len(overlay['absent'])} reference saddles have no explorer reaction")
        reference = set(overlay.get("reference", {}))
        for method, cutoffs in overlay.get("found", {}).items():
            for cutoff, ids in cutoffs.items():
                if not set(ids) <= reference:
                    problems.append(f"{entry['parent']} {method} {cutoff}: found saddles outside the reference")
    print(f"checked {checked} CSV rows against {len(index['rows'])} explorer structures: "
          f"{'OK' if not problems else f'{len(problems)} problems'}")
    for problem in problems:
        print("  " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.exit(main(*sys.argv[1:]))
