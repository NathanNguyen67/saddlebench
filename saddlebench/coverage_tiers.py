"""Read-only three-threshold first-hit reference coverage and greedy-hit accuracy.

Distinct from the existing score/score_detailed/coverage APIs and JSON schemas.
Adds all-selected-match median while keeping all original JSON fields unchanged.
No Hessians, DoubleMin, force calls, or global nearest-reference search.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import time

THRESHOLDS = (0.20, 0.10, 0.05)
SCHEMA = 'saddlebench-greedy-coverage-tiers-v1'


def _median(values):
    values = list(values)
    return float(statistics.median(values)) if values else None


def _tier_label(t):
    return f'{t:.2f}'


def _find_greedy(stage, geometry, threshold, batch_size):
    """Return distance and reaction of first eligible hit, without nearest search.

    Exactly the same near ordering, group radius catalog and exact maxdev
    calculation as the original greedy coverage matcher. The scalar distance
    below is one verification/evidence measurement for the selected reference.
    """
    from saddlebench.matching.geometry import core_drift_maxdev

    found = stage.catalog.find_first(geometry, batch_size=batch_size,
                                     order='near', group_prune=True,
                                     threshold=threshold)
    if found.get('uncertain_scalar', 0):
        raise ValueError('GREEDY_UNCERTAIN_SCALAR_COMPARISON')
    stats = Counter({key: value for key, value in found.items()
                     if key not in ('found', 'match_index') and type(value) is int})
    if not found.get('found'):
        return None, stats
    idx = found.get('match_index')
    if type(idx) is not int or not 0 <= idx < len(stage.members):
        raise ValueError('GREEDY_MATCH_INDEX_MISSING')
    member = stage.members[idx][1]
    distance = core_drift_maxdev(geometry, member['geometry'])
    if not math.isfinite(distance) or distance > threshold + 1e-12:
        raise ValueError('GREEDY_MATCH_DISTANCE_INCONSISTENT')
    return (str(member['reaction_id']), distance), stats


def _tally_predictions(predictions, eligible, k, batch_size):
    """Three independent greedy first-hit assignments using one reference index.

    MEDIAN: median selected-reference maxdev across ALL matched predictions,
      including repeats, not just one per reaction.
    BEST: median across credited reactions of their smallest observed selected-
      reference distance. Neither minimizes over all reference geometries.
    """
    states = {
        _tier_label(t): {
            'first': {}, 'best': {}, 'distances': [], 'matched_predictions': 0,
            'work': Counter(), 'threshold': t,
        } for t in THRESHOLDS
    }
    start = time.perf_counter()
    n = 0
    for pred in predictions:
        n += 1
        numbers = tuple(pred['geometry']['numbers'])
        stage = eligible.get(numbers)
        if stage is None:
            continue
        # A certified absence at a looser radius excludes smaller radii too.
        for t in THRESHOLDS:
            state = states[_tier_label(t)]
            matched, stats = _find_greedy(stage, pred['geometry'], t, batch_size)
            state['work'].update(stats)
            if matched is None:
                break
            rid, d = matched
            state['matched_predictions'] += 1
            state['distances'].append(float(d))
            state['first'].setdefault(rid, d)
            state['best'][rid] = min(d, state['best'].get(rid, d))
    elapsed = time.perf_counter() - start
    result = {}
    for label, state in states.items():
        first = state['first']
        best = state['best']
        u = len(first)
        if u > k:
            raise ValueError('GREEDY_CREDIT_EXCEEDS_CASE_K')
        result[label] = {
            'threshold_maxdev_A': state['threshold'],
            'greedy_unique_reactions_U': u,
            'known_eligible_reactions_K': k,
            'micro_coverage': u / k if k else None,
            'median_matched_predictions_maxdev_A': _median(state['distances']),
            'matched_prediction_maxdev_A': state['distances'],
            'greedy_first_hit_median_maxdev_A': _median(first.values()),
            'best_observed_greedy_hit_median_maxdev_A': _median(best.values()),
            'matched_predictions': state['matched_predictions'],
            'matching_work_counts': dict(state['work']),
            'matched_reactions': [
                {'reaction_id': rid,
                 'greedy_first_hit_maxdev_A': first[rid],
                 'best_observed_greedy_hit_maxdev_A': best[rid]}
                for rid in sorted(first)
            ],
        }
    return n, result, elapsed


def _case_worker(task):
    from saddlebench.prototype import reaction_mvp as rm
    from saddlebench.matching.greedy_modes import (
        _group_eligible, _build_stages, _load_pinned_eligible_indices,
    )
    private = Path(task['private'])
    case = task['case']
    _, reactions = rm.load_reference(private / 'reference')
    meta = json.loads((private / 'private_manifest.json').read_text())
    if meta['source_registry_sha256'] != case['registry_sha256']:
        raise ValueError('GREEDY_REGISTRY_IDENTITY_MISMATCH')
    predictions = rm.read_submissions(task['trajectory'],
                                      start=case['starting_minimum_id'],
                                      parent=case['structure_id'],
                                      pes=case['pes_id'])
    grouped = _group_eligible(reactions, case)
    eligible = _build_stages(grouped, Path(task['index_cache']), 'eligible',
                             _load_pinned_eligible_indices(case, grouped))
    k = int(case['known_reaction_count'])
    n, tiers, match_seconds = _tally_predictions(
        predictions, eligible, k, task['microbatch_size'])
    return {
        'model': task.get('model', 'default'),
        'case_id': case['case_id'],
        'structure_id': case['structure_id'],
        'starting_minimum_id': case['starting_minimum_id'],
        'prediction_count': n,
        'self_reported_elapsed_seconds': task['elapsed_seconds'],
        'matching_only_seconds': match_seconds,
        'thresholds': tiers,
    }


def _aggregate(items):
    k = sum(x['thresholds']['0.20']['known_eligible_reactions_K'] for x in items)
    secs = sum(x['self_reported_elapsed_seconds'] for x in items)
    thresholds = {}
    for t in THRESHOLDS:
        label = _tier_label(t)
        u = sum(x['thresholds'][label]['greedy_unique_reactions_U'] for x in items)
        eligible = [x['thresholds'][label]['micro_coverage'] for x in items
                    if x['thresholds'][label]['micro_coverage'] is not None]
        distances = [r for x in items for r in x['thresholds'][label]['matched_reactions']]
        if len(distances) != u:
            raise ValueError('AGGREGATE_MATCH_COUNT_INCONSISTENT')
        all_hits = [d for x in items for d in x['thresholds'][label]['matched_prediction_maxdev_A']]
        if sum(x['thresholds'][label]['matched_predictions'] for x in items) != len(all_hits):
            raise ValueError('AGGREGATE_MATCHED_PREDICTION_COUNT_INCONSISTENT')
        thresholds[label] = {
            'greedy_unique_reactions_U': u,
            'known_eligible_reactions_K': k,
            'micro_coverage': u / k if k else None,
            'macro_coverage_known_cases': sum(eligible) / len(eligible) if eligible else None,
            'median_matched_predictions_maxdev_A': _median(all_hits),
            'greedy_first_hit_median_maxdev_A': _median(
                r['greedy_first_hit_maxdev_A'] for r in distances),
            'best_observed_greedy_hit_median_maxdev_A': _median(
                r['best_observed_greedy_hit_maxdev_A'] for r in distances),
        }
    return {'thresholds': thresholds,
            'total_self_reported_seconds': secs,
            'total_predictions': sum(x['prediction_count'] for x in items)}


def _create_report(results, available):
    by_model = defaultdict(list)
    for result in results:
        by_model[result['model']].append(result)
    models = [
        {'model': name, 'case_ids': sorted(x['case_id'] for x in cases),
         'selected_cases': len(cases), 'aggregate': _aggregate(cases)}
        for name, cases in sorted(by_model.items())
    ]
    sets = [frozenset(m['case_ids']) for m in models]
    return {'schema_version': SCHEMA, 'mode': 'coverage_tiers',
            'thresholds_maxdev_A': list(THRESHOLDS),
            'available_cases': available,
            'selected_cases': len({x['case_id'] for x in results}),
            'model_case_sets_identical': len(set(sets)) <= 1,
            'method': 'independent first-hit greedy eligible-only per threshold',
            'accuracy_definition': {
                'median': 'median selected-reference maxdev over all eligible matched predictions, including duplicates',
                'closest_match_median': 'median over credited reactions of smallest observed selected-reference maxdev; NOT an exhaustive nearest reference search',
            },
            'model_aggregates': models, 'case_results': results}


def _percent(value):
    return f'{value * 100:6.1f}' if value is not None else '     -'


def _distance(value):
    return f'{value:.4f}' if value is not None else '-'


def render_report(report, view='model'):
    if view not in ('model', 'case'):
        raise ValueError('INVALID_VIEW')
    lines = [f'SADDLEBENCH | COVERAGE TIERS | {view.upper()}',
             'COV columns use MICRO coverage; MEDIAN and BEST MED are Å at 0.20 Å.',
             'BEST MED uses one best observed match per reaction; not nearest-reference search.']
    if view == 'model':
        models = report['model_aggregates']
        if report['model_case_sets_identical']:
            models = sorted(models, key=lambda r: (
                -(r['aggregate']['thresholds']['0.20']['micro_coverage'] or 0.0),
                r['model']))
        else:
            lines.append('UNRANKED: submitted case sets differ across models')
        lines.append(f"{'MODEL':19} {'CASES':>7} {'U/K@.20':>10} {'COV@.20':>8} {'COV@.10':>8} {'COV@.05':>8} {'MEDIAN':>9} {'BEST MED':>10} {'SEC':>9}")
        lines.append('-' * 100)
        available = report['available_cases']
        for model in models:
            x = model['aggregate']; tiers = x['thresholds']
            first = tiers['0.20']; u = first['greedy_unique_reactions_U']; k = first['known_eligible_reactions_K']
            lines.append(f"{model['model'][:19]:19} {str(model['selected_cases'])+'/'+str(available):>7} "
                         f"{str(u)+'/'+str(k):>10} "
                         f"{_percent(first['micro_coverage']):>8} "
                         f"{_percent(tiers['0.10']['micro_coverage']):>8} "
                         f"{_percent(tiers['0.05']['micro_coverage']):>8} "
                         f"{_distance(first['median_matched_predictions_maxdev_A']):>9} "
                         f"{_distance(first['best_observed_greedy_hit_median_maxdev_A']):>10} "
                         f"{x['total_self_reported_seconds']:9.0f}")
    else:
        lines.append(f"{'MODEL':19} {'CASE':>9} {'U/K@.20':>10} {'COV@.20':>8} {'COV@.10':>8} {'COV@.05':>8} {'MEDIAN':>9} {'BEST MED':>10} {'SEC':>9}")
        lines.append('-' * 100)
        for x in sorted(report['case_results'], key=lambda x: (x['case_id'], x['model'])):
            tiers = x['thresholds']; first = tiers['0.20']
            lines.append(f"{x['model'][:19]:19} {x['case_id']:>9} "
                         f"{str(first['greedy_unique_reactions_U'])+'/'+str(first['known_eligible_reactions_K']):>10} "
                         f"{_percent(first['micro_coverage']):>8} "
                         f"{_percent(tiers['0.10']['micro_coverage']):>8} "
                         f"{_percent(tiers['0.05']['micro_coverage']):>8} "
                         f"{_distance(first['median_matched_predictions_maxdev_A']):>9} "
                         f"{_distance(first['best_observed_greedy_hit_median_maxdev_A']):>10} "
                         f"{x['self_reported_elapsed_seconds']:9.0f}")
    lines.append('COV@t=unique eligible reactions matched at maxdev <=t / published known eligible reactions.')
    lines.append('Thresholds rerun greedy selection; tighter U counts are not mathematically guaranteed nested.')
    lines.append('MEDIAN uses all matched predictions (repeats included); BEST MED uses one best per reaction.')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='SaddleBench three-tier greedy reference coverage')
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--submission-csv')
    selection.add_argument('--case-id')
    parser.add_argument('--submission', help='ASE .traj of predictions for --case-id')
    parser.add_argument('--elapsed-seconds', type=float, help='Search time for --case-id')
    parser.add_argument('--output', required=True)
    parser.add_argument('--view', choices=('model', 'case'), default='model')
    parser.add_argument('--processes', type=int, default=1)
    parser.add_argument('--microbatch-size', type=int, default=16)
    parser.add_argument('--cache-dir', default=str(Path.home() / '.cache/saddlebench_reference'))
    parser.add_argument('--index-cache-dir', default=str(Path.home() / '.cache/saddlebench_first_hit_index'))
    parser.add_argument('--reference-root', help='Offline verified HF private reference snapshot directory')
    parser.add_argument('--cases-csv', help='Offline HF public cases.csv (with --reference-root)')
    parser.add_argument('--no-progress', action='store_true')
    parser.add_argument('--no-html', action='store_true', help='Do not write standalone HTML')
    parser.add_argument('--no-image', action='store_true', help='Do not write dataset SVG/PNG')
    parser.add_argument('--open-image', action='store_true', help='Request an X11 image viewer when available')
    parser.add_argument('--no-color', action='store_true', help='Disable ANSI colors')
    parser.add_argument('--color', action='store_true', help='Force ANSI color output')
    args = parser.parse_args(argv)
    if args.case_id and (args.submission is None or args.elapsed_seconds is None):
        parser.error('--case-id requires --submission and --elapsed-seconds')
    if args.submission_csv and (args.submission is not None or args.elapsed_seconds is not None):
        parser.error('--submission-csv cannot use --submission or --elapsed-seconds')
    if args.color and args.no_color:
        parser.error('--color and --no-color are mutually exclusive')
    if (args.reference_root is None) != (args.cases_csv is None):
        parser.error('--reference-root and --cases-csv must be provided together')
    if args.processes < 1 or not 1 <= args.microbatch_size <= 256:
        parser.error('processes must be >=1 and microbatch-size must be 1..256')
    os.umask(0o077)
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                 'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[name] = '1'
    from saddlebench.score_modes import _collect_tasks, verify_reference
    output = Path(args.output).expanduser().absolute()
    if output.exists():
        raise SystemExit('OUTPUT_ALREADY_EXISTS: ' + str(output))
    if args.reference_root:
        private = Path(args.reference_root).expanduser().absolute()
        casefile = Path(args.cases_csv).expanduser().absolute()
    else:
        cache = Path(args.cache_dir).expanduser().absolute()
        for part in (cache, *cache.parents):
            if part.is_symlink():
                raise ValueError('SYMLINK_IN_REFERENCE_CACHE_PATH')
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache.chmod(0o700)
        from huggingface_hub import hf_hub_download, snapshot_download
        if not args.no_progress:
            print('REFERENCE_DOWNLOAD=START', flush=True)
        try:
            private = Path(snapshot_download(
                repo_id='traingletrain/saddlebench-reference-private',
                repo_type='dataset', cache_dir=str(cache), token=False,
                allow_patterns=['private_manifest.json', 'reference/*',
                                'diagnostic_saddles.jsonl.gz', 'CHECKSUMS.json']))
            casefile = Path(hf_hub_download(
                repo_id='traingletrain/saddlebench-prototype', repo_type='dataset',
                filename='cases.csv', cache_dir=str(cache), token=False))
        except Exception as exc:
            raise SystemExit('PUBLIC_HF_REFERENCE_UNAVAILABLE: ' + type(exc).__name__) from exc
    if args.reference_root:
        verify_reference(private, casefile)
    else:
        from saddlebench.reference.hf_compat import resolve_cases
        casefile = resolve_cases(private, casefile, cache, verify_reference)
    submission_csv = Path(args.submission_csv).expanduser().absolute() if args.submission_csv else None
    tasks, available = _collect_tasks(
        casefile=casefile, public_root=casefile.parent, private=private,
        case_id=args.case_id, submission=args.submission, elapsed_seconds=args.elapsed_seconds,
        submission_csv=submission_csv, mode='coverage',
        microbatch_size=args.microbatch_size, nonfo_cache=None,
        index_cache=Path(args.index_cache_dir) / 'group_indices')
    if args.processes > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=min(args.processes, len(tasks))) as pool:
            results = list(pool.map(_case_worker, tasks))
    else:
        results = [_case_worker(task) for task in tasks]
    report = _create_report(results, available)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    fd, temp = tempfile.mkstemp(dir=output.parent, prefix='.saddlebench_tiers_pending_')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.link(temp, output)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print('REFERENCE_VERIFY=PASS')
    print('COVERAGE_TIERS=PASS')
    print('OUTPUT=' + str(output))
    # One terminal view. The renderer consumes the native JSON report unchanged.
    from saddlebench.coverage_leaderboard import terminal_view, html_view, write_new
    from saddlebench.coverage_image import render_images
    from sys import stdout
    color = args.color or (not args.no_color and stdout.isatty()
                           and not os.environ.get('NO_COLOR'))
    print(terminal_view(report, use_color=color) if args.view=='model'
          else render_report(report, view='case'))
    if not args.no_html:
        html = output.with_suffix('.html')
        write_new(html, html_view(report))
        print('HTML_THIS=' + str(html))
    if not args.no_image:
        for image in render_images(output):
            print('DATASET=' + image['dataset'])
            print('SVG_THIS=' + image['svg'])
            if image['png']:
                print('PNG_THIS=' + image['png'])
            else:
                print('PNG_NOTE=' + image['conversion'])
            if args.open_image:
                from saddlebench.coverage_image_open import open_image
                print('IMAGE_OPEN=' + open_image(image['png'] or image['svg']))
    print('LEADERBOARD_RENDER=PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
