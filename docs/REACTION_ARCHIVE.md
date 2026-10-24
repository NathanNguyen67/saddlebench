# Reaction archive (integrated standalone exporter v3)

The code under `tools.reaction_archive` is the **unqualified, read-only** SaddleMill registry extraction and SQLite archive implementation from `SADDLEBENCH_REACTION_SQLITE_RESUME_20261022_v3_LUSTRE_ID_FIX`. This does not modify the public scoring-reference contract. Do not interpret all extracted saddles as certified scoring reactions.

- The source registry and native ASE trajectories are **read-only**; output SQLite and optional regenerated JSONL are independent.
- Source IDs, embedded geometries, energies, mode provenance, missing-data statuses and endpoints are preserved as recorded. No optimizer or calculator is called.
- Interruptions resume from committed import offsets and extracted `(kind, path, frame)` rows. Oversized Lustre file IDs use tagged `fid:<decimal>` TEXT to avoid SQLite INTEGER overflow.
- The archive must be checked using `ARCHIVE_VALIDATION=...`, `ARCHIVE_COMPLETE=PASS`. Even an archive marked complete can carry `NO_RECORDED_MODE_PATH` for mode-free source observations.
- The presence of an older complete *lean JSONL* export does **not** establish that the subsequent SQLite exporter completed on Lonestar6.
- Default pinned registry identity follows the v3 exporter. Do not substitute an unrelated registry or delete a partially written SQLite.

On an **already allocated vm-small idev** node (not a login shell), run from the checked-out repository:

```bash
bash scripts/peek_reaction_archive_on_idev.sh   # cheap read-only meta; not integrity certification
# --run performs writer/import/extraction + full integrity validation on shared /work;
# do not launch until a resource/IO plan has been reviewed.
```

`--status` still triggers a full `PRAGMA integrity_check` on the shared archive and may stall; prefer the separate metadata-only peek. `--run` resumes, `--import-only` ingests the previous lean JSONL without competing for trajectory I/O, and `--export-only` regenerates JSONL from a complete archive. Set `SBE_SQLITE` explicitly if the current registry pointer has moved and the prior archive must be addressed without source registry files. See the original v3 README for backup, environment and fail-closed notes.

The original v3 exporter files, output keys, schema and SQL remain unchanged except for import paths. The shell launcher now imports the package from this source checkout via `PYTHONPATH`; no installer or GPU submission is performed.
