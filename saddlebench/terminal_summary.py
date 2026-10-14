"""Human-readable leaderboard for SaddleBench quick-mode JSON reports.

Presentation only: uses each case's existing first-hit results and the
reference's published exploration-confidence heuristic. No rescoring.
"""
from collections import Counter, defaultdict
import csv
import math
import os
from pathlib import Path
import sys


def _case_metadata(cases_csv):
    if cases_csv is None or not Path(cases_csv).is_file():
        return {}
    with Path(cases_csv).open(newline='', encoding='utf8') as stream:
        return {row['case_id']: row for row in csv.DictReader(stream)}


def _numeric(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _confidence(case, metadata):
    meta = metadata.get(case.get('case_id'))
    if meta is None:
        return None
    for key in ('structure_id', 'starting_minimum_id'):
        if case.get(key) is not None and case[key] != meta.get(key):
            return None
    value = _numeric(meta.get('exploration_confidence_heuristic'))
    return value if value is not None and 0 <= value <= 1 else None


def _cells(case, mode, confidence):
    entries = case.get('entries')
    entries = entries if isinstance(entries, list) else None
    stages = Counter(e.get('stage') or 'unmatched' for e in (entries or []))
    categories = Counter(e.get('category') for e in (entries or []) if e.get('stage') not in (None, 'eligible'))
    eligible = stages['eligible']
    other = sum(n for stage, n in stages.items() if stage not in ('eligible', 'unmatched'))
    nf = categories['KNOWN_NON_FIRST_ORDER']
    hb = categories['KNOWN_CONNECTED_HIGH_BARRIER'] if mode == 'score_detailed' else None
    neutral = other - nf - (hb or 0) if mode == 'score_detailed' else None
    k = case.get('known_eligible_reactions_K')
    u = case.get('greedy_unique_reactions_U')
    cov = _numeric(case.get('coverage'))
    score = _numeric(case.get('provisional_score_per_hour'))
    secs = _numeric(case.get('self_reported_elapsed_seconds'))
    bad_groups = case.get('first_hit_potential_bad_groups_with_known_start') if mode != 'coverage' else None
    return {
        'model': str(case.get('model') or 'default'),
        'case': str(case.get('case_id') or '?'),
        'n': str(len(entries)) if entries is not None else '-',
        'uk': f'{u}/{k}' if u is not None and k is not None else '-',
        'cov': f'{100*cov:.1f}' if cov is not None else '-',
        'conf': f'{100*confidence:.1f}' if confidence is not None else '-',
        'hit': str(eligible + other) if entries is not None else '-',
        'dup': str(sum(e.get('status') == 'DUPLICATE' for e in entries)) if entries is not None else '-',
        'nf': str(nf) if entries is not None and mode != 'coverage' else '-',
        'hb': str(hb) if entries is not None and hb is not None else '-',
        'oth': str(neutral) if entries is not None and neutral is not None else '-',
        'unm': str(stages['unmatched']) if entries is not None else '-',
        'bad': str(bad_groups) if bad_groups is not None else '-',
        'time': str(round(secs)) if secs is not None else '-',
        'score': f'{score:.2f}' if score is not None else '-',
        '_sort_score': score,
        '_cov': cov,
        '_confidence': confidence,
        '_bad': bad_groups,
        '_nf': nf if mode != 'coverage' else None,
        '_hb': hb,
    }


def _clip(value, width):
    value = str(value)
    return value if len(value) <= width else value[:max(0, width-3)] + '...'


def _ansi(text, color, enable):
    return f'\x1b[{color}m{text}\x1b[0m' if enable and color else text


def render_summary(report, *, cases_csv=None, color=None):
    """Render a case-by-model table; never change numeric results or report."""
    if color is None:
        color = (sys.stdout.isatty() and not os.environ.get('NO_COLOR')
                 and os.environ.get('TERM', '') != 'dumb') or os.environ.get('SADDLEBENCH_COLOR') == 'always'
    if os.environ.get('SADDLEBENCH_COLOR') == 'never':
        color = False
    metadata = _case_metadata(cases_csv)
    mode = report.get('mode', 'unknown')
    cases = report.get('case_results')
    if cases is None:
        cases = [report]
    rows = [_cells(c, mode, _confidence(c, metadata)) for c in cases]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['case']].append(row)
    case_count = len(grouped)
    model_count = len({r['model'] for r in rows})
    lines = ['', '=' * 112,
             f' SADDLEBENCH | {mode} | {len(rows)} submissions | {case_count} case(s) | {model_count} model(s)',
             '=' * 112]

    # All percentages and colored cells are derived from existing JSON values.
    columns = [('#', 2, 'rank'), ('MODEL', 19, 'model'), ('CASE', 8, 'case'),
               ('N', 4, 'n'), ('U/K', 6, 'uk'), ('COV%', 6, 'cov'),
               ('CONF%', 6, 'conf'), ('HIT', 4, 'hit'), ('DUP', 4, 'dup'),
               ('NF', 3, 'nf'), ('HB', 3, 'hb'), ('OTH', 3, 'oth'),
               ('UNM', 3, 'unm'), ('B', 3, 'bad'), ('SEC', 5, 'time'),
               ('S/H', 7, 'score')]
    hdr = ' '.join(name.rjust(width) if key not in ('model','case') else name.ljust(width)
                   for name,width,key in columns)
    lines.append(_ansi(hdr, '1;36', color))
    lines.append('-' * len(hdr))
    for case_id in sorted(grouped):
        group = sorted(grouped[case_id], key=lambda r:
                       (r['_sort_score'] is None, -(r['_sort_score'] or 0), r['model']))
        for i, row in enumerate(group, 1):
            rendered=[]
            for name, width, key in columns:
                value = str(i) if key == 'rank' else row[key]
                value = _clip(value, width)
                cell = value.ljust(width) if key in ('model','case') else value.rjust(width)
                if key == 'cov' and row['_cov'] is not None:
                    # Color bands are strictly visual, not scientific thresholds.
                    cell = _ansi(cell, '32' if row['_cov'] >= .9 else '33' if row['_cov'] >= .5 else '31', color)
                elif key == 'conf' and row['_confidence'] is not None:
                    cell = _ansi(cell, '36', color)
                elif key in ('nf','hb','bad') and (row.get('_nf') if key=='nf' else row.get('_hb') if key=='hb' else row.get('_bad')):
                    cell = _ansi(cell, '31', color)
                elif key == 'score' and row['_sort_score'] is not None and i == 1 and len(group) > 1:
                    cell = _ansi(cell, '1;32', color)
                rendered.append(cell)
            lines.append(' '.join(rendered))
        if case_id != sorted(grouped)[-1]:
            lines.append('')

    # Per-model totals only when every model was scored on the same case set.
    model_aggregates = report.get('model_aggregates') or []
    case_sets = [frozenset(m.get('case_ids', [])) for m in model_aggregates]
    if len(model_aggregates) > 1 and case_sets and len(set(case_sets)) == 1:
        lines += ['', 'PER-MODEL TOTALS (identical selected cases)',
                  'MODEL               U/K     COV%  B    SEC    S/H',
                  '-' * 54]
        ordered = sorted(model_aggregates,
                         key=lambda m: -(m.get('aggregate', {}).get('provisional_score_per_hour') or -1))
        for m in ordered:
            lines.append(_aggregate_row(m, mode))
    elif len(model_aggregates) > 1:
        lines += ['', 'No aggregate ranking: the models were not submitted on identical case sets.']

    lines += ['',
        'LEGEND  N=predictions; U/K=unique eligible reactions/reference eligible reactions;',
        '        COV%=reaction coverage; CONF%=historical reference exploration heuristic;',
        '        HIT=predictions matched in checked stages (includes eligible duplicates);',
        '        DUP=duplicate eligible predictions; NF=known non-first-order first hits;',
        '        HB=known connected high-barrier first hits; OTH=other known saddle first hits;',
        '        UNM=no match in stages checked; B=distinct first-hit penalty groups;',
        '        SEC=total search seconds; S/H=provisional score/hour (coverage: unpenalized).',
        'NOTE    CONF% is NOT probability all saddles have been found. B is provisional,',
        '        not a certified bad-reaction count; unknown is not automatically bad.',
    ]
    if mode == 'coverage':
        lines.append('        Coverage mode only checks eligible reactions; NF/HB/OTH/B are not evaluated.')
    elif mode == 'score':
        lines.append('        Score mode does not check HB/OTH; UNM may match unsearched diagnostics.')
    if color:
        lines.append('        Colors: green/high coverage, yellow/medium, red/low or bad, cyan/confidence.')
    return '\n'.join(lines)


def _aggregate_row(model_item, mode):
    a = model_item['aggregate']
    u = a.get('greedy_unique_reactions_U')
    k = a.get('reference_known_K')
    cov = _numeric(a.get('micro_coverage'))
    sec = _numeric(a.get('total_self_reported_seconds'))
    score = _numeric(a.get('provisional_score_per_hour'))
    b = a.get('first_hit_potential_bad_groups_with_known_start') if mode != 'coverage' else None
    return (f"{_clip(model_item['model'],19):19} {str(u)+'/'+str(k):7} "
            f"{str(round(cov*100,1)) if cov is not None else '-':>6} "
            f"{str(b) if b is not None else '-':>3} "
            f"{str(round(sec)) if sec is not None else '-':>6} "
            f"{f'{score:.2f}' if score is not None else '-':>7}")
