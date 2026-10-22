#!/usr/bin/env bash
# Run the unchanged RC14 Slurm worker as ordinary Bash inside an existing vm-small allocation.
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd -P)"
ROOT=/scratch/11588/ndn454/sm_anskey
POINTER="${SBE_REGISTRY_POINTER:-$ROOT/pipeline/state/latest_canonical_registry_v1.txt}"
BASE="${SBE_OUTPUT_BASE:-$ROOT/saddlebench_diagnostics}"
WORKERS="${SBE_WORKERS:-4}"
PREFLIGHT_ONLY=0
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != '--preflight-only' ) ]]; then
  echo 'USAGE: bash run_direct_vm_small.sh [--preflight-only]' >&2
  exit 2
fi
if [[ "${1:-}" == '--preflight-only' ]]; then PREFLIGHT_ONLY=1; fi

if [[ ! "$WORKERS" =~ ^[0-9]+$ ]] || (( WORKERS < 1 || WORKERS > 16 )); then
  echo 'BLOCK: SBE_WORKERS must be an integer from 1 to 16' >&2
  exit 2
fi
HOST_SHORT="$(hostname -s)"
case "$HOST_SHORT" in
  login*|*login*) echo "BLOCK: REFUSE_LOGIN_NODE=$HOST_SHORT; enter your active vm-small allocation first" >&2; exit 2 ;;
esac

ALLOCATION_CHECK=''
if [[ "${SLURM_JOB_PARTITION:-}" == 'vm-small' && -n "${SLURM_JOB_ID:-}" ]]; then
  ALLOCATION_CHECK='SLURM_ENVIRONMENT'
else
  if ! command -v squeue >/dev/null 2>&1; then
    echo 'BLOCK: no Slurm allocation environment and squeue unavailable' >&2
    exit 2
  fi
  # Supports SSH into the node of an allocation: SSH may not preserve SLURM_*.
  if ! PARTITIONS="$(squeue -h -u "$(id -un)" -w "$HOST_SHORT" -t RUNNING -o '%P' 2>/dev/null)"; then
    echo 'BLOCK: cannot verify own active vm-small allocation on this host' >&2
    exit 2
  fi
  if ! printf '%s\n' "$PARTITIONS" | grep -Eq '^vm-small\*?$'; then
    echo "BLOCK: no running vm-small allocation for $(id -un) on $HOST_SHORT" >&2
    exit 2
  fi
  ALLOCATION_CHECK='SLURM_SQUEUE_RUNNING_ALLOCATION'
fi

# GNU nproc is additionally limited by OMP_NUM_THREADS/OMP_THREAD_LIMIT.
# Those govern threads per process, NOT the number of independent export
# processes. Query affinity/cgroup CPU availability without those limits.
# The worker deliberately resets OMP_NUM_THREADS=1 for NumPy/BLAS later.
AVAILABLE_CPUS="$(env -u OMP_NUM_THREADS -u OMP_THREAD_LIMIT nproc 2>/dev/null || echo 0)"
if [[ ! "$AVAILABLE_CPUS" =~ ^[0-9]+$ ]] || (( AVAILABLE_CPUS < WORKERS )); then
  echo "BLOCK: available CPUs=$AVAILABLE_CPUS below requested workers=$WORKERS (OpenMP limits excluded)" >&2
  exit 2
fi
if [[ -n "${SLURM_CPUS_PER_TASK:-}" ]] && (( SLURM_CPUS_PER_TASK < WORKERS )); then
  echo "BLOCK: allocation CPUs/task=$SLURM_CPUS_PER_TASK below requested workers=$WORKERS" >&2
  exit 2
fi

if [[ "$POINTER" != /* || -L "$POINTER" || ! -f "$POINTER" ]]; then
  echo 'BLOCK: missing/untrusted published registry pointer' >&2
  exit 2
fi
REGISTRY="$(cat "$POINTER")"
if [[ "$REGISTRY" != /* || "$REGISTRY" == *$'\n'* || -L "$REGISTRY" || ! -f "$REGISTRY" ]]; then
  echo 'BLOCK: invalid/missing published registry snapshot' >&2
  exit 2
fi
EXPECTED_SHA="$(sha256sum "$REGISTRY" | awk '{print $1}')"
if [[ "$(cat "$POINTER")" != "$REGISTRY" ]]; then
  echo 'BLOCK: published registry pointer moved during preflight' >&2
  exit 2
fi
if [[ ! -f "$HERE/run_vm_small.sbatch" || ! -f "$HERE/payload/export_published_registry.py" ]]; then
  echo 'BLOCK: RC14 package missing its worker or exporter' >&2
  exit 2
fi
printf 'DIRECT_VM_SMALL_PREFLIGHT=PASS\nHOST=%s\nALLOCATION_VERIFIED_BY=%s\nREGISTRY=%s\nREGISTRY_SHA256=%s\nWORKERS=%s\n' \
  "$HOST_SHORT" "$ALLOCATION_CHECK" "$REGISTRY" "$EXPECTED_SHA" "$WORKERS"
if ((PREFLIGHT_ONLY)); then exit 0; fi

mkdir -p "$BASE"
RUN_DIR="$(mktemp -d -p "$BASE" sbe_parallel_export_rc14_direct_XXXXXXXX)"
mkdir -p "$RUN_DIR/tmp"
printf 'REGISTRY=%s\nSHA256=%s\nPOINTER_AT_START=%s\nWORKERS=%s\nMODE=DIRECT_VM_SMALL\n' \
  "$REGISTRY" "$EXPECTED_SHA" "$POINTER" "$WORKERS" > "$RUN_DIR/source_at_submit.txt"
export SBE_RUN_DIR="$RUN_DIR" SBE_PACKAGE_ROOT="$HERE" SBE_SOURCE_REGISTRY="$REGISTRY"
export SBE_SOURCE_SHA="$EXPECTED_SHA" SBE_WORKERS="$WORKERS"
printf 'RESULT_DIR=%s\nRUN_LOG=%s\n' "$RUN_DIR" "$RUN_DIR/direct_run.log"
# A #SBATCH header is a Bash comment outside sbatch. Reuse the identical,
# previously validated exporter, SHA256 checks, constraint audit and summary trap.
# tee preserves live terminal output, while pipefail preserves worker exit code.
bash "$HERE/run_vm_small.sbatch" 2>&1 | tee "$RUN_DIR/direct_run.log"
printf 'DIRECT_VM_SMALL_EXPORT=PASS\nRESULT_DIR=%s\n' "$RUN_DIR"
