#!/usr/bin/env bash
# Score the two existing Sella trajectories on Saskatoon's CPU partition.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PY=/home/ndn454/fairchem_audit_222_223/venvs/fairchem222/bin/python
BASE=/home/ndn454/saddlemill_jobs
INPUT_ROOT="$BASE/saddlebench_signature_case_inputs_20261024_v2"
TRAJ6="${SADDLEBENCH_TRAJECTORY_0006:-$INPUT_ROOT/case0006_predicted_saddles.traj}"
TRAJ22="${SADDLEBENCH_TRAJECTORY_0022:-$INPUT_ROOT/case0022_predicted_saddles.traj}"
LATEST="$BASE/.saddlebench_prepared_skew_scoring_latest"
MODE="${1:---run}"
case "$MODE" in --plan|--run|--status) ;; *) echo 'Usage: bash scripts/score_saskatoon_cases.sh [--plan|--run|--status]' >&2;exit 2;; esac
if [[ "$MODE" == --status ]]; then
  [[ -s "$LATEST" ]] || { echo 'NO_SCORING_JOB_SUBMITTED';exit 1; }
  OUT="$(cat "$LATEST")"
  echo "SCORE_OUTPUT_DIR=$OUT"
  if [[ -s "$OUT/job_id.txt" ]]; then
    JOB="$(cat "$OUT/job_id.txt")"
    echo "SCORE_JOB_ID=$JOB"
    squeue -j "$JOB" -o '%.18i %.12T %.12M %.25R' || true
    sacct -X -j "$JOB" -o JobID,State,Elapsed,ExitCode -P || true
    [[ ! -f "$OUT/slurm-$JOB.out" ]] || tail -n 35 "$OUT/slurm-$JOB.out"
    [[ ! -f "$OUT/slurm-$JOB.err" ]] || tail -n 20 "$OUT/slurm-$JOB.err"
  fi
  if [[ -s "$OUT/exit_status.txt" ]]; then echo "SCORE_EXIT=$(cat "$OUT/exit_status.txt")"; fi
  if [[ -s "$OUT/scores.json" ]]; then echo "SCORE_JSON=$OUT/scores.json"; fi
  if [[ -s "$OUT.tar.gz" ]]; then echo "UPLOAD_THIS_FILE=$OUT.tar.gz"; fi
  exit 0
fi
[[ "$(hostname -s)" == saskatoon ]] || { echo 'WRONG_HOST: run on Saskatoon' >&2;exit 2; }
[[ -x "$PY" ]] || { echo "MISSING_PYTHON=$PY" >&2;exit 2; }
for f in "$TRAJ6" "$TRAJ22";do
  [[ -s "$f" && ! -L "$f" ]] || { echo "MISSING_LOCAL_TRAJECTORY=$f" >&2;exit 2; }
done
# Preserve the installed lab venv path without symlink resolution.
ROOT_EXPECTED="$ROOT" PYTHONPATH="$ROOT" "$PY" - <<'PY'
import os, sys
from pathlib import Path
from importlib.metadata import version
assert Path(sys.prefix)==Path('/home/ndn454/fairchem_audit_222_223/venvs/fairchem222'),sys.prefix
assert version('fairchem-core')=='2.22.0'
import numpy,scipy,ase,huggingface_hub
from saddlebench.matching import geometry,batch_exact,group_radius_exists,pair_signature
root=Path(os.environ['ROOT_EXPECTED'])
assert all(root in Path(x.__file__).absolute().parents for x in (geometry,batch_exact,group_radius_exists,pair_signature))
assert hasattr(geometry,'_prepare_skew_mic')
print('SASKATOON_SCORER_PREFLIGHT=PASS',sys.executable,'ase',ase.__version__,flush=True)
PY
[[ -f "$ROOT/scripts/score_saskatoon_cases.sbatch" ]] || { echo 'MISSING_JOB_SCRIPT';exit 2; }
if [[ -n "${SADDLEBENCH_REFERENCE_ROOT:-}" || -n "${SADDLEBENCH_CASES_CSV:-}" ]]; then
  [[ -d "${SADDLEBENCH_REFERENCE_ROOT:-}" && -f "${SADDLEBENCH_CASES_CSV:-}" ]] || { echo 'OFFLINE_REFERENCE_REQUIRES_BOTH_VALID_PATHS';exit 2; }
fi
printf 'SOURCE=%s\nCASE0006=%s\nCASE0022=%s\nPARTITION=localhost CPU=4 RAM=24G GPUs=0\nSIGNATURE_FILTER=auto\n' "$ROOT" "$TRAJ6" "$TRAJ22"
if [[ "$MODE" == --plan ]];then echo 'SCORE_PLAN=PASS';exit 0;fi
mkdir -p "$BASE"
OUT="$(mktemp -d "$BASE/saddlebench_skew_score_20261024_XXXXXXXX")"
printf '%s\n' "$OUT" > "$LATEST"
cat > "$OUT/submissions.csv" <<CSV
model,case_id,trajectory,elapsed_seconds
Sella-SEL00M24,case0006,$TRAJ6,532
Sella-SEL00M24,case0022,$TRAJ22,1114
CSV
RES="$(sbatch --output="$OUT/slurm-%j.out" --error="$OUT/slurm-%j.err" "$ROOT/scripts/score_saskatoon_cases.sbatch" "$ROOT" "$OUT")"
printf '%s\n' "$RES"
JOB="$(printf '%s\n' "$RES" | sed -n 's/^Submitted batch job \([0-9][0-9]*\)$/\1/p' | tail -n 1)"
[[ -n "$JOB" ]] || { echo 'SBATCH_SUBMISSION_UNVERIFIED';exit 2; }
printf '%s\n' "$JOB" > "$OUT/job_id.txt"
echo "SCORE_JOB_ID=$JOB"
echo "STATUS_COMMAND=bash '$ROOT/scripts/score_saskatoon_cases.sh' --status"
