# SaddleBench registry exporter vNEXT — 2026-10-21

**Conclusion:** Ready-to-run, read-only LS6 exporter that preserves the RC14 v3 six JSONL datasets and adds **saddles enriched with reaction triplet links and Hessian-mode sources** for other projects. This is a candidate replacement *data snapshot*, **not yet the active scorer reference**.

## 1. Put the source into SaddleBench Git

On an LS6 login node, from the extracted top-level package directory:

```bash
bash publish_source_to_github.sh
```

This clones a **fresh temporary checkout** of `NathanNguyen67/saddlebench`, runs all CPU-only tests, adds `tools/registry_export` and `docs/REGISTRY_EXPORT_SCIENTIFIC_LAYER.md`, commits and pushes with Git credentials already configured on LS6. **It will refuse to overwrite an existing `tools/registry_export` Git directory.** Does not change `/scratch` pipeline state or any existing repo checkout. Check `SOURCE_GIT_PUBLISH=PASS` and `PUBLISHED_COMMIT=<sha>`; if auth/branch protection fails, nothing is published.

If desired, only the source can be committed first; no LS6 run or HF upload happens in this step.

## 2. Extract latest published registry

Upload ZIP into `/scratch/11588/ndn454/sm_anskey/` and extract it (new directory, never overwrite earlier runs). On an **active `vm-small` compute node**:

```bash
cd /scratch/11588/ndn454/sm_anskey
unzip -nq SADDLEBENCH_REGISTRY_EXPORT_VNEXT_20261021_v1.zip
KIT=/scratch/11588/ndn454/sm_anskey/SADDLEBENCH_REGISTRY_EXPORT_VNEXT_20261021_v1/tools/registry_export
SBE_WORKERS=4 bash "$KIT/run_direct_vm_small.sh" --preflight-only
SBE_WORKERS=4 bash "$KIT/run_direct_vm_small.sh"
```

On a login node, **submit** instead of directly running the exporter:

```bash
KIT=/scratch/11588/ndn454/sm_anskey/SADDLEBENCH_REGISTRY_EXPORT_VNEXT_20261021_v1/tools/registry_export
SBE_WORKERS=4 bash "$KIT/submit_ls6.sh"
```

One CPU-only Slurm job uses `-A CHE21006`, `vm-small`, 16 CPUs and a 4-hour requested limit. Default is four workers; an inherited OpenMP thread limit of 1 doesn't falsely imply only one allocated CPU. If resource availability requires one process, set `SBE_WORKERS=1` with the *same command*. No GPUs are requested.

The exporter pins the published-registry pointer to a source SHA-256. It does not read unpublished state, change registry or perform science calculations. New output is always placed into a fresh numbered diagnostics directory, never overwriting old dataset files.

**Check**:

```text
PARALLEL_EXPORT_VALIDATION=PASS
SCIENTIFIC_LAYER_EXPORT=PASS
SCIENTIFIC_EXPORT_VALIDATION=PASS
EXPORT_STATUS=EXPORTED_UNQUALIFIED REASON=OK
DIRECT_VM_SMALL_EXPORT=PASS   # only when using the direct runner
RESULT_DIR=/scratch/.../saddlebench_diagnostics/...
UPLOAD_THIS_FILE=.../sbe_export_report_....tar.gz
```

Outputs per run:

```text
<RESULT_DIR>/
  evidence_unqualified/                 # Original six RC14 files unchanged
    saddles.jsonl
    doublemin_observations.jsonl
    reactions.jsonl
    minima.jsonl
    geometries.jsonl
    source_artifacts.jsonl
    manifest.json
  scientific_dataset_unqualified/       # NEW linked scientific data
    saddles_enriched.jsonl
    reaction_triplets.jsonl
    hessian_modes.jsonl
    mode_arrays/modes_000000.npz ...
    manifest_science.json
    SHA256SUMS.txt
  compact_summary.json
  constraint_audit.json
  sbe_export_report_<UTC>.tar.gz        # Upload this small diagnostic archive
```

**Upload to ChatGPT**: upload only `UPLOAD_THIS_FILE` (the compact `sbe_export_report_*.tar.gz`). It contains both manifests and audit/validation logs, but not the enormous registry data. Do not transfer a huge dataset to diagnose a missing mode or metadata mismatch.

## 3. Optional private Hugging Face candidate upload

Requires a working `huggingface_hub` connection and an **existing private** dataset repo. Run this from the compute node/storage context **only after examining the first extraction report**:

```bash
PY=/work/11588/ndn454/ls6/conda_libraries/saddlemill/bin/python
KIT=/path/to/SADDLEBENCH_REGISTRY_EXPORT_VNEXT_20261021_v1/tools/registry_export
RUN=/scratch/11588/ndn454/sm_anskey/saddlebench_diagnostics/<RESULT_DIR_NAME>
"$PY" "$KIT/upload_hf_candidate.py" \
  --evidence "$RUN/evidence_unqualified" \
  --science "$RUN/scientific_dataset_unqualified"
```

This **only prints a plan**. Adding `--upload-candidate` performs versioned, immutable upload to `candidates/registry_<sha16>/` and does *not* replace `private/reference` or any old published dataset version. Explicit scientific reference qualification and a separate promotion are required before replacing official SaddleBench scoring data.

## Compatibility and validation

- 17 CPU tests pass locally across original RC14 parallel equivalence, vm-small preflight, and new scientific enrichment.
- The full LS6 extraction of the newly published ~120k additional saddles **has not been run** in this environment.
- The full Hugging Face publication and candidate upload **have not been executed**.
- The existing scoring baseline and all canonical scientific definitions remain unchanged.

See `docs/DATASET_SCHEMA.md` for exact data linkage, ambiguous directionality, unavailable data, and ASE loading examples.
