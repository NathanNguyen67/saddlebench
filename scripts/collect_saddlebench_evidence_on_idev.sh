#!/usr/bin/env bash
# SaddleBench evidence collector v2 (2026-10-24).
# Read-only CPU benchmark only. Deliberately NEVER opens the potentially enormous
# reaction SQLite on /work: the v1 full PRAGMA integrity_check could stall for hours.
# Intended for an existing LS6 vm-small interactive allocation. No submissions.
set -Eeuo pipefail
umask 077

HERE="$(cd "$(dirname "$0")/.." && pwd -P)"
case "$(hostname -s)" in
  login*|*login*) echo 'BLOCK: run inside an existing vm-small allocation, not login' >&2; exit 2 ;;
esac
if [[ "${SLURM_JOB_PARTITION:-}" != 'vm-small' || -z "${SLURM_JOB_ID:-}" ]]; then
  if ! command -v squeue >/dev/null || ! squeue -h -u "$(id -un)" -w "$(hostname -s)" -t RUNNING -o '%P' | grep -Eq '^vm-small\*?$'; then
    echo 'BLOCK: no verified active vm-small allocation' >&2
    exit 2
  fi
fi

BASE="${SBE_EVIDENCE_BASE:-/scratch/11588/ndn454/sm_anskey/saddlebench_diagnostics}"
REPEATS="${SBE_REPEATS:-1}"
TIME_LIMIT="${SBE_BENCHMARK_TIMEOUT_SECONDS:-300}"
[[ "$REPEATS" =~ ^[1-9]([0-9]*)$ && "$REPEATS" -le 3 ]] || {
  echo 'BLOCK: SBE_REPEATS must be 1..3' >&2; exit 2;
}
[[ "$TIME_LIMIT" =~ ^[1-9][0-9]*$ && "$TIME_LIMIT" -le 1800 ]] || {
  echo 'BLOCK: SBE_BENCHMARK_TIMEOUT_SECONDS must be 1..1800' >&2; exit 2;
}
[[ -f "$HERE/benchmarks/pt343_maxdev/benchmark_prepared.py" ]] || {
  echo "BLOCK: SaddleBench development source not found beneath $HERE" >&2; exit 2;
}
command -v timeout >/dev/null || { echo 'BLOCK: GNU timeout is required' >&2; exit 2; }
mkdir -p "$BASE"
DIR="$BASE/saddlebench_dev_evidence_v2_$(date +%Y%m%d_%H%M%S)_$$"
mkdir -m 700 "$DIR"
DB="${SBE_SQLITE:-/work/11588/ndn454/ls6/saddlebench_reaction_archives/reaction_archive_28098e06323645bc1dca2210.sqlite}"

{
  echo "COLLECTOR_VERSION=2"
  echo "COLLECT_TIME=$(date -Is)"
  echo "HOST=$(hostname -s)"
  echo "JOB_ID=${SLURM_JOB_ID:-none}"
  echo "PARTITION=${SLURM_JOB_PARTITION:-none}"
  echo "ARCHIVE_PATH_UNTOUCHED=$DB"
  echo 'SQLITE_STATUS=NOT_CHECKED_BY_DESIGN'
  echo 'REASON=avoid full integrity_check and possible uninterruptible network I/O on /work'
  echo "SOURCE=$HERE"
  echo "REPEATS=$REPEATS"
  echo "BENCHMARK_TIMEOUT_SECONDS=$TIME_LIMIT"
  sha256sum "$HERE/saddlebench/matching/geometry.py" \
    "$HERE/tools/reaction_archive/reaction_archive.py" \
    "$HERE/tools/reaction_archive/fast_frames.py"
} > "$DIR/identity.txt"

PACKAGED=0
finish() {
  local rc=$?
  trap - EXIT INT TERM
  set +e
  echo "COLLECTOR_EXIT=$rc" >> "$DIR/identity.txt"
  if [[ "$PACKAGED" == 0 ]]; then
    local dest="$BASE/$(basename "$DIR").tar.gz"
    if tar -czf "$dest" -C "$BASE" "$(basename "$DIR")"; then
      echo "UPLOAD_THIS_FILE=$dest"
    else
      echo "EVIDENCE_DIRECTORY=$DIR"
      echo 'WARNING: evidence tarball could not be written; original logs are preserved.' >&2
    fi
  fi
  return "$rc"
}
trap finish EXIT
trap 'echo "INTERRUPTED_BY_USER" >&2; exit 130' INT
trap 'echo "TERMINATED_BY_SIGNAL" >&2; exit 143' TERM

echo "COLLECTOR_VERSION=2"
echo "EVIDENCE_DIRECTORY=$DIR"
echo 'PHASE=SQLITE_CHECK_SKIPPED (no /work archive opened)'
echo "PHASE=BENCHMARK_START repeats=$REPEATS timeout_seconds=$TIME_LIMIT"

# Use the validated launcher unchanged; SBE_REPEATS=1 cuts run time to 1/3 of v1.
# Run with a hard wall clock guard so the user receives the captured logs even if
# this CPU microbenchmark is unexpectedly slow. A kernel stuck in D-state may
# still resist signals; no timeout can guarantee cancellation of uninterruptible I/O.
set +e
SBE_REPEATS="$REPEATS" \
SBE_PREPARED_RESULT="$DIR/real_pt343_benchmark.json" \
timeout -s TERM -k 10s "${TIME_LIMIT}s" \
  bash "$HERE/benchmarks/pt343_maxdev/run_prepared_on_ls6_vm_small.sh" \
  2>&1 | tee "$DIR/benchmark.log"
BENCH_RC=${PIPESTATUS[0]}
set -e

if [[ "$BENCH_RC" -eq 0 ]]; then
  echo 'BENCHMARK_COMMAND=PASS' | tee -a "$DIR/identity.txt"
  echo 'PHASE=BENCHMARK_COMPLETE'
else
  echo "BENCHMARK_COMMAND=FAIL_EXIT_$BENCH_RC" | tee -a "$DIR/identity.txt"
  if [[ "$BENCH_RC" -eq 124 || "$BENCH_RC" -eq 137 ]]; then
    echo "BENCHMARK_TIMED_OUT=YES" | tee -a "$DIR/identity.txt"
    echo 'PHASE=BENCHMARK_TIMED_OUT; captured diagnostic logs will be packaged' >&2
  else
    echo "PHASE=BENCHMARK_FAILED code=$BENCH_RC; captured diagnostic logs will be packaged" >&2
  fi
fi
# Do not mask the benchmark failure with a successful tarball creation.
exit "$BENCH_RC"
