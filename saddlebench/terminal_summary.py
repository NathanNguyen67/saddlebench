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


# dataset-and-short-case-table-v1
# Shortening a case ID changes presentation only; matching uses the full ID.
def _short_case(case_id):
    value = str(case_id or '?')
    suffix = value[4:] if value.startswith('case') else ''
    return str(int(suffix)) if suffix.isdigit() else value


def _dataset(case, metadata):
    structure_id = case.get('structure_id') or (metadata.get(case.get('case_id')) or {}).get('structure_id')
    if not isinstance(structure_id, str):
        return '-'
    prefix, separator, remainder = structure_id.partition(':')
    return prefix.upper() if prefix and separator and remainder else '-'


def _cells(case, mode, confidence, dataset):
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
        'short_case': _short_case(case.get('case_id')),
        'dataset': dataset,
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
    rows = [_cells(c, mode, _confidence(c, metadata), _dataset(c, metadata)) for c in cases]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['case']].append(row)
    case_count = len(grouped)
    model_count = len({r['model'] for r in rows})
    lines = ['', '=' * 126,
             f' SADDLEBENCH | {mode} | {len(rows)} submissions | {case_count} case(s) | {model_count} model(s)',
             '=' * 126]

    # All percentages and colored cells are derived from existing JSON values.
    columns = [('#', 2, 'rank'), ('MODEL', 19, 'model'), ('DATASET', 9, 'dataset'), ('CASE', 5, 'short_case'),
               ('N', 4, 'n'), ('U/K', 6, 'uk'), ('COV%', 6, 'cov'),
               ('CONF%', 6, 'conf'), ('HIT', 4, 'hit'), ('DUP', 4, 'dup'),
               ('NF', 3, 'nf'), ('HB', 3, 'hb'), ('OTH', 3, 'oth'),
               ('UNM', 3, 'unm'), ('B', 3, 'bad'), ('SEC', 5, 'time'),
               ('S/H', 7, 'score')]
    hdr = ' '.join(name.rjust(width) if key not in ('model','case','short_case','dataset') else name.ljust(width)
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
                cell = value.ljust(width) if key in ('model','case','short_case','dataset') else value.rjust(width)
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


# Model-view-default: presentation uses existing model_aggregates only.
# No scoring logic, native JSON fields or first-hit classifications change.
def _model_view(report, cases_csv, color):
    metadata = _case_metadata(cases_csv)
    mode = report.get('mode', 'unknown')
    models = report['model_aggregates']
    observations = defaultdict(list)
    for item in report.get('case_results', []):
        observations[item.get('model')].append(item)
    case_sets = [frozenset(m.get('case_ids', [])) for m in models]
    comparable = len(set(case_sets)) <= 1
    union = set().union(*case_sets) if case_sets else set()
    ranked = sorted(models, key=lambda m: (
        _numeric(m.get('aggregate', {}).get('provisional_score_per_hour')) is None,
        -(_numeric(m.get('aggregate', {}).get('provisional_score_per_hour')) or 0),
        m['model'])) if comparable else sorted(models, key=lambda m: m['model'])
    lines = ['', 'SADDLEBENCH | MODEL LEADERBOARD | ' + mode,
             ('Same submitted case set: YES' if comparable else
              'Same submitted case set: NO — aggregate ranking disabled')]
    columns = [('#', 2), ('MODEL', 19), ('DATASET', 8), ('CASES', 7),
               ('U/K', 9), ('COV%', 6), ('REF%', 6), ('N', 6),
               ('DUP', 5), ('NF', 4), ('HB', 4), ('OTH', 5),
               ('UNM', 5), ('B', 4), ('SEC', 8), ('S/H', 9)]
    header = ' '.join(name.ljust(width) if name in ('MODEL', 'DATASET') else name.rjust(width)
                      for name, width in columns)
    lines += [_ansi(header, '1;36', color), '-' * len(header)]
    for index, model in enumerate(ranked, 1):
        a = model.get('aggregate', {})
        label = model['model']
        entries = observations.get(label, [])
        stages = Counter()
        categories = Counter()
        statuses = Counter()
        n = 0
        dataset_names = set()
        confidence_values = []
        for case in entries:
            predictions = case.get('entries') or []
            n += len(predictions)
            stages.update(p.get('stage') or 'unmatched' for p in predictions)
            statuses.update(p.get('status') for p in predictions)
            categories.update(p.get('category') for p in predictions
                              if p.get('stage') not in (None, 'eligible'))
            dataset_names.add(_dataset(case, metadata))
            confidence_values.append(_confidence(case, metadata))
        dataset = next(iter(dataset_names)) if len(dataset_names) == 1 else 'MIXED' if dataset_names else '-'
        reference_confidence = (sum(confidence_values) / len(confidence_values)
                                if confidence_values and all(v is not None for v in confidence_values)
                                else None)
        k = a.get('reference_known_K')
        u = a.get('greedy_unique_reactions_U')
        cov = _numeric(a.get('micro_coverage'))
        score = _numeric(a.get('provisional_score_per_hour'))
        sec = _numeric(a.get('total_self_reported_seconds'))
        b = a.get('first_hit_potential_bad_groups_with_known_start') if mode != 'coverage' else None
        other = sum(q for stage, q in stages.items() if stage not in ('eligible', 'unmatched'))
        nf = categories['KNOWN_NON_FIRST_ORDER']
        hb = categories['KNOWN_CONNECTED_HIGH_BARRIER'] if mode == 'score_detailed' else None
        neutral = other - nf - (hb or 0) if mode == 'score_detailed' else None
        values = [str(index) if comparable else '-', label, dataset,
                  f"{len(model.get('case_ids', []))}/{len(union)}",
                  f'{u}/{k}' if u is not None and k is not None else '-',
                  f'{100 * cov:.1f}' if cov is not None else '-',
                  f'{100 * reference_confidence:.1f}' if reference_confidence is not None else '-',
                  str(n), str(statuses['DUPLICATE']),
                  str(nf) if mode != 'coverage' else '-',
                  str(hb) if hb is not None else '-',
                  str(neutral) if neutral is not None else '-',
                  str(stages['unmatched']), str(b) if b is not None else '-',
                  str(round(sec)) if sec is not None else '-',
                  f'{score:.2f}' if score is not None else '-']
        cells = []
        for (name, width), value in zip(columns, values):
            cell = _clip(value, width)
            cell = cell.ljust(width) if name in ('MODEL', 'DATASET') else cell.rjust(width)
            if name == 'COV%' and cov is not None:
                cell = _ansi(cell, '32' if cov >= .9 else '33' if cov >= .5 else '31', color)
            elif name == 'REF%' and reference_confidence is not None:
                cell = _ansi(cell, '36', color)
            elif name in ('NF', 'HB', 'B') and value not in ('0', '-'):
                cell = _ansi(cell, '31', color)
            elif name == 'S/H' and index == 1 and comparable and score is not None:
                cell = _ansi(cell, '1;32', color)
            cells.append(cell)
        lines.append(' '.join(cells))
    lines += ['', 'LEGEND  U/K=unique eligible reactions / known eligible reactions; COV%=U/K;',
              '        REF%=mean published historical reference-confidence heuristic for submitted cases;',
              '        N=predictions; DUP=duplicate eligible hits; NF=known non-first-order first hits;',
              '        HB=high-barrier first hits; OTH=other known saddle first hits;',
              '        UNM=unmatched in checked stages; B=distinct first-hit penalty groups;',
              '        SEC=search seconds; S/H=provisional score per hour.',
              'NOTE    B is provisional, not a certified bad-reaction count. REF% is not',
              '        probability all saddles were found. Cases are only checked against',
              '        the submitted union; this does NOT certify full benchmark coverage.',
              '        Use --view case for per-case results.']
    if not comparable:
        lines.append('        UNRANKED: models have different submitted case sets.')
    if mode == 'coverage':
        lines.append('        Coverage mode does not evaluate NF/HB/OTH/B.')
    elif mode == 'score':
        lines.append('        Score mode does not evaluate HB/OTH.')
    return '\n'.join(lines)


# Preserve the original case view and its existing behavior.
_case_view = render_summary

def render_summary(report, *, cases_csv=None, color=None, view='model'):
    if view not in ('model', 'case'):
        raise ValueError('INVALID_TERMINAL_VIEW')
    if color is None:
        color = (sys.stdout.isatty() and not os.environ.get('NO_COLOR')
                 and os.environ.get('TERM', '') != 'dumb') or os.environ.get('SADDLEBENCH_COLOR') == 'always'
    if os.environ.get('SADDLEBENCH_COLOR') == 'never':
        color = False
    if view == 'case' or not report.get('model_aggregates'):
        return _case_view(report, cases_csv=cases_csv, color=color)
    return _model_view(report, cases_csv, color)
