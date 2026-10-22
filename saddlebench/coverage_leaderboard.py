#!/usr/bin/env python3
"""Presentation-only SaddleBench coverage-tier report renderer.

Consumes existing saddlebench-greedy-coverage-tiers-v1 JSON unchanged. No scorer
imports, reference access, scientific classification, or geometry evaluations.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import sys

SCHEMA = 'saddlebench-greedy-coverage-tiers-v1'
TIERS = ('0.20', '0.10', '0.05')


def dataset_for(row):
    sid = row.get('structure_id', '')
    if not isinstance(sid, str):
        return 'UNKNOWN'
    prefix, separator, suffix = sid.partition(':')
    return prefix.upper() if separator and prefix and suffix else 'UNKNOWN'


def _median(values):
    values = sorted(values)
    n = len(values)
    return ((values[(n - 1) // 2] + values[n // 2]) / 2) if n else None


def _aggregate(rows):
    """Presentation-side dataset aggregation, with exact reaction-level pooling."""
    output = {'cases': len(rows), 'case_ids': frozenset(x['case_id'] for x in rows),
              'time_s': sum(float(x['self_reported_elapsed_seconds']) for x in rows),
              'tiers': {}}
    for tier in TIERS:
        vals = [x['thresholds'][tier] for x in rows]
        k = sum(int(v['known_eligible_reactions_K']) for v in vals)
        u = sum(int(v['greedy_unique_reactions_U']) for v in vals)
        reaction_matches = [m for v in vals for m in v['matched_reactions']]
        if len(reaction_matches) != u:
            raise ValueError('INVALID_MATCHED_REACTION_COUNT')
        # Older reports do not contain individual matched-prediction errors;
        # never mislabel their first-per-reaction median as all-match median.
        available = [v.get('matched_prediction_maxdev_A') for v in vals]
        if all(isinstance(seq, list) for seq in available):
            distances = [float(d) for seq in available for d in seq]
            if len(distances) != sum(int(v['matched_predictions']) for v in vals):
                raise ValueError('MATCHED_PREDICTION_DISTANCES_COUNT_MISMATCH')
            median = _median(distances)
        else:
            median = None
        output['tiers'][tier] = {
            'u': u, 'k': k, 'cov': (u / k if k else None),
            'median': median,
            'best': _median([float(m['best_observed_greedy_hit_maxdev_A'])
                             for m in reaction_matches]),
        }
    return output


def build_boards(report):
    if report.get('schema_version') != SCHEMA:
        raise ValueError('UNSUPPORTED_REPORT_SCHEMA')
    entries = report.get('case_results')
    if not isinstance(entries, list):
        raise ValueError('CASE_RESULTS_MISSING')
    groups = defaultdict(lambda: defaultdict(list))
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError('INVALID_CASE_RESULT')
        model = row.get('model') or 'default'
        if not isinstance(model, str):
            raise ValueError('INVALID_MODEL_LABEL')
        groups[dataset_for(row)][model].append(row)
    boards = []
    for dataset, by_model in sorted(groups.items()):
        models = []
        for model, rows in sorted(by_model.items()):
            cases = [x['case_id'] for x in rows]
            if len(set(cases)) != len(cases):
                raise ValueError('DUPLICATE_MODEL_DATASET_CASE')
            models.append({'model': model, **_aggregate(rows)})
        equal_sets = len({m['case_ids'] for m in models}) <= 1
        if equal_sets:
            models.sort(key=lambda m: (
                -(m['tiers']['0.20']['cov'] if m['tiers']['0.20']['cov'] is not None else -1),
                m['tiers']['0.20']['median'] if m['tiers']['0.20']['median'] is not None else float('inf'),
                m['time_s'], m['model']))
        # Report only datasets represented in submitted cases. Never use 123
        # as a dataset-specific denominator.
        boards.append({'dataset': dataset,
                       'evaluated_cases': len(set(x['case_id'] for rows in by_model.values() for x in rows)),
                       'equal_sets': equal_sets,
                       'models': models})
    return boards


def grade(cov):
    """Purely visual traffic-light grading; does not affect scoring."""
    if cov is None:
        return 'muted'
    if cov >= .90:
        return 'good'
    if cov >= .70:
        return 'mid'
    return 'low'


def fmt_cov(cov):
    return '-' if cov is None else f'{cov * 100:.1f}%'


def fmt_dev(value):
    return '-' if value is None else f'{value:.4f}'


def fmt_time(seconds):
    return f'{seconds:,.0f}s'


def _ansi(text, code, color):
    return f'\033[{code}m{text}\033[0m' if color else text


def _palette_grade(value, color):
    codes = {'good': '1;32', 'mid': '1;33', 'low': '1;31', 'muted': '2'}
    return _ansi(value, codes[color], color != 'none')


def terminal_view(report, use_color=None):
    """Terminal leaderboard grouped into separate *actual* dataset sections."""
    if use_color is None:
        use_color = bool(sys.stdout.isatty() and not os.environ.get('NO_COLOR') and os.environ.get('TERM') != 'dumb')
    sections = [_ansi('SADDLEBENCH', '1;37', use_color) + '  ·  Reference coverage & geometry',
                '']
    for board in build_boards(report):
        name = board['dataset']
        subtitle = (f"{board['evaluated_cases']} evaluated {'case' if board['evaluated_cases'] == 1 else 'cases'}  ·  "
                    f"{'same case set across models' if board['equal_sets'] else 'different case sets — not ranked'}")
        sections.extend([_ansi(f'  {name}', '1;37', use_color), f'  {subtitle}', ''])
        # Each column group has the same color in the header and explanatory key.
        family_line = (f"  {'':<19} {'':>5} {'':>8} "
                       + _ansi(f"{'REFERENCE COVERAGE (%)':^26}", '1;36', use_color)
                       + '  ' + _ansi(f"{'GEOMETRY (Å)':^20}", '1;35', use_color)
                       + '  ' + _ansi('RUNTIME', '1;34', use_color))
        headers = (f"  {'MODEL':<19} {'CASES':>5} {'FOUND':>8} "
                   + _ansi(f"{'≤ .20 Å':>8} {'≤ .10 Å':>8} {'≤ .05 Å':>8}", '1;36', use_color)
                   + '  ' + _ansi(f"{'MEDIAN':>9} {'BEST MED':>10}", '1;35', use_color)
                   + '  ' + _ansi(f"{'TIME':>9}", '1;34', use_color))
        sections.extend([family_line, headers, '  ' + '─' * 101])
        for m in board['models']:
            at = m['tiers']['0.20']
            prefix = f"  {m['model'][:19]:<19} {m['cases']:>5} {str(at['u'])+'/'+str(at['k']):>8} "
            covfields = ' '.join(
                _ansi(f"{fmt_cov(m['tiers'][t]['cov']):>8}",
                      {'good':'1;32','mid':'1;33','low':'1;31','muted':'2'}[grade(m['tiers'][t]['cov'])],
                      use_color)
                for t in TIERS)
            tail = (f"  {fmt_dev(at['median']):>9} {fmt_dev(at['best']):>10}"
                    f"  {fmt_time(m['time_s']):>9}")
            sections.append(prefix + covfields + tail)
        sections.extend(['', ''])
    sections.extend([
        _ansi('  READING THE LEADERBOARD', '1;37', use_color), '',
        '  ' + _ansi('COVERAGE', '1;36', use_color) + '   Unique known eligible reactions found / known eligible reactions.',
        '             Cutoffs are maxdev tolerances: 0.20, 0.10 and 0.05 Å.',
        '             ' + _ansi('Green ≥90%', '1;32', use_color) + '    ' + _ansi('Amber 70–<90%', '1;33', use_color) + '    ' + _ansi('Red <70%', '1;31', use_color) + '  (display colors only)',
        '',
        '  ' + _ansi('GEOMETRY', '1;35', use_color) + '   MEDIAN: all matched predictions, including repeats (Å).',
        "             BEST MED: median of each reaction's best observed match (Å).",
        '             Unmatched predictions excluded; nearest-reference search not performed.',
        '',
        '  ' + _ansi('RUNTIME', '1;34', use_color) + '    Submitted method time in seconds; evaluator time excluded.',
        ''])
    return '\n'.join(sections)


CSS = '''
:root{color-scheme:light;--ink:#1c2635;--muted:#67758a;--line:#dfe6ee;--bg:#f5f7fb;--white:#fff;--cyan:#187d98;--purple:#7756b5;--blue:#456ca9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1220px;margin:auto;padding:40px 24px 72px}
.top{margin:0 0 42px}.eyebrow{font-size:12px;letter-spacing:.18em;font-weight:750;color:var(--cyan)}h1{font-size:30px;letter-spacing:-.03em;margin:7px 0 4px}h2{font-size:22px;margin:0;letter-spacing:-.02em}p{margin:0;color:var(--muted)}
.board{background:var(--white);border:1px solid var(--line);border-radius:14px;box-shadow:0 5px 25px rgba(24,46,76,.04);margin:0 0 29px;overflow:hidden}
.board-head{padding:22px 26px 21px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.board-head p{font-size:13px;margin-top:5px}.notice{font-size:12px;padding:5px 10px;border:1px solid var(--line);border-radius:6px;color:var(--muted);background:#f9fbfd}
.table-scroller{overflow-x:auto}.results{border-collapse:collapse;width:100%;min-width:990px;font-size:13px;font-variant-numeric:tabular-nums}
.results th,.results td{padding:13px 13px;text-align:right;white-space:nowrap;border-bottom:1px solid #edf1f6}
.results tbody tr:last-child td{border-bottom:none}.results tbody tr:hover{background:#f8fbff}
.results th:first-child,.results td:first-child{text-align:left;padding-left:26px}
.results th:last-child,.results td:last-child{padding-right:26px}
.results .bands th{font-size:10px;letter-spacing:.11em;padding-top:14px;padding-bottom:3px;border-bottom:0}
.results .bands .cov{color:var(--cyan)}.results .bands .geom{color:var(--purple)}.results .bands .time{color:var(--blue)}
.results .heads th{font-size:11px;color:#69798a;padding-top:4px;padding-bottom:12px;font-weight:700}
.results tbody .model{font-weight:700;color:#26364b;font-size:14px}.results .denom{color:#394d62}
.results td.geometry{color:#614c85}.results td.time{color:#46648d}
.coverage{font-weight:750;display:inline-flex;gap:7px;align-items:center;justify-content:flex-end;min-width:87px}
.coverage .track{height:5px;width:30px;border-radius:4px;background:#ebeff2;overflow:hidden}
.coverage .fill{height:100%;display:block;background:currentColor;border-radius:4px}
.good{color:#17794c}.mid{color:#a2690c}.low{color:#c44c48}.muted{color:#8892a0}
.legend{background:#fbfcfe;border-top:1px solid var(--line);padding:22px 26px 25px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:26px}
.legend h3{margin:0 0 10px;font-size:12px;letter-spacing:.1em}.legend .lc{color:var(--cyan)}.legend .lg{color:var(--purple)}.legend .lt{color:var(--blue)}
.legend p{font-size:12px;line-height:1.8;color:#59687b}
.legend strong{color:#27394e}.legend .geometry-note{margin-top:9px}.colors{display:flex;gap:11px;flex-wrap:wrap;margin-top:13px;font-size:11px;font-weight:650}.dot{display:inline-block;border-radius:50%;width:8px;height:8px;background:currentColor;margin-right:5px}
.note{font-size:12px;margin:22px 4px 0;max-width:900px}
@media(max-width:760px){.wrap{padding:24px 12px 50px}h1{font-size:24px}.board-head{padding:18px}.legend{grid-template-columns:1fr;padding:20px;gap:18px}.results th:first-child,.results td:first-child{padding-left:18px}}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--ink:#e5eaf2;--muted:#acbad0;--line:#384256;--bg:#151b27;--white:#202938;--cyan:#69c7de;--purple:#c4a5f2;--blue:#a2bfff}.notice{background:#253040}.results th,.results td{border-bottom-color:#303c50}.results tbody tr:hover{background:#28364b}.results tbody .model,.legend strong{color:#eaf1fb}.results .heads th{color:#c0cce0}.results .denom{color:#cbd8ea}.results td.geometry{color:#d4b7ff}.results td.time{color:#b3cbff}.legend{background:#1c2433}.legend p{color:#c0ccde}.good{color:#69d6a0}.mid{color:#f3c16c}.low{color:#ff8b8d}.coverage .track{background:#3d4659}}
'''


def _coverage_cell(val):
    if val is None:
        return '<span class="coverage muted">—</span>'
    cls = grade(val)
    width = min(100., max(0., val * 100))
    return (f'<span class="coverage {cls}" title="{val*100:.3f}%">'
            f'<span class="track" aria-hidden="true"><span class="fill" style="width:{width:.3f}%"></span></span>'
            f'{val*100:.1f}%</span>')


def html_view(report):
    boards = build_boards(report)
    sections=[]
    for board in boards:
        dataset = html.escape(board['dataset'])
        common = board['equal_sets']
        note = ('Same evaluation cases across models' if common
                else 'Different case sets; ranking disabled')
        legacy = any(m['tiers']['0.20']['median'] is None for m in board['models'])
        rows=[]
        for model in board['models']:
            b=model['tiers']['0.20']
            name=html.escape(model['model'])
            cols=''.join(f'<td>{_coverage_cell(model["tiers"][t]["cov"])}</td>' for t in TIERS)
            rows.append(f'<tr><td class="model">{name}</td>'
                        f'<td>{model["cases"]}</td><td class="denom">{b["u"]}/{b["k"]}</td>'
                        + cols + f'<td class="geometry" title="Median maxdev over every matched prediction; older JSON does not contain these distances">{fmt_dev(b["median"])}</td>'
                        f'<td class="geometry">{fmt_dev(b["best"])}</td>'
                        f'<td class="time">{html.escape(fmt_time(model["time_s"]))}</td></tr>')
        if legacy:
            note += ' · all-match median requires re-scoring old JSON'
        sections.append(f'''<section class="board" aria-label="{dataset} leaderboard">
  <div class="board-head"><div><h2>{dataset}</h2><p>{board['evaluated_cases']} evaluated {'case' if board['evaluated_cases'] == 1 else 'cases'}</p></div>
    <span class="notice">{note}</span></div>
  <div class="table-scroller"><table class="results">
    <thead><tr class="bands"><th colspan="3"></th><th colspan="3" class="cov">REFERENCE COVERAGE</th><th colspan="2" class="geom">GEOMETRY · Å</th><th class="time">RUNTIME</th></tr>
    <tr class="heads"><th>Model</th><th>Cases</th><th>Found</th><th>≤ 0.20 Å</th><th>≤ 0.10 Å</th><th>≤ 0.05 Å</th><th>Median</th><th>Best med</th><th>Time</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>
  <div class="legend">
    <div><h3 class="lc">COVERAGE</h3><p><strong>Found</strong> is unique known eligible reactions matched / known eligible reference reactions. Three cutoffs indicate maxdev tolerance; percentages use those denominators.</p>
      <div class="colors"><span class="good"><span class="dot"></span>≥ 90%</span><span class="mid"><span class="dot"></span>70–&lt;90%</span><span class="low"><span class="dot"></span>&lt; 70%</span></div></div>
    <div><h3 class="lg">GEOMETRY · Å</h3><p><strong>Median:</strong> all matched predictions (repeats included). <strong>Best med:</strong> one best observed match per reaction, then the median. Both use the 0.20 Å cutoff.</p><p class="geometry-note">Unmatched predictions are excluded. No exhaustive nearest-reference search.</p></div>
    <div><h3 class="lt">RUNTIME</h3><p>Self-reported method execution time in seconds, summed over evaluated cases. Matching and leaderboard rendering time are not included.</p></div>
  </div></section>''')
    return ('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SaddleBench · Reference coverage</title><style>'''
            + CSS + '''</style></head><body><main class="wrap">
    <header class="top"><div class="eyebrow">SADDLEBENCH</div><h1>Reference coverage &amp; geometric accuracy</h1>
    <p>Separate dataset leaderboards · reference agreement</p></header>'''
            + (''.join(sections) if sections else '<p>No evaluated cases in this report.</p>')
            + '''<p class="note">These are coverage statistics over known eligible references, not proof that all physical reactions have been discovered. Color thresholds are display-only. Different dataset sections are not combined into one rank. For complete matching definitions, see SaddleBench's methodology README.</p>
    </main></body></html>''')


def write_new(path, contents):
    """No-overwrite output with exclusive create, leaving previous results intact."""
    with Path(path).open('x', encoding='utf-8') as f:
        f.write(contents)


def main(argv=None):
    p=argparse.ArgumentParser(description='Render separate SaddleBench dataset leaderboards from coverage-tier JSON')
    p.add_argument('report', help='Existing coverage-tiers JSON (read-only)')
    p.add_argument('--html-output', help='HTML destination; default is next to JSON with .html extension')
    p.add_argument('--no-html', action='store_true', help='Only print terminal leaderboard')
    p.add_argument('--no-color', action='store_true', help='Disable ANSI color for piped/terminal output')
    p.add_argument('--color', action='store_true', help='Force ANSI color (useful with less -R)')
    p.add_argument('--open', action='store_true', help='Request graphical HTML opening (requires X11/desktop browser)')
    args=p.parse_args(argv)
    if args.open and args.no_html:
        p.error('--open requires HTML output; cannot use --no-html')
    if args.no_color and args.color:
        p.error('--no-color and --color are mutually exclusive')
    source=Path(args.report).expanduser().absolute()
    report=json.loads(source.read_text(encoding='utf-8'))
    boards=build_boards(report)
    if not args.no_html:
        destination=Path(args.html_output).expanduser().absolute() if args.html_output else source.with_suffix('.html')
        if destination == source:
            raise SystemExit('REFUSE_TO_OVERWRITE_JSON')
        if destination.exists():
            raise SystemExit('HTML_OUTPUT_ALREADY_EXISTS: '+str(destination))
        write_new(destination, html_view(report))
    else:
        destination=None
    print(terminal_view(report, use_color=(args.color or (not args.no_color and sys.stdout.isatty() and not os.environ.get('NO_COLOR')))))
    print('LEADERBOARD_RENDER=PASS')
    if any(m['tiers']['0.20']['median'] is None for b in boards for m in b['models']):
        print('ALL_MATCH_MEDIAN=UNAVAILABLE_FOR_OLD_REPORT; rerun scorer for this metric')
    if destination:
        print('HTML_THIS='+str(destination))
        if args.open:
            from saddlebench.coverage_html_open import open_html
            status = open_html(destination)
            print('HTML_OPEN=' + status)
            if status.startswith(('SKIPPED_', 'FAILED_')):
                print('HTML_OPEN_HINT=No usable remote graphical HTML viewer; use open_in_windows.ps1 on Windows, or install/configure a remote X11 browser.')
    print('JSON_THIS='+str(source))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
