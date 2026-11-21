#!/usr/bin/env python3
"""Inline the web app and one gzipped payload into a single offline HTML file.

usage: build_html.py PAYLOAD.json.gz OUT.html [--index HREF] [--title TEXT] [--mode motif|bench] [--config JSON]
  --index HREF   show a "← All structures" link to the structure index (e.g. index.html)
  --title TEXT   page <title> (default "SaddleMotif Explorer")
  --mode         motif (default): SaddleMotif debugging view with motion groups; bench: the SaddleBench view
  --config JSON  extra page options (lit, score, title, links); see web/ui.js SM.config

Page contract (SCHEMA in build_structure.py, CONFIG_KEYS below) is what SaddleBench vendors; keep both additive."""
import argparse
import base64
import html
import json
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
SCRIPTS = ["geometry.js", "store.js", "renderer.js", "viewer.js", "ui.js", "inspector.js", "gallery.js", "workspace.js", "app.js"]
TITLE = "<title>SaddleMotif Explorer</title>"
BRANDS = {"motif": "SaddleMotif Explorer", "bench": "SaddleBench Explorer"}
CONFIG_KEYS = {"mode", "brand", "title", "lit", "score", "links"}


def scripts() -> str:
    text = "\n".join((WEB / name).read_text(encoding="utf-8") for name in SCRIPTS)
    if "</script" in text:
        raise SystemExit("a script contains a closing script tag")
    return text


def json_for_script(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


def render_bytes(payload: bytes, index_href: str | None = None, title: str | None = None, mode: str = "motif",
                 config: dict | None = None, *, web_scripts: str | None = None, styles: str | None = None) -> str:
    """payload is the gzipped JSON payload. web_scripts/styles let callers reuse one read for many pages."""
    config = dict(config or {})
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise SystemExit(f"unknown page config keys: {sorted(unknown)}")
    config.setdefault("mode", mode)
    config.setdefault("brand", BRANDS.get(config["mode"], BRANDS["motif"]))
    links = dict(config.get("links") or {})
    if index_href:
        links["index"] = index_href
    config["links"] = links
    page = (WEB / "template.html").read_text(encoding="utf-8")
    for marker, value in (
        ("/*__STYLES__*/", styles if styles is not None else (WEB / "styles.css").read_text(encoding="utf-8")),
        ("/*__SCRIPTS__*/", web_scripts if web_scripts is not None else scripts()),
        ("__CONFIG__", json_for_script(config)),
        ("__PAYLOAD__", base64.b64encode(payload).decode("ascii")),
    ):
        if page.count(marker) != 1:
            raise SystemExit(f"template marker missing: {marker}")
        page = page.replace(marker, value)
    if title:
        page = page.replace(TITLE, f"<title>{html.escape(title)}</title>", 1)
    return page


def render(payload: Path, index_href: str | None = None, title: str | None = None, mode: str = "motif",
           config: dict | None = None) -> str:
    return render_bytes(payload.read_bytes(), index_href, title, mode, config)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--index")
    ap.add_argument("--title")
    ap.add_argument("--mode", choices=sorted(BRANDS), default="motif")
    ap.add_argument("--config", type=Path)
    a = ap.parse_args()
    config = json.loads(a.config.read_text()) if a.config else None
    page = render(a.payload, a.index, a.title, a.mode, config)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")
    print("WROTE", a.out, a.out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
