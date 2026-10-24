#!/usr/bin/env bash
#SBATCH --job-name=saddlebench_skew_score
#SBATCH --partition=localhost
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:45:00
set -euo pipefail
ROOT="$1"
OUT="$2"
PY=/home/ndn454/fairchem_audit_222_223/venvs/fairchem222/bin/python
export PYTHONNOUSERSITE=1
export OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PATH="$PATH:/usr/sbin"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SADDLEBENCH_SIGNATURE_FILTER=auto
export SADDLEBENCH_SIGNATURE_TRACE=1
CACHE="$HOME/.cache/saddlebench_first_hit_index"
finish() {
  status=$?
  trap - EXIT
  echo "$status" > "$OUT/exit_status.txt"
  tar -czf "$OUT.tar.gz" -C "$(dirname "$OUT")" "$(basename "$OUT")" || echo 'SCORE_ARCHIVE=FAIL'
  [[ ! -s "$OUT.tar.gz" ]] || echo "UPLOAD_THIS_FILE=$OUT.tar.gz"
  echo "SCORE_JOB_EXIT=$status"
}
trap finish EXIT
[[ "$(hostname -s)" == saskatoon ]] || { echo 'WRONG_EXECUTION_HOST'; exit 2; }
[[ -d "$OUT" && ! -L "$OUT" && -r "$OUT/submissions.csv" ]] || { echo 'MISSING_INPUTS';exit 2; }
# Revalidate inside Slurm: environment and *actual* scored module paths.
ROOT_EXPECTED="$ROOT" "$PY" - <<'PY'
import os,sys
from pathlib import Path
from importlib.metadata import version
assert Path(sys.prefix)==Path('/home/ndn454/fairchem_audit_222_223/venvs/fairchem222')
assert version('fairchem-core')=='2.22.0'
import numpy,scipy,ase,huggingface_hub
from saddlebench.matching import geometry,batch_exact,group_radius_exists,pair_signature
root=Path(os.environ['ROOT_EXPECTED'])
assert all(root in Path(x.__file__).absolute().parents for x in (geometry,batch_exact,group_radius_exists,pair_signature))
print('COMPUTE_PREFLIGHT=PASS',flush=True)
PY
# Fail closed if the shipped scorer has been modified after release packaging.
if [[ -f "$(dirname "$ROOT")/SOURCE_SHA256SUMS.txt" ]]; then
  (cd "$ROOT" && sha256sum -c "$(dirname "$ROOT")/SOURCE_SHA256SUMS.txt" >/dev/null) || { echo 'SOURCE_HASH_MISMATCH';exit 2; }
fi
# Store temp and pytest fixture directories in a traversable private home path.
export TMPDIR="$OUT/private_tmp"
mkdir -m 700 -p "$TMPDIR"
"$PY" -m pytest -q --basetemp "$OUT/pytest_private" \
  "$ROOT/tests/test_prepared_skew_context_20261024.py" \
  "$ROOT/tests/test_unified_prepared_batch.py" \
  "$ROOT/tests/test_pair_signature_persistent.py" > "$OUT/focused_tests.log" 2>&1 || { tail -n 35 "$OUT/focused_tests.log";exit 1; }
tail -n 2 "$OUT/focused_tests.log"
mkdir -p "$CACHE"
[[ ! -L "$CACHE" ]] || { echo 'CACHE_SYMLINK_NOT_ALLOWED';exit 2; }
EXTRA=()
if [[ -n "${SADDLEBENCH_REFERENCE_ROOT:-}" && -n "${SADDLEBENCH_CASES_CSV:-}" ]];then
 EXTRA=(--reference-root "$SADDLEBENCH_REFERENCE_ROOT" --cases-csv "$SADDLEBENCH_CASES_CSV")
fi
START="$(date +%s%N)"
"$PY" -m saddlebench score --submission-csv "$OUT/submissions.csv" \
    --mode score --view case --processes 2 --microbatch-size 16 \
    --index-cache-dir "$CACHE" "${EXTRA[@]}" --output "$OUT/scores.json" \
    > "$OUT/scorer.log" 2>&1 || { code=$?;tail -n 50 "$OUT/scorer.log";exit "$code"; }
END="$(date +%s%N)"
export SCORE_ELAPSED_NS="$((END-START))" SCORE_DIR="$OUT"
"$PY" - <<'PY' | tee "$OUT/summary.log"
import json,os
from pathlib import Path
from saddlebench.matching.geometry import _prepare_skew_mic
out=Path(os.environ['SCORE_DIR'])
d=json.loads((out/'scores.json').read_text())
t=float(os.environ['SCORE_ELAPSED_NS'])/1e9
print('SCORING_END_TO_END_SECONDS=%.6f'%t)
print('SCORING_PREDICTIONS=%s'%sum(len(x.get('entries',[])) for x in d['case_results']))
for case in d['case_results']:
 print('CASE=%s K=%s U=%s SCORE_PER_HOUR=%s MATCHING_SECONDS=%s'%(
  case['case_id'],case.get('known_eligible_reactions_K'),case.get('greedy_unique_reactions_U'),
  case.get('provisional_score_per_hour'),case.get('matching_only_seconds')))
old=Path('/home/ndn454/saddlemill_jobs/saddlebench_case0006_0022_signature_20261024_47oYJsVQ/signature_warm.json')
if old.exists():
 from sys import path
 current=json.loads(old.read_text())
 ignored={'matching_only_seconds','work_counts','cached_preparation','stage_work_counts','stage_preparation'}
 def science(v):
  if isinstance(v,dict):return {k:science(x) for k,x in v.items() if k not in ignored}
  if isinstance(v,list):return [science(x) for x in v]
  return v
 ok=science(current)==science(d)
 print('SCIENTIFIC_PARITY='+('PASS' if ok else 'FAIL'))
 if not ok:raise SystemExit('SCIENTIFIC_OUTPUT_CHANGED')
else:print('SCIENTIFIC_PARITY=NOT_CHECKED_PREVIOUS_RESULTS_UNAVAILABLE')
print('SCORING_COMPLETE=PASS')
PY
