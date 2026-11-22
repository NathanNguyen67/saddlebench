#!/usr/bin/env python3
"""Build a multi-structure explorer: one page per structure payload plus a visual index of every structure.

usage: build_index.py PAYLOAD_DIR OUT_DIR [--confidence confidence_latest.csv] [--lit lit.json] [--mode motif|bench] [--fragment]
  PAYLOAD_DIR  *.json.gz from build_structure.py (one per structure)
  OUT_DIR      gets index.html and <slug>.html per structure (flat, so links are plain relative names)
  --lit        parent -> literature notes (citation links, intended reaction); see SM.literature in web/ui.js
  --fragment   write index.html without <!doctype>/<head>/<body> (for hosts that add their own skeleton)
Standard library only (no numpy): runs anywhere. build_site() is the library entry point used by SaddleBench."""
import argparse
import array
import base64
import csv
import gzip
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_html  # noqa: E402

GLOSSARY = ("Saddle: a distinct saddle geometry (0.2 Å group) with a complete DoubleMin. "
            "Reaction: one saddle with the two minima its DoubleMin reached. "
            "Seed reaction: a distinct pair of end minima that includes the starting minimum, barrier below 1 eV. "
            "Reference saddle: a seed-reaction saddle that SaddleBench scores (aligned slides excluded). "
            "Class: SaddleMotif's grouping of saddles by motion.")


def slug(parent: str, seed: str | None = None) -> str:
    base = re.sub(r"[^A-Za-z0-9_.]+", "-", parent).strip("-")
    return f"{base}-seed-{seed[4:10]}" if seed else base


def seed_label(structure: dict) -> str:
    """'' for single-seed pages, else ' · seed 2 (23370d)'."""
    seed = structure.get("seedPage")
    if not seed:
        return ""
    order = structure.get("allSeeds") or [seed]
    return f" · seed {order.index(seed) + 1} ({seed[4:10]})"


def short_name(parent: str) -> str:
    name = parent.split(":", 1)[-1]
    return name if len(name) <= 28 else name[:26] + "…"


def _array(kind: str, text: str) -> array.array:
    out = array.array(kind)
    out.frombytes(base64.b64decode(text))
    if sys.byteorder != "little":
        out.byteswap()
    return out


def _encode(values: array.array) -> str:
    if sys.byteorder != "little":
        values = array.array(values.typecode, values)
        values.byteswap()
    return base64.b64encode(values.tobytes()).decode("ascii")


def representative(d: dict):
    """Largest real class's representative reaction, else the lowest-barrier seed reaction, else reaction 0."""
    classes = sorted((c for c in d["classes"] if c["id"] != "Unclassified"), key=lambda c: -len(c["reactions"]))
    if classes:
        return classes[0]["representative"]
    connected = [r for r in d["reactions"] if r["seedConnected"]]
    if connected:
        return min(connected, key=lambda r: r["barrier"])["id"]
    return 0 if d["reactions"] else None


def mini_payload(d: dict, reaction):
    """A one-reaction payload (same schema) for the index card animation."""
    s = d["structure"]
    if reaction is None:
        frames, values, slots = array.array("H"), array.array("h"), [[0, 0]] * 3
    else:
        index = _array("H", d["buffers"]["frameIndex"])
        raw = _array("h", d["buffers"]["frameValues"])
        frames, values, slots = array.array("H"), array.array("h"), []
        for start, count in d["reactions"][reaction]["frames"]:
            slots.append([len(frames), count])
            frames.extend(index[start:start + count])
            values.extend(raw[3 * start:3 * (start + count)])
    return {
        "schema": d["schema"], "structure": s, "provenance": d["provenance"], "thresholds": d["thresholds"],
        "encoding": d["encoding"], "classes": [],
        "reactions": [{"id": 0, "frames": slots, "mode": None, "saddle": "", "motion": {"groups": []}}],
        "buffers": {"frameIndex": _encode(frames), "frameValues": _encode(values), "modeIndex": "", "modeValues": ""},
    }


def summary(d: dict, confidence: dict, lit: dict | None, score: dict | None) -> dict:
    s, classes, reactions = d["structure"], d["classes"], d["reactions"]
    parent = s["parent"]
    seeds = set(s["seeds"])
    seed_pairs = {tuple(sorted((r["reactant"], r["product"]))) for r in reactions if r["seedConnected"] and r["barrier"] < 1.0}
    connected = [r for r in reactions if r["seedConnected"]]
    conf = confidence.get(parent, {})
    counts = {}
    for n in s["numbers"]:
        counts[n] = counts.get(n, 0) + 1
    row = {
        "parent": parent, "slug": slug(parent, s.get("seedPage")), "dataset": parent.split(":", 1)[0],
        "name": parent.split(":", 1)[-1] + seed_label(s),
        "short": ((lit or {}).get("short") or short_name(parent)) + seed_label(s), "atoms": len(s["numbers"]),
        "free": len(s["numbers"]) - len(s["fixed"]),
        "classes": sum(c["id"] != "Unclassified" for c in classes), "reactions": len(reactions),
        "saddles": len({r["saddle"] for r in reactions}), "seedReactions": len(seed_pairs),
        "lowest": round(min(r["barrier"] for r in connected), 3) if connected else None,
        "mechanism": (lit or {}).get("mechanism"), "formula": formula(s["numbers"]),
        "state": conf.get("state") or None,
        "tail": float(conf["tail_mean"]) if conf.get("tail_mean") not in (None, "") else None,
        "actual": int(float(conf["actual_random_tail"])) if conf.get("actual_random_tail") not in (None, "") else None,
        "mini": mini_payload(d, representative(d)),
        "radius": 9,
    }
    del seeds
    if score:
        found = {}
        ref = set(score["reference"])
        for method, cutoffs in score["found"].items():
            found[method] = {cutoff: len(ref.intersection(ids)) for cutoff, ids in cutoffs.items()}
        row["score"] = {"reference": len(ref), "found": found}
    return row


SYMBOLS = ("X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr "
           "Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt "
           "Au Hg Tl Pb Bi").split()


def formula(numbers) -> str:
    counts = {}
    for n in numbers:
        counts[n] = counts.get(n, 0) + 1
    order = sorted(counts, key=lambda n: (n not in (6, 1), SYMBOLS[n] if n < len(SYMBOLS) else str(n)))
    return "".join(f"{SYMBOLS[n] if n < len(SYMBOLS) else n}{counts[n] if counts[n] > 1 else ''}" for n in order)


INDEX_STYLE = """
.structure-grid { grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
.structure-card { color: inherit; text-decoration: none; }
.card-mech { padding: 0 11px; font-size: 13px; line-height: 1.35; }
.card-scoreline { display: flex; align-items: center; gap: 6px; }
.meter { flex: 0 0 64px; height: 6px; overflow: hidden; background: var(--miss-soft); border-radius: 3px; }
.meter i { display: block; height: 100%; background: var(--hit); }
.missed-count { color: var(--miss); font-weight: 600; }
.view-toggle { width: 150px; margin-left: auto; }
.box { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { padding: 8px 10px; text-align: right; border-bottom: 1px solid var(--line); white-space: nowrap; font-variant-numeric: tabular-nums; }
th.l, td.l { text-align: left; }
th { position: sticky; top: 0; background: var(--panel); font-size: 12px; color: var(--muted); font-weight: 600; }
th button { all: unset; cursor: pointer; }
th button[data-dir]::after { content: attr(data-dir); margin-left: 4px; color: var(--accent); }
tbody tr:hover { background: var(--soft); }
td a { color: var(--ink); font-weight: 600; text-decoration: none; border-bottom: 1px solid var(--line); }
.ds { display: block; max-width: 34ch; overflow: hidden; text-overflow: ellipsis; font-size: 11.5px; color: var(--muted); font-weight: 400; }
td.met { color: var(--hit); font-weight: 600; }
.glossary { margin-top: 14px; font-size: 12.5px; max-width: 110ch; }
.lede { max-width: 80ch; }
"""


def index_page(rows: list, *, mode: str, title: str, lede: str, methods: list, note: str, fragment: bool,
               links: dict | None = None) -> str:
    web = build_html.WEB
    config = {"mode": mode, "brand": build_html.BRANDS[mode], "links": links or {}}
    scripts = "\n".join((web / name).read_text(encoding="utf-8") for name in
                        ["geometry.js", "store.js", "renderer.js", "viewer.js", "ui.js", "index.js"])
    if "</script" in scripts:
        raise SystemExit("a script contains a closing script tag")
    data = {"rows": rows, "methods": methods, "glossary": GLOSSARY}
    back = ""
    if (links or {}).get("scoreboard"):
        back = f'<a class="brand-back" href="{html.escape(links["scoreboard"], quote=True)}">← Scoreboard</a> '
    head = (f"<title>{html.escape(title)}</title>\n"
            f"<style>{(web / 'styles.css').read_text(encoding='utf-8')}{INDEX_STYLE}</style>\n")
    body = f"""<header class="top"><div class="top-main">
<p class="brand">{back}<span class="brand-name">{html.escape(config["brand"])}</span></p>
<h1>{html.escape(title)}</h1>
<p class="muted lede">{html.escape(lede)}</p>
</div></header>
<main>{f'<p class="note" style="margin-bottom:12px">{html.escape(note)}</p>' if note else ''}<div id="index-root"></div></main>
<script id="page-config" type="application/json">{build_html.json_for_script(config)}</script>
<script id="index-data" type="application/json">{build_html.json_for_script(data)}</script>
<script>{scripts}</script>"""
    if fragment:
        return head + body
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'{head}</head>\n<body>\n{body}\n</body>\n</html>\n')


def build_site(items, out: Path, *, mode: str = "motif", lits: dict | None = None, scores: dict | None = None,
               methods: list | None = None, confidence: dict | None = None, title: str | None = None,
               lede: str = "", note: str = "", links: dict | None = None, fragment: bool = False) -> list:
    """items: iterable of (gzipped payload bytes). Writes <slug>.html per structure and index.html; returns rows."""
    lits, scores, confidence = lits or {}, scores or {}, confidence or {}
    out.mkdir(parents=True, exist_ok=True)
    web_scripts = build_html.scripts()
    styles = (build_html.WEB / "styles.css").read_text(encoding="utf-8")
    rows = []
    for raw in items:
        d = json.loads(gzip.decompress(raw))
        parent = d["structure"]["parent"]
        lit, score = lits.get(parent), scores.get(parent)
        row = summary(d, confidence, lit, score)
        rows.append(row)
        config = {"lit": lit, "title": ((lit or {}).get("title") or parent) + seed_label(d["structure"])}
        if score:
            config["score"] = score
        if links and links.get("scoreboard"):
            config["links"] = {"scoreboard": links["scoreboard"]}
        page = build_html.render_bytes(raw, "index.html", f"{build_html.BRANDS[mode].split()[0]} · {row['short']}", mode,
                                       config, web_scripts=web_scripts, styles=styles)
        (out / f"{row['slug']}.html").write_text(page, encoding="utf-8")
    rows.sort(key=lambda r: r["parent"])
    (out / "index.html").write_text(index_page(
        rows, mode=mode, title=title or "Every structure", lede=lede, methods=methods or [], note=note,
        fragment=fragment, links=links), encoding="utf-8")
    return rows


def read_confidence(path: Path | None) -> dict:
    confidence = {}
    if path and path.exists():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                # Several seeds per parent (e.g. O06): keep the least-converged seed (lowest shuffled tail).
                old = confidence.get(row["parent_id"])
                if old is None or float(row.get("tail_mean") or 0) < float(old.get("tail_mean") or 0):
                    confidence[row["parent_id"]] = row
    return confidence


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payloads", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--confidence", type=Path)
    ap.add_argument("--lit", type=Path)
    ap.add_argument("--mode", choices=sorted(build_html.BRANDS), default="motif")
    ap.add_argument("--fragment", action="store_true")
    a = ap.parse_args()
    lits = json.loads(a.lit.read_text()) if a.lit else {}
    paths = sorted(a.payloads.glob("*.json.gz"))
    rows = build_site((p.read_bytes() for p in paths), a.out, mode=a.mode, lits=lits,
                      confidence=read_confidence(a.confidence), fragment=a.fragment,
                      title="UMA answer-key campaign: every structure",
                      lede="Each card animates a representative reaction on the structure's starting minimum. Open one to see its saddle classes, reactions and products.",
                      note="Saddles are not Hessian-certified and DoubleMin endpoints are unverified: on Al some stored minima were found to be first-order saddles (Nov 19). Tails count qualifying random outcomes; a seed is done when the actual or shuffled tail reaches 200 (green).")
    size = sum(p.stat().st_size for p in a.out.glob("*.html"))
    print(f"WROTE {len(rows)} structure pages + index.html to {a.out} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
