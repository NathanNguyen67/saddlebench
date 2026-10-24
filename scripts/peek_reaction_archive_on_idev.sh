#!/usr/bin/env bash
# Bounded read-only metadata peek; NO PRAGMA integrity_check or full row counts.
# Run only on an existing LS6 vm-small allocation. No jobs submitted.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd -P)"
case "$(hostname -s)" in
  login*|*login*) echo 'BLOCK: run on an allocated vm-small compute node' >&2; exit 2;;
esac
if [[ "${SLURM_JOB_PARTITION:-}" != vm-small || -z "${SLURM_JOB_ID:-}" ]]; then
  echo 'BLOCK: SLURM_JOB_ID and vm-small SLURM_JOB_PARTITION required' >&2
  exit 2
fi
PY=/work/11588/ndn454/ls6/conda_libraries/saddlemill/bin/python
DB="${SBE_SQLITE:-/work/11588/ndn454/ls6/saddlebench_reaction_archives/reaction_archive_28098e06323645bc1dca2210.sqlite}"
[[ -x "$PY" && -f "$DB" && ! -L "$DB" ]] || { echo 'BLOCK: Python/archive missing or symlink archive' >&2; exit 2; }
# Timeout cannot guarantee interruption of D-state shared filesystem I/O.
timeout -s TERM -k 5s 20s "$PY" "$HERE/tools/reaction_archive/peek_archive.py" "$DB"
