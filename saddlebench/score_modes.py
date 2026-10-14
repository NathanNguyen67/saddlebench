"""Public SaddleBench mode routing: greedy score (default), greedy full diagnostic detail, coverage.

SCIENCE: all three modes use first-hit eligible reaction classification.
`score_detailed` also searches the complete diagnostic catalog with greedy exit.
No greedy mode establishes canonical ambiguity or canonical known-bad B.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

MODES = ('score', 'score_detailed', 'coverage')
SCORE_WARNING = ('First-hit score: eligible reactions are checked before known '
    'non-first-order reference saddles. This does not exhaust competing reaction '
    'identities, competing neutral diagnostics, or every known-bad group. '
    'First-hit non-FO penalties are NOT canonical W2 known_bad_unique_B. '
    'Use score_detailed for broader first-hit diagnostics; neither greedy mode proves canonical B.')
DETAILED_WARNING = ('Greedy full-diagnostic score: eligible reactions first, then known '
    'non-first-order, then remaining diagnostic categories, stopping at the first '
    'exact match at each stage. It does NOT enumerate all matching groups, prove '
    'cross-reaction ambiguity or calculate canonical W2 known_bad_unique_B. '
    'The first-hit potential-bad count is an unverified preview, not canonical B.')
COVERAGE_WARNING = ('Reaction coverage only: stop at the first eligible reference '
    'hit. No non-first-order or other diagnostic catalog was searched. '
    'Greedy reaction credits can differ from detailed-scoring ambiguity rules.')


def _file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            h.update(part)
    return h.hexdigest()


REGISTRY_SHA='85d37e55448b544473b34e7bb244823e80ddc85091bbe6ae3e771c7beb4b0a41'


def verify_reference(root, cases_path):
    """Unchanged published-reference verification contract from original CLI."""
    root=Path(root)
    private_meta=json.loads((root/'private_manifest.json').read_text())
    refmeta=json.loads((root/'reference/manifest.json').read_text())
    hashes=json.loads((root/'CHECKSUMS.json').read_text())
    if private_meta.get('source_registry_sha256')!=REGISTRY_SHA or refmeta.get('registry_sha256')!=REGISTRY_SHA:
        raise RuntimeError('REFERENCE_REGISTRY_SHA_MISMATCH')
    if private_meta.get('mode')!='all_saddles' or refmeta.get('mode')!='all_saddles':
        raise RuntimeError('REFERENCE_MODE_MISMATCH')
    if private_meta.get('case_count')!=123 or refmeta.get('reference_count')!=2387:
        raise RuntimeError('REFERENCE_COUNTS_MISMATCH')
    for relative,expected in hashes.items():
        if (not isinstance(relative,str) or not relative or Path(relative).is_absolute()
                or '..' in Path(relative).parts or not isinstance(expected,str)
                or len(expected)!=64 or not (root/relative).is_file()
                or _file_digest(root/relative)!=expected):
            raise RuntimeError('REFERENCE_CHECKSUM_FAILED:'+str(relative))
    if _file_digest(root/'reference/reference.jsonl')!=refmeta['reference_sha256']:
        raise RuntimeError('REACTION_REFERENCE_HASH_MISMATCH')
    with Path(cases_path).open(newline='',encoding='utf8') as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=123 or any(x['registry_sha256']!=REGISTRY_SHA for x in rows):
        raise RuntimeError('PUBLIC_INPUTS_SNAPSHOT_MISMATCH')
    return True


def _score_metric(u, k, b, seconds):
    if not k:
        return None
    if seconds <= 0:
        raise ValueError('ELAPSED_SECONDS_MUST_BE_POSITIVE')
    if not 0 <= u <= k or b < 0:
        raise ValueError('INVALID_SCORING_COUNTS')
    return 100 * (u / k)**2 * (u / (u + 2*b) if u else 0.0) * 3600 / seconds


def _case_worker(task):
    public = Path(task['public'])
    private = Path(task['private'])
    case = task['case']
    mode = task['mode']
    trajectory = Path(task['trajectory'])
    seconds = float(task['elapsed_seconds'])
    from saddlebench.matching.greedy_modes import run_case
    observation = run_case(public, private, case, trajectory, seconds,
        task.get('nonfo_cache'), mode=('greedy_full' if mode == 'score_detailed' else mode),
        batch_size=task['microbatch_size'],
        cache_root=Path(task['index_cache']))
    k = observation['known_eligible_reactions_K']
    u = observation['greedy_unique_reactions_U']
    bad = observation['greedy_potential_bad_first_hit_groups'] if mode != 'coverage' else 0
    score = _score_metric(u, k, bad, seconds)
    return {
        'schema_version': 'saddlebench-quick-case-v1',
        'mode': mode,
        'case_id': case['case_id'],
        'structure_id': case['structure_id'],
        'starting_minimum_id': case['starting_minimum_id'],
        'known_eligible_reactions_K': k,
        'greedy_unique_reactions_U': u,
        'coverage': u/k if k else None,
        'scorable': bool(k),
        'self_reported_elapsed_seconds': seconds,
        'provisional_score_per_hour': score,
        'score_semantics': {'score': 'GREEDY_FIRST_HIT_NONFO',
            'score_detailed': 'GREEDY_FIRST_HIT_FULL_DIAGNOSTICS',
            'coverage': 'GREEDY_REACTION_COVERAGE_ONLY'}[mode],
        'first_hit_nonfo_groups_with_known_start': (bad if mode == 'score' else None),
        'first_hit_potential_bad_groups_with_known_start': (bad if mode != 'coverage' else None),
        'canonical_known_bad_unique_B': None,
        'nonfo_checked': mode != 'coverage',
        'full_diagnostic_checked': mode == 'score_detailed',
        'entries': observation['entries'],
        'work_counts': observation['work_counts'],
        'stage_counts': observation['stage_counts'],
        'matching_only_seconds': observation['matching_only_seconds'],
        'warning': {'score': SCORE_WARNING, 'score_detailed': DETAILED_WARNING,
                    'coverage': COVERAGE_WARNING}[mode],
    }


def _load_cases(casefile):
    with Path(casefile).open(newline='', encoding='utf8') as f:
        rows = list(csv.DictReader(f))
    if not rows or any('case_id' not in r for r in rows):
        raise ValueError('INVALID_PUBLIC_CASES_CSV')
    if len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('DUPLICATE_PUBLIC_CASE')
    return {r['case_id']: r for r in rows}


def _collect_tasks(*, casefile, public_root, private, case_id, submission,
                   elapsed_seconds, submission_csv, mode, microbatch_size,
                   nonfo_cache, index_cache):
    cases = _load_cases(casefile)
    if case_id is not None:
        if case_id not in cases:
            raise ValueError('UNKNOWN_CASE_ID')
        jobs = [(case_id, str(submission), elapsed_seconds, None)]
    else:
        with Path(submission_csv).open(newline='', encoding='utf8') as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            if not {'case_id', 'trajectory', 'elapsed_seconds'}.issubset(fields):
                raise ValueError('SUBMISSION_CSV_COLUMNS_REQUIRED')
            has_model = 'model' in fields
            rows = list(reader)
        if not rows:
            raise ValueError('EMPTY_SUBMISSION_CSV')
        if has_model:
            labels = [str(row.get('model') or '').strip() for row in rows]
            if any(not label for label in labels):
                raise ValueError('EMPTY_MODEL_LABEL')
            if len({(label, row['case_id']) for label, row in zip(labels, rows)}) != len(rows):
                raise ValueError('DUPLICATE_MODEL_CASE')
            jobs = [(row['case_id'], row['trajectory'], row['elapsed_seconds'], label)
                    for row, label in zip(rows, labels)]
        else:
            if len({row['case_id'] for row in rows}) != len(rows):
                raise ValueError('DUPLICATE_SUBMITTED_CASE')
            jobs = [(row['case_id'], row['trajectory'], row['elapsed_seconds'], None)
                    for row in rows]
    tasks=[]
    for cid, name, seconds, model in jobs:
        if cid not in cases:
            raise ValueError('UNKNOWN_CASE_ID: '+cid)
        traj = Path(name).expanduser()
        if not traj.is_absolute() and submission_csv is not None:
            traj = Path(submission_csv).parent / traj
        traj = traj.absolute()
        if traj.is_symlink() or not traj.is_file():
            raise ValueError('UNSAFE_OR_MISSING_TRAJECTORY: '+str(traj))
        seconds = float(seconds)
        if seconds <= 0:
            raise ValueError('ELAPSED_SECONDS_MUST_BE_POSITIVE')
        task = {'case': cases[cid], 'public': str(public_root), 'private': str(private),
            'trajectory': str(traj), 'elapsed_seconds': seconds, 'mode': mode,
            'microbatch_size': microbatch_size,
            'nonfo_cache': str(nonfo_cache) if nonfo_cache is not None else None,
            'index_cache': str(index_cache)}
        if model is not None:
            task['model'] = model
        tasks.append(task)
    return tasks, len(cases)


def _batch_report(reports, mode, total_available):
    k = sum(r['known_eligible_reactions_K'] for r in reports)
    u = sum(r['greedy_unique_reactions_U'] for r in reports)
    b = sum(r['first_hit_potential_bad_groups_with_known_start'] or 0 for r in reports)
    seconds = sum(r['self_reported_elapsed_seconds'] for r in reports)
    coverage = [r['coverage'] for r in reports if r['coverage'] is not None]
    aggregate = {'reference_known_K': k, 'greedy_unique_reactions_U': u,
        'micro_coverage': u/k if k else None,
        'macro_coverage_known_cases': sum(coverage)/len(coverage) if coverage else None,
        'total_self_reported_seconds': seconds,
        'provisional_score_per_hour': _score_metric(u,k,b if mode != 'coverage' else 0,seconds),
        'first_hit_nonfo_groups_with_known_start': b if mode == 'score' else None,
        'first_hit_potential_bad_groups_with_known_start': b if mode != 'coverage' else None,
        'canonical_known_bad_unique_B': None}
    return {'schema_version':'saddlebench-quick-batch-v1', 'mode':mode,
        'selected_cases':len(reports),'available_cases':total_available,
        'aggregate':aggregate,'case_results':reports,
        'warning': {'score': SCORE_WARNING, 'score_detailed': DETAILED_WARNING,
                    'coverage': COVERAGE_WARNING}[mode]}


def _model_report(reports, mode, total_available):
    """Separate per-model aggregates; do not merge different model denominators."""
    groups = {}
    for report in reports:
        groups.setdefault(report['model'], []).append(report)
    arms = []
    for label, items in groups.items():
        arms.append({'model': label, 'selected_cases': len(items),
                     'case_ids': sorted(item['case_id'] for item in items),
                     'aggregate': _batch_report(items, mode, total_available)['aggregate']})
    return {'schema_version': 'saddlebench-quick-model-comparison-v1',
            'mode': mode, 'selected_cases': len({r['case_id'] for r in reports}),
            'selected_submissions': len(reports), 'available_cases': total_available,
            'model_aggregates': arms, 'case_results': reports,
            'warning': 'Model scores are comparable only for the same case sets, '
                       'reference snapshot, PES, scoring policy, and correct self-reported timing.'}


def _detailed_batch_report(reports, total_available):
    # Archival helper only; no CLI scoring mode calls this legacy W2 routine.
    # Mirror the frozen grade_batch aggregate *without* changing any native
    # case report. This is required for optional process-level parallelism.
    from saddlebench.prototype.release_mvp import score_metric
    k=sum(r['known_eligible_reactions_K'] for r in reports)
    u=sum(r['unique_reactions_U'] for r in reports)
    b=sum(r['known_bad_unique_B'] for r in reports)
    t=sum(r['self_reported_elapsed_seconds'] for r in reports)
    scored=[r['coverage'] for r in reports if r['coverage'] is not None]
    return {'schema_version':'saddlebench-prototype-batch-v1',
        'selected_cases':len(reports),'available_cases':total_available,
        'aggregate':{'reference_known_K':k,'unique_credited_U':u,'known_bad_unique_B':b,
            'total_self_reported_seconds':t,'micro_coverage':u/k if k else None,
            'macro_coverage_known_cases':sum(scored)/len(scored) if scored else None,
            'provisional_score_per_hour':score_metric(u,k,b,t)},
        'case_results':reports,
        'comparability_warning':('Aggregate applies ONLY to explicitly submitted cases; '
            'cherry-picked subsets, self-reported times and unverified PES are not controlled leaderboard comparisons.')}


def run(*, casefile, private, case_id=None, submission=None, elapsed_seconds=None,
        submission_csv=None, mode='score', processes=1, microbatch_size=16,
        index_cache=None, nonfo_cache=None):
    if mode not in MODES:
        raise ValueError('UNKNOWN_SCORE_MODE')
    if type(processes) is not int or processes < 1:
        raise ValueError('PROCESSES_MUST_BE_POSITIVE')
    if not 1 <= microbatch_size <= 256:
        raise ValueError('MICROBATCH_OUT_OF_RANGE')
    if bool(case_id) == bool(submission_csv):
        raise ValueError('CHOOSE_CASE_ID_OR_SUBMISSION_CSV')
    if case_id and (not submission or elapsed_seconds is None):
        raise ValueError('SINGLE_CASE_REQUIRES_SUBMISSION_AND_ELAPSED_SECONDS')
    if submission_csv and (submission is not None or elapsed_seconds is not None):
        raise ValueError('BATCH_DOES_NOT_TAKE_SINGLE_SUBMISSION')
    casefile=Path(casefile)
    private=Path(private)
    tasks,total=_collect_tasks(casefile=casefile,public_root=casefile.parent,
        private=private,case_id=case_id,submission=submission,
        elapsed_seconds=elapsed_seconds,submission_csv=submission_csv,mode=mode,
        microbatch_size=microbatch_size,nonfo_cache=nonfo_cache,index_cache=index_cache)
    if mode in ('score','score_detailed') and nonfo_cache is None:
        raise ValueError('SCORE_REQUIRES_VERIFIED_NONFO_CACHE')
    # Serial remains the default. Parallelism is strictly across submitted
    # cases, never within one prediction or a single case.
    if processes==1 or len(tasks)==1:
        reports=[_case_worker(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(processes,len(tasks))) as pool:
            reports=list(pool.map(_case_worker,tasks))
    if case_id:
        return reports[0]
    if any('model' in task for task in tasks):
        # Annotate in the parent process: worker and scoring logic stay unchanged.
        for task, result in zip(tasks, reports):
            result['model'] = task['model']
        return _model_report(reports,mode,total)
    return _batch_report(reports,mode,total)


def main(argv=None):
    p=argparse.ArgumentParser(description='Pinned public-reference SaddleBench scorer (CPU-only).')
    selection=p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--case-id')
    selection.add_argument('--submission-csv')
    p.add_argument('--submission',help='ASE .traj containing predicted saddle geometries')
    p.add_argument('--elapsed-seconds',type=float,help='Search runtime, not scoring runtime')
    p.add_argument('--output',required=True,help='New JSON output path; never overwritten')
    p.add_argument('--mode',choices=MODES,default='score',help='score (default): greedy eligible + non-FO; score_detailed: greedy all diagnostics; coverage: greedy eligible only')
    p.add_argument('--view',choices=('model','case'),default='model',help='Terminal output view (default: model for labeled CSV; case shows individual submissions)')
    p.add_argument('--processes',type=int,default=1,help='Case-level CPU processes; default 1, no parallelism')
    p.add_argument('--microbatch-size',type=int,default=16,help='First-hit exact vectorized MIC microbatch; default 16')
    p.add_argument('--cache-dir',default=str(Path.home()/'.cache/saddlebench_reference'))
    p.add_argument('--index-cache-dir',default=str(Path.home()/'.cache/saddlebench_first_hit_index'))
    p.add_argument('--reference-root',help='Offline verified HF reference snapshot directory')
    p.add_argument('--cases-csv',help='Offline published cases.csv (requires --reference-root)')
    p.add_argument('--no-progress',action='store_true',help='Silence reference download messages')
    args=p.parse_args(argv)
    if (args.reference_root is None)!=(args.cases_csv is None):
        p.error('--reference-root and --cases-csv must be provided together')
    out=Path(args.output).expanduser().absolute()
    if out.exists():
        raise SystemExit('OUTPUT_ALREADY_EXISTS: '+str(out))
    if args.processes<1 or not 1<=args.microbatch_size<=256:
        p.error('processes must be >=1 and microbatch-size must be 1..256')
    if args.case_id and (args.submission is None or args.elapsed_seconds is None):
        p.error('--case-id requires --submission and --elapsed-seconds')
    if args.submission_csv and (args.submission is not None or args.elapsed_seconds is not None):
        p.error('--submission-csv cannot take --submission/--elapsed-seconds')
    os.umask(0o077)
    # One numerical thread per requested process; --processes is the sole
    # explicit CPU parallelism control for the HF scoring interface.
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
        os.environ[name]='1'
    from saddlebench.matching.fast_reference import make_nonfo_cache
    if args.reference_root:
        private=Path(args.reference_root).expanduser().absolute()
        casefile=Path(args.cases_csv).expanduser().absolute()
    else:
        cache=Path(args.cache_dir).expanduser().absolute()
        for d in (cache,*cache.parents):
            if d.is_symlink():raise ValueError('SYMLINK_IN_REFERENCE_CACHE_PATH')
        cache.mkdir(parents=True,exist_ok=True,mode=0o700)
        cache.chmod(0o700)
        from huggingface_hub import snapshot_download,hf_hub_download
        if not args.no_progress:print('REFERENCE_DOWNLOAD=START',flush=True)
        try:
            private=Path(snapshot_download(repo_id='traingletrain/saddlebench-reference-private',
                repo_type='dataset',cache_dir=str(cache),token=False,
                allow_patterns=['private_manifest.json','reference/*','diagnostic_saddles.jsonl.gz','CHECKSUMS.json']))
            casefile=Path(hf_hub_download(repo_id='traingletrain/saddlebench-prototype',
                repo_type='dataset',filename='cases.csv',cache_dir=str(cache),token=False))
        except Exception as exc:
            raise SystemExit('PUBLIC_HF_REFERENCE_UNAVAILABLE: '+type(exc).__name__) from exc
    verify_reference(private,casefile)
    if args.mode in ('score','score_detailed'):
        # verify_reference checked all published bytes and registry above;
        # make_nonfo_cache independently repeats the gzip SHA-256 check.
        nonfo,_,_=make_nonfo_cache(private,Path(args.index_cache_dir)/'nonfo')
    else:
        nonfo=None
    result=run(casefile=casefile,private=private,case_id=args.case_id,
        submission=args.submission,elapsed_seconds=args.elapsed_seconds,
        submission_csv=args.submission_csv,mode=args.mode,
        processes=args.processes,microbatch_size=args.microbatch_size,
        index_cache=Path(args.index_cache_dir)/'group_indices',nonfo_cache=nonfo)
    # No silent file replacement. Publish result atomically only after scoring.
    if out.exists():
        raise SystemExit('OUTPUT_ALREADY_EXISTS: '+str(out))
    out.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    fd,name=tempfile.mkstemp(dir=out.parent,prefix='.saddlebench_pending_')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.link(name,out)
    finally:
        if os.path.exists(name):os.unlink(name)
    print('SCORE_VERIFY=PASS')
    print('MODE='+args.mode)
    print('PROCESSES='+str(min(args.processes,1 if args.case_id else len(result['case_results']))))
    print('OUTPUT='+str(out))
    from saddlebench.terminal_summary import render_summary
    print(render_summary(result, cases_csv=casefile, view=args.view))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
