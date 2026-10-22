# SaddleBench RC14: parallel published-registry evidence export

## Conclusion

This is a **new, read-only, parallel implementation of the existing W1-v2 full published-registry exporter**, aimed at the *newest already published* snapshot, not the obsolete 7,522-event file. Run once on Lonestar6 `vm-small` to enumerate the expanded registry and measure unknown **saddle** constraints. No Search, Hessian, DoubleMin, GPU, registry change, or Hugging Face publication is performed.

This package does **not** claim to have repaired the unknown saddle-constraint issue. Exporting more quickly and recovering verified constraints are separate tasks. Unknown remains unknown, and references derived from this export **must not be republished/used for an official score until the scorer-reference constraint audit and scientific gates pass**.

## Submit (on LS6 login node)

Upload the ZIP into `/scratch/11588/ndn454/sm_anskey/`, then:

```bash
cd /scratch/11588/ndn454/sm_anskey
unzip -nq SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v1.zip -d sbe_parallel_rc14
bash sbe_parallel_rc14/SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v1/submit_ls6.sh
```

`SBE_WORKERS=4` is the default. To retry on a **new** run only if performance evidence warrants it, set `SBE_WORKERS=2` or `SBE_WORKERS=8` before the command; **do not blindly resubmit a still-running job**. All runs create unique output directories. No production files are removed or replaced.

The launcher snapshots the **current** published-registry pointer into an immutable path and SHA-256 *before* it submits Slurm, so a later publication cannot silently change the data being exported. It prints the actual source path/SHA. The v2 source code provenance is pinned; it is not asserted to be the current installed workflow source.

## Resource use and data flow

One CPU-only Slurm job: `-A CHE21006`, `-p vm-small`, 1 node, 1 task, 16 CPUs, 4 hours. No GPU or MPS. Four independent reader processes with `mode=ro&immutable=1` and separate temporary spools. Each main registry table is split into key-ordered ranges; the main process concatenates those record ranges and heap-merges geometries and source-artifact records by stable keys. SHA-256 checks, source snapshot identity, immutable-source check, completeness and deterministic ordering remain enforced. All six JSONL filenames/record formats and existing manifest schema are preserved. The `export_version` metadata is updated to distinguish this implementation.

Artifact-source hashing on Lustre is likely the dominant cost, and parallelism can also saturate shared storage. Therefore **4 workers is a conservative initial test**, not a guaranteed speedup. Startup/cell science settings and scorer matching algorithms are unaffected.

## Success checks

1. `sbatch` prints `JOB_ID=...`, `RESULT_DIR=...`; use the printed `sacct` command or `squeue -j JOB_ID`.
2. In `slurm-JOBID.out` find `PARALLEL_EXPORT_VALIDATION=PASS`, `EXPORT_STATUS=EXPORTED_UNQUALIFIED REASON=OK` and `UPLOAD_THIS_FILE=`.
3. Inspect `RESULT_DIR/compact_summary.json` (when written) for `status=EXPORTED_UNQUALIFIED`, `wrapper_exit_code=0`, `registry.published_through`, row counts, and the registry SHA. Verify `RESULT_DIR/constraint_audit.json` for `counts.saddles.verified/unknown`.
4. Upload **only** `RESULT_DIR/sbe_export_report_*.tar.gz` from this run, not the full raw evidence directory. The compact report includes logs, manifest and constraint audit. If it fails, upload the same report and relevant Slurm log. Leave raw/production outputs intact.

The new source SHA and published-through watermark will differ from RC9 when new results were published; that is intentional **dataset versioning**, not a change in the scientific definition. Do not silently overwrite the previous public reference or compare provisional scores across old/new denominators.

## Scientific versus implementation changes

**Scientific:** none. No maxdev threshold, reaction identity rule, barrier rule, optimizer/calculator or PES change; unknown constraint metadata are NOT filled in or relaxed.  No new certification is claimed.

**Implementation:** parallel sharded export, read-only deterministic merge, new snapshot pin at submission, unchanged output JSONL, new optional `--workers`, explicit manifest provenance version and a metadata-only constraint audit. No Slurm/GPU jobs were submitted by the packager; LS6 runtime is unverified.

**Caveat:** The legacy exporter marks constraint metadata unknown when the source is ASE-unreadable `.npz` or contains no serialized constraint objects. This package deliberately preserves those conservative semantics. A separate verified-provenance repair and a re-run of the existing saved Sella trajectories is necessary before accepting all score classifications.

## Direct execution from an existing vm-small allocation (v2)

**This is the requested alternative to `sbatch`.** Transfer/extract this v2 ZIP
into `/scratch/11588/ndn454/sm_anskey`, then, **from the shell on the compute
node belonging to your active `vm-small` allocation**, run:

```bash
cd /scratch/11588/ndn454/sm_anskey
unzip -nq SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v2.zip -d sbe_parallel_rc14_v2
bash sbe_parallel_rc14_v2/SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v2/run_direct_vm_small.sh
```

The launcher refuses LS6 login nodes. SSH into the vm-small compute node is
supported when `squeue` can verify **your own running vm-small allocation** there.
Merely SSH-ing to a compute node without an active allocation is not sufficient.
You may use `--preflight-only` to verify allocation, current published snapshot,
and available workers without creating a run directory. Set `SBE_WORKERS=2` for a
smaller allocation, or leave it at 4. Default results remain under
`/scratch/11588/ndn454/sm_anskey/saddlebench_diagnostics/` in a new
`sbe_parallel_export_rc14_direct_XXXXXXXX` directory.

To protect the run against SSH disconnection, start it inside `tmux` *on the
allocated vm-small node* (if tmux is available), and ensure the allocation
remains active for the full extraction. Alternatively keep the terminal open.
Running this script **does not create or extend a Slurm allocation**. It cannot
continue after the interactive allocation ends. The existing `submit_ls6.sh`
remains available for queue-based execution.

Success: `PARALLEL_EXPORT_VALIDATION=PASS`, `EXPORT_STATUS=EXPORTED_UNQUALIFIED
REASON=OK`, `DIRECT_VM_SMALL_EXPORT=PASS`, and `UPLOAD_THIS_FILE=<report.tar.gz>`.
The raw registry export remains in `RESULT_DIR/evidence_unqualified`; upload only
the compact `sbe_export_report_*.tar.gz` to ChatGPT. On failure, upload that
report plus `direct_run.log` and leave both raw and production outputs intact.

**Scientific change:** none. **Implementation change:** direct invocation and
allocation checks only; the exporter, report schema, and frozen snapshot rules
are unchanged. Unknown saddle constraints remain unknown and must be repaired
separately before new reference publication.

## v3 CPU affinity diagnostic repair (2026-10-13)

On a vm-small node, `taskset -pc $$` may show an affinity mask such as `0-15`
(16 available CPUs), but `nproc` can still report `1` if the shell inherited
`OMP_NUM_THREADS=1` or `OMP_THREAD_LIMIT=1`. GNU `nproc` honors OpenMP limits.
That is not evidence of a one-CPU Slurm allocation. The v2 direct launcher
mistakenly blocked four worker processes in this situation.

**v3 changes only the CPU preflight**: it calls
`env -u OMP_NUM_THREADS -u OMP_THREAD_LIMIT nproc` to read the available
CPU count independently from BLAS/OpenMP thread limits. The exporter worker
continues to force `OMP_NUM_THREADS=1` inside each process; this is appropriate
for four worker processes. A real one-CPU affinity limit still blocks four
workers. The own-allocation check is unchanged.

After uploading the v3 ZIP, from the **active vm-small allocation node**:

```bash
cd /scratch/11588/ndn454/sm_anskey
unzip -nq SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v3.zip -d sbe_parallel_rc14_v3
bash sbe_parallel_rc14_v3/SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v3/run_direct_vm_small.sh --preflight-only
bash sbe_parallel_rc14_v3/SADDLEBENCH_PARALLEL_PUBLISHED_EXPORT_RC14_20261013_v3/run_direct_vm_small.sh
```

The preflight prints `DIRECT_VM_SMALL_PREFLIGHT=PASS` without generating any
outputs. The live run prints `RESULT_DIR` and writes a new run there. Check
`PARALLEL_EXPORT_VALIDATION=PASS`, `EXPORT_STATUS=EXPORTED_UNQUALIFIED REASON=OK`,
`DIRECT_VM_SMALL_EXPORT=PASS`, and the `UPLOAD_THIS_FILE` report path.
No science, registry, output formats, submit limits or production state change.
No new job is submitted. Your active vm-small allocation must last long enough.
