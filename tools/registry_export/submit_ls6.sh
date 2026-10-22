#!/usr/bin/env bash
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd -P)"
ROOT=/scratch/11588/ndn454/sm_anskey
POINTER="${SBE_REGISTRY_POINTER:-$ROOT/pipeline/state/latest_canonical_registry_v1.txt}"
BASE="$ROOT/saddlebench_diagnostics"
WORKERS="${SBE_WORKERS:-4}"
if [[ ! "$WORKERS" =~ ^[0-9]+$ ]] || (( WORKERS < 1 || WORKERS > 16 )); then
  echo 'BLOCK: SBE_WORKERS must be 1..16' >&2; exit 2
fi
if [[ "$POINTER" != /* || -L "$POINTER" || ! -f "$POINTER" ]]; then
  echo 'BLOCK: missing/untrusted published registry pointer' >&2; exit 2
fi
# Pin the snapshot that was latest at submission; later publications do not
# silently alter or mix it with a different database.
REGISTRY="$(cat "$POINTER")"
if [[ "$REGISTRY" != /* || "$REGISTRY" == *$'\n'* || -L "$REGISTRY" || ! -f "$REGISTRY" ]]; then
  echo 'BLOCK: invalid or missing immutable published registry path' >&2; exit 2
fi
EXPECTED_SHA="$(sha256sum "$REGISTRY" | awk '{print $1}')"
if [[ "$(cat "$POINTER")" != "$REGISTRY" ]]; then
  echo 'BLOCK: publication pointer changed during submission preflight' >&2; exit 2
fi
mkdir -p "$BASE"
RUN_DIR="$(mktemp -d -p "$BASE" sbe_parallel_export_rc14_XXXXXXXX)"
printf 'REGISTRY=%s\nSHA256=%s\nPOINTER_AT_SUBMIT=%s\nWORKERS=%s\n' \
  "$REGISTRY" "$EXPECTED_SHA" "$POINTER" "$WORKERS" \
  > "$RUN_DIR/source_at_submit.txt"
mkdir -p "$RUN_DIR/tmp"
if ! OUTPUT="$(sbatch --export="ALL,SBE_RUN_DIR=$RUN_DIR,SBE_PACKAGE_ROOT=$HERE,SBE_SOURCE_REGISTRY=$REGISTRY,SBE_SOURCE_SHA=$EXPECTED_SHA,SBE_WORKERS=$WORKERS" \
  --output="$RUN_DIR/slurm-%j.out" --error="$RUN_DIR/slurm-%j.err" \
  "$HERE/run_vm_small.sbatch" 2>&1)"; then
  printf '%s\n' "$OUTPUT" >&2
  echo 'SUBMISSION_FAILED; new diagnostic workspace remains untouched' >&2
  exit 2
fi
printf '%s\n' "$OUTPUT"
JOB_ID="$(printf '%s\n' "$OUTPUT" | sed -n 's/^Submitted batch job \([0-9][0-9]*\)$/\1/p' | tail -n 1)"
if [[ -z "$JOB_ID" ]]; then
  echo 'BLOCK: sbatch returned no recognizable job ID; check squeue; do NOT blindly resubmit' >&2
  exit 2
fi
printf 'JOB_ID=%s\nRESULT_DIR=%s\n' "$JOB_ID" "$RUN_DIR"
printf 'CHECK_COMMAND=sacct -j %s -o JobID,State,Elapsed,ExitCode\n' "$JOB_ID"
printf 'UPLOAD_HINT=find %s -maxdepth 1 -name "sbe_export_report_*.tar.gz" -print\n' "$RUN_DIR"
