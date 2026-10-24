#!/usr/bin/env bash
# Read-only source registry and .traj; single SQLite writer in /work. No sbatch.
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")/.." && pwd -P)"
ROOT=/scratch/11588/ndn454/sm_anskey
PY=/work/11588/ndn454/ls6/conda_libraries/saddlemill/bin/python
OVERLAY=/work/11588/ndn454/ls6/conda_libraries/saddlemill_vm_numpy
POINTER="${SBE_REGISTRY_POINTER:-$ROOT/pipeline/state/latest_canonical_registry_v1.txt}"
ARCHIVE_BASE="${SBE_ARCHIVE_BASE:-/work/11588/ndn454/ls6/saddlebench_reaction_archives}"
SOURCE_RUN="${SBE_SOURCE_RUN:-$ROOT/saddlebench_diagnostics/saddlebench_lean_z2_j9rmh}"
OUT_BASE="${SBE_OUTPUT_BASE:-$ROOT/saddlebench_diagnostics}"
MODE="${1:---run}"
case "$MODE" in --run|--import-only|--export-only|--status) ;; *) echo 'Usage: bash run_on_idev.sh [--run|--import-only|--export-only|--status]' >&2; exit 2;; esac
case "$(hostname -s)" in login*|*login*) echo 'BLOCK: allocated compute node required' >&2; exit 2;; esac
if [[ "${SLURM_JOB_PARTITION:-}" != 'vm-small' || -z "${SLURM_JOB_ID:-}" ]]; then
  if ! command -v squeue >/dev/null || ! squeue -h -u "$(id -un)" -w "$(hostname -s)" -t RUNNING -o '%P' | grep -Eq '^vm-small\*?$'; then
    echo 'BLOCK: no verified user-owned vm-small allocation' >&2; exit 2
  fi
fi
[[ -x "$PY" && -d "$OVERLAY" ]] || { echo 'BLOCK: authoritative Python/NumPy overlay missing' >&2; exit 2; }
export PYTHONNOUSERSITE=1
export PYTHONPATH="$OVERLAY${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
"$PY" - <<'PY'
import numpy,ase
assert 'saddlemill_vm_numpy' in str(numpy.__file__), numpy.__file__
from ase.io.trajectory import Trajectory
print('PYTHON_PREFLIGHT=PASS',flush=True)
print('NUMPY='+str(numpy.__file__),flush=True)
PY
REGISTRY=''
if [[ -f "$POINTER" ]]; then REGISTRY="$(cat "$POINTER")"; fi
if [[ "$MODE" != '--export-only' && "$MODE" != '--status' ]]; then
  [[ "$REGISTRY" == /* && -f "$REGISTRY" && ! -L "$REGISTRY" ]] || { echo 'BLOCK: pinned registry missing or invalid' >&2; exit 2; }
  if [[ "$MODE" == '--run' && "${SBE_ALLOW_CONCURRENT:-0}" != 1 ]] && ps -u "$(id -un)" -o args= | grep -E '[e]xport_lean\.py' >/dev/null; then
    echo 'BLOCK: original lean exporter appears active; run --import-only first, or stop the original after preserving its files. No processes were killed.' >&2
    exit 2
  fi
fi
mkdir -p "$ARCHIVE_BASE" "$OUT_BASE"
if [[ -n "${SBE_SQLITE:-}" ]]; then
  DB="$SBE_SQLITE"
elif [[ -n "$REGISTRY" && -f "$REGISTRY" ]]; then
  HASH="$($PY - "$HERE" "$REGISTRY" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from reaction_archive import registry_signature
print(registry_signature(Path(sys.argv[2]))['identity'][:24])
PY
)"
  DB="$ARCHIVE_BASE/reaction_archive_${HASH}.sqlite"
else
  echo 'BLOCK: export-only/status without registry requires SBE_SQLITE=/work/.../reaction_archive_...sqlite' >&2
  exit 2
fi
ARGS=(--sqlite "$DB")
if [[ -n "$REGISTRY" && -f "$REGISTRY" ]]; then ARGS+=(--registry "$REGISTRY"); fi
if [[ -d "$SOURCE_RUN" && "$MODE" != '--export-only' && "$MODE" != '--status' ]]; then ARGS+=(--source-lean-run "$SOURCE_RUN"); fi
if [[ "$MODE" == '--import-only' ]]; then ARGS+=(--import-only);
elif [[ "$MODE" == '--export-only' ]]; then ARGS+=(--export-only);
elif [[ "$MODE" == '--status' ]]; then ARGS+=(--status); fi
if [[ "$MODE" == '--run' || "$MODE" == '--export-only' ]]; then
  if [[ "${SBE_EXPORT_JSONL:-1}" == 1 ]]; then
    OUT="$OUT_BASE/saddlebench_sqlite_export_$(date +%Y%m%d_%H%M%S)_$$_v1"
    ARGS+=(--export-to "$OUT")
    echo "JSONL_OUTPUT=$OUT"
  fi
fi
if [[ -n "${SBE_BACKUP_TO:-}" ]]; then ARGS+=(--backup-to "$SBE_BACKUP_TO"); fi
if [[ "${SBE_STRICT_ASE:-0}" == 1 ]]; then ARGS+=(--strict-ase); fi
ARGS+=(--workers "${SBE_WORKERS:-3}" --progress-every "${SBE_PROGRESS_EVERY:-1000}")
LOG="$OUT_BASE/reaction_archive_$(date +%Y%m%d_%H%M%S)_$$_v1.log"
echo "ARCHIVE=$DB"
echo "LOG=$LOG"
echo "MODE=$MODE"
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" "$PY" -u -m tools.reaction_archive.reaction_archive "${ARGS[@]}" 2>&1 | tee -a "$LOG"
