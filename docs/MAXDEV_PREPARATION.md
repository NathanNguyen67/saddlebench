# Scalar maxdev redundant-work optimization

The existing `core_drift_maxdev(dict, dict)` public API is unchanged. It validates both reference dictionaries and converts both position and cell arrays on **every pair**. This repeats expensive validation/allocation in a workload such as 8,128 Pt-343 comparisons on 128 already-known frames.

This change introduces **opt-in** `prepare_geometry(g)` and `core_drift_maxdev_prepared(a,b)` in `saddlebench.matching.geometry`. One-time preparation fully executes the existing `validate_geometry` and makes independent, read-only NumPy copies of the positions/cell, atom ordering and PBC. The prepared kernel retains the old median-`<=`-tie core, mean displacement, two MIC evaluations, cell tolerance, atom/PBC checking, and max norm. Orthogonal MIC caches cell basis/lensq once; near-boundary/degenerate/skew cases fall back to the frozen `_mic` implementation. No new identity standard or threshold is introduced.

A separate `PreparedReferenceMatcher` in `saddlebench.matching.matcher` offers an explicit reusable context for repeated matching calls, retaining reference IDs, ordered results and error reporting. It is **not activated by default** for `saddlebench score`, whose legacy input behavior and outputs remain unchanged pending LS6 real-data parity/performance validation. Prepared objects are owned by the caller; modify the original geometry only by creating a **new** prepared object. Do not use one prepared snapshot to represent a subsequently mutated reference.

## Exact pinned Pt-343 head-to-head

An unmodified copy of the original SHA-verified portable benchmark, including 128 real frames and the pinned V3 comparator, is in `benchmarks/pt343_maxdev/`. `benchmark_prepared.py` adds a 3-way real-data measurement on all 8,128 pairs: frozen V3, unchanged SaddleBench, prepared SaddleBench. It checks legacy vs prepared distances (`<= 1e-10 A` maximum difference), 0.05/0.10/0.20 A decisions, and the original V3 vs legacy parity. Prep time is separately reported; rates measure already-loaded repeated comparisons.

On an existing LS6 vm-small allocation, use:

```bash
bash benchmarks/pt343_maxdev/run_prepared_on_ls6_vm_small.sh
```

The launcher uses the authoritative SaddleMill Python and vm-small NumPy overlay, does not submit jobs, and prints `UPLOAD_RESULT=...` for the small JSON report. This environment was not available to the local CPU unit test run; local synthetic CPU tests do **not** establish 8,128-Pt-frame performance.

## Real LS6 validation and score-path adoption (2026-10-24)

From `saddlebench_dev_evidence_v2_20261024_001550_2608166.tar.gz` on
`v320-027`, Python 3.12.13, NumPy overlay 2.4.6, ASE 3.29.0, one
measurement across 8,128 pairs of native real 343-atom Pt frames:

| Implementation | Pairs/s | Seconds |
| --- | ---: | ---: |
| Legacy SaddleBench | 1201.60 | 6.7643 |
| Frozen V3 | 3868.38 | 2.1011 |
| Prepared SaddleBench | 6786.65 | 1.1976 |

Preparation took 0.0481 s. Prepared vs legacy distances were numerically
identical in all comparisons, as were classification decisions at the
0.05 / 0.10 / 0.20 A cutoffs. Three-way benchmark passed.

`scoring.engine.score()` now uses one short-lived `PreparedReferenceMatcher` per
score invocation to reuse reference geometry validation and allocations. The
public dictionary-based `core_drift_maxdev()` kernel stays unchanged; no
scientific definition, cutoff, comparison semantics or score output field changed.
Tests compare complete score reports with the old path, including cases outside
Pt-343. This is source-only: no installed/published version was modified.
