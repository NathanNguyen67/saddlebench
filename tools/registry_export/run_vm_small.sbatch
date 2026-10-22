#!/usr/bin/env bash
#SBATCH -J sbe-export-par
#SBATCH -A CHE21006
#SBATCH -p vm-small
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=16
#SBATCH --time=04:00:00
set -euo pipefail
umask 077
: "${SBE_RUN_DIR:?missing output workspace}"
: "${SBE_PACKAGE_ROOT:?missing RC14 package directory}"
: "${SBE_SOURCE_REGISTRY:?missing snapshot path}"
: "${SBE_SOURCE_SHA:?missing snapshot SHA256}"
: "${SBE_WORKERS:?missing worker count}"
PY=/work/11588/ndn454/ls6/conda_libraries/saddlemill/bin/python
OVERLAY=/work/11588/ndn454/ls6/conda_libraries/saddlemill_vm_numpy
OUT="$SBE_RUN_DIR/evidence_unqualified"
export PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
if [[ -n "${SLURM_TMPDIR:-}" && -d "$SLURM_TMPDIR" && -w "$SLURM_TMPDIR" ]]; then
  export TMPDIR="$SLURM_TMPDIR"
else
  export TMPDIR="$SBE_RUN_DIR/tmp"
  mkdir -p "$TMPDIR"
fi
if type module >/dev/null 2>&1; then
  module reset >/dev/null 2>&1 || true
  module unload impi python3 >/dev/null 2>&1 || true
  module load gcc >/dev/null 2>&1 || true
fi
export PYTHONPATH="$OVERLAY${PYTHONPATH:+:$PYTHONPATH}"
MAIN_STATUS=BLOCKED
REASON=NOT_STARTED
EXPORT_RC=99
VALIDATION_RC=99
STARTED_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finish() {
  local rc=$?
  trap - EXIT
  set +e
  export MAIN_STATUS REASON EXPORT_RC VALIDATION_RC STARTED_UTC
  "$PY" "$SBE_PACKAGE_ROOT/summarize.py" \
    --run-dir "$SBE_RUN_DIR" --registry "$SBE_SOURCE_REGISTRY" \
    --out "$OUT" --exit-code "$rc" \
    > "$SBE_RUN_DIR/summary_stdout.log" 2> "$SBE_RUN_DIR/summary_stderr.log"
  cat "$SBE_RUN_DIR/summary_stdout.log" || true
  echo "EXPORT_STATUS=$MAIN_STATUS REASON=$REASON"
  echo "RESULT_DIR=$SBE_RUN_DIR"
  exit "$rc"
}
trap finish EXIT
if [[ ! -x "$PY" || ! -d "$OVERLAY" ]]; then REASON=AUTHORITATIVE_ENV_MISSING; exit 2; fi
if ! "$PY" - <<'PY' > "$SBE_RUN_DIR/import_preflight.log" 2>&1
import numpy, ase, sqlite3
from pathlib import Path
actual=Path(numpy.__file__).resolve()
overlay=Path('/work/11588/ndn454/ls6/conda_libraries/saddlemill_vm_numpy').resolve()
print('numpy:',actual)
print('ase:',ase.__file__)
if overlay not in actual.parents: raise SystemExit('NUMPY_NOT_FROM_VM_OVERLAY')
PY
then REASON=ENV_IMPORT_PREFLIGHT_FAILED; exit 2; fi
if [[ ! -f "$SBE_SOURCE_REGISTRY" || -L "$SBE_SOURCE_REGISTRY" ]]; then REASON=PINNED_REGISTRY_MISSING; exit 2; fi
if [[ "$(sha256sum "$SBE_SOURCE_REGISTRY" | awk '{print $1}')" != "$SBE_SOURCE_SHA" ]]; then
  REASON=PINNED_REGISTRY_SHA_MISMATCH; exit 2
fi
printf 'PINNED_REGISTRY=%s\nSHA256=%s\nWORKERS=%s\n' \
  "$SBE_SOURCE_REGISTRY" "$SBE_SOURCE_SHA" "$SBE_WORKERS"
set +e
"$PY" "$SBE_PACKAGE_ROOT/payload/export_published_registry.py" \
  --registry "$SBE_SOURCE_REGISTRY" --out-dir "$OUT" --workers "$SBE_WORKERS" \
  > "$SBE_RUN_DIR/export_stdout.log" 2> "$SBE_RUN_DIR/export_stderr.log"
EXPORT_RC=$?
set -e
if ((EXPORT_RC != 0)); then REASON=PARALLEL_EXPORT_FAILED; exit "$EXPORT_RC"; fi
if [[ ! -f "$OUT/manifest.json" || ! -f "$OUT/SHA256SUMS.txt" ]]; then REASON=MISSING_MANIFEST; exit 2; fi
set +e
(cd "$OUT" && sha256sum -c SHA256SUMS.txt) > "$SBE_RUN_DIR/sha256_validation.log" 2>&1
VALIDATION_RC=$?
set -e
if ((VALIDATION_RC != 0)); then REASON=OUTPUT_HASH_VALIDATION_FAILED; exit 2; fi
if ! "$PY" - "$OUT/manifest.json" "$SBE_SOURCE_SHA" <<'PY' > "$SBE_RUN_DIR/manifest_validation.log" 2>&1
import json,sys
m=json.load(open(sys.argv[1]))
if m['schema_version']!='saddlemill-evidence-v1':raise SystemExit('SCHEMA_CHANGED')
if m['registry']['sha256']!=sys.argv[2]:raise SystemExit('SOURCE_SHA_CHANGED')
if not m['completeness']['registry_rows_enumerated']:raise SystemExit('NOT_FULLY_ENUMERATED')
for fn,n in sorted(m['record_counts'].items()): print(fn,n)
print('PUBLISHED_WATERMARK='+str(m['registry']['published_through']))
PY
then REASON=MANIFEST_VALIDATION_FAILED; exit 2; fi
if ! "$PY" "$SBE_PACKAGE_ROOT/audit_constraints.py" --evidence "$OUT" \
   --out "$SBE_RUN_DIR/constraint_audit.json" > "$SBE_RUN_DIR/constraint_audit.log" 2>&1; then
  REASON=CONSTRAINT_AUDIT_FAILED; exit 2
fi
# Additive export only: never alter the six RC14 evidence files.
# This is an unqualified data release candidate, NOT HF publication.
if ! "$PY" "$SBE_PACKAGE_ROOT/enrich_scientific.py" \
     --evidence "$OUT" --out "$SBE_RUN_DIR/scientific_dataset_unqualified" \
     > "$SBE_RUN_DIR/science_stdout.log" 2> "$SBE_RUN_DIR/science_stderr.log"; then
  REASON=SCIENTIFIC_DATA_EXTENSION_FAILED; exit 2
fi
if ! (cd "$SBE_RUN_DIR/scientific_dataset_unqualified" && sha256sum -c SHA256SUMS.txt) \
     > "$SBE_RUN_DIR/science_hash_validation.log" 2>&1; then
  REASON=SCIENTIFIC_DATA_EXTENSION_HASH_FAILED; exit 2
fi
cat "$SBE_RUN_DIR/science_stdout.log"
if ! "$PY" "$SBE_PACKAGE_ROOT/validate_export.py" \
  --evidence "$OUT" --science "$SBE_RUN_DIR/scientific_dataset_unqualified" \
  > "$SBE_RUN_DIR/science_validation.log" 2>&1; then
  REASON=SCIENTIFIC_EXPORT_VALIDATION_FAILED; exit 2
fi
cat "$SBE_RUN_DIR/science_validation.log"
MAIN_STATUS=EXPORTED_UNQUALIFIED
REASON=OK
cat "$SBE_RUN_DIR/constraint_audit.log"
echo 'PARALLEL_EXPORT_VALIDATION=PASS'
echo 'SCIENTIFIC_QUALIFICATION=NOT_ESTABLISHED'
