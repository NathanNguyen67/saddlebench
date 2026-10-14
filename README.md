# SaddleBench

SaddleBench scores predicted saddle-point geometries against the **public published reference** of eligible reactions and known saddle diagnostics. Scoring is CPU-only; your trajectories stay local. This is an open-reference research prototype, **not** an independently certified or exhaustive benchmark ground truth.

## Scoring modes

Use `--mode` with `python scripts/score_from_hf.py`. All **three modes use
certified greedy first-hit matching**, a cached measured saddle-group radius
index, nearest-group-first search, and exact core-drift **maxdev <= 0.2 A**.
The default is `score`. They search in this order:

| Mode | Search order | Stopping rule | Output |
|---|---|---|---|
| **`score` (default)** | Eligible reactions, then known non-first-order saddles if eligible-unmatched | First exact hit | Reaction-credit preview and first-hit non-first-order flags |
| **`score_detailed`** | Eligible reactions, then known non-first-order, then all remaining diagnostic categories if still unmatched | First exact hit per prediction | Greedy full-catalog existence and first-hit category; **NOT exhaustive W2 match lists or canonical B** |
| **`coverage`** | Eligible reactions only | First exact hit | Unique first-hit eligible-reaction coverage; no diagnostics |

**Change from RC1:** `score_detailed` is now the already benchmarked *greedy
full* algorithm (18.65 s for the earlier 499-prediction, two-process
Saskatchewan experiment). It is **not** the old exhaustive W2 scorer and does
**not** preserve W2's native JSON schema. This is an intentional scientific
*reporting/stopping-rule* change. Earlier W2 numerical scores must not be
silently compared to the new greedy scores.

The group bound and exact MIC calculations preserve the existing geometric
identity definition; topology is never used for equality. However, stopping at
one eligible hit can hide cross-reaction ambiguity, and stopping at one
diagnostic hit can hide multiple classifications. Every greedy JSON result
sets `canonical_known_bad_unique_B: null`. Greedy `B`-like counts are **first-hit
preview indicators**, not proven W2 bad-saddle penalties. The frozen W2 source
remains in the repository for historical rescoring, but no longer corresponds
to any of these three public `--mode` names.

### Choosing the mode

```bash
# Default: greedy reactions then known non-first-order saddles, one CPU process
python scripts/score_from_hf.py \
  --case-id case0001 --submission predicted_saddles.traj \
  --elapsed-seconds 3600 --output score.json

# Greedy full-catalog first-hit classification (NOT canonical exhaustive W2)
python scripts/score_from_hf.py --mode score_detailed \
  --case-id case0001 --submission predicted_saddles.traj \
  --elapsed-seconds 3600 --output score_detailed.json

# Reaction coverage ONLY. No non-first-order or general diagnostics.
python scripts/score_from_hf.py --mode coverage \
  --case-id case0001 --submission predicted_saddles.traj \
  --elapsed-seconds 3600 --output coverage.json
```

`--elapsed-seconds` is the **search** duration, including unsuccessful attempts, **not** the scoring duration.

### Controlling CPU parallelization

`--processes` defaults to **1 (no parallelism)** for all modes. For multiple cases, set `--processes 2`, `--processes 4`, etc. Work is parallelized **across independent cases**, not across saddle frames within a single case. A single `--case-id` uses one worker even if `--processes 4` is supplied. Excess workers beyond the number of cases bring no benefit. Each process can hold separate geometry and reference data in RAM, so choose concurrency according to your resource limits. `--microbatch-size` (default 16) independently controls NumPy vectorized MIC comparisons per first-hit step; it is **not** the CPU core count.

For multiple cases, create `submission.csv` containing:

```csv
case_id,trajectory,elapsed_seconds
case0001,predictions_case0001.traj,3600
case0002,predictions_case0002.traj,4800
```

```bash
# Two cases scored in parallel, using the default score mode
python scripts/score_from_hf.py --submission-csv submission.csv \
  --processes 2 --output batch_score.json

# Detailed scorer can also parallelize over cases
python scripts/score_from_hf.py --submission-csv submission.csv \
  --mode score_detailed --processes 2 --output batch_score_detailed.json

# Coverage only, serial, default --processes 1
python scripts/score_from_hf.py --submission-csv submission.csv \
  --mode coverage --output batch_coverage.json
```

Trajectory paths in the CSV are relative to the CSV directory. Outputs are never overwritten. See [Submission format](docs/SUBMISSION_FORMAT.md) for requirements.

## Installing and scoring

Python 3.10+ and ASE / NumPy are required. For published-reference downloads, also install `huggingface_hub`:

```bash
python -m pip install -e .
python -m pip install huggingface_hub
```

The public starting-minimum dataset is [`traingletrain/saddlebench-prototype`](https://huggingface.co/datasets/traingletrain/saddlebench-prototype). The scoring reference is [`traingletrain/saddlebench-reference-private`](https://huggingface.co/datasets/traingletrain/saddlebench-reference-private). Both are public; the reference's `-private` suffix is part of its published name, not an authentication requirement. The pinned reference contains 123 cases, 2,387 eligible reaction identities and 83,529 diagnostic saddle records; counts are *dataset-wide*, not the number matched by any one submission. Published registry watermark is 23,134, registry SHA-256 `85d37e55448b544473b34e7bb244823e80ddc85091bbe6ae3e771c7beb4b0a41`.

Download an example starting structure:

```bash
hf download traingletrain/saddlebench-prototype minima/case0001.traj \
  --repo-type dataset --local-dir inputs
```

Scoring downloads/checks the public reference, checks each published SHA-256 and keeps predictions local. For offline scoring of an already published and verified HF reference snapshot, pass **both** `--reference-root PATH` and `--cases-csv PATH`. The latter is the published public `cases.csv`, not a submission manifest.

### Output semantics and formula

For any mode with `K > 0`, the same *algebraic* provisional score shape is
shown, `100*(U/K)^2*(U/(U+2*Bpreview))*(3600/search_seconds)` where `U` is the
number of distinct **first-hit** reaction IDs and `Bpreview` is the number of
distinct, known-start potential-bad groups encountered as first diagnostic
hits. For `coverage`, `Bpreview=0` by definition because diagnostics are not
searched. `score` tests known non-first-order; `score_detailed` also tests
remaining diagnostic categories (including connected high-barrier) after
known non-first-order misses. These different search domains produce
different *provisional* scores.

None of the three modes establishes authoritative W2 `known_bad_unique_B`; it
is serialized as `null` in every quick case and batch report. The native W2
`score_detailed` output is **not** retained under that name. All three modes
return the versioned `saddlebench-quick-case-v1` or
`saddlebench-quick-batch-v1` output with an explicit `mode` and
`score_semantics`. This intentionally changes the `score_detailed` output
format, while leaving the source reference, threshold and original W2 scorer
implementation available for historical analyses.

## Scientific reference and limitations

The eligible reference uses a saddle force threshold of 0.005 eV/Å, DoubleMin endpoint thresholds of 0.0025 eV/Å, connected DoubleMin reactions and barriers strictly below 1.5 eV. Reference `all_saddles` mode accepts specified historical DoubleMin protocols; it is not simply every first-order registry saddle. Starting constraints are preserved for searches; saddle-to-saddle identity uses geometry and ignores fixed-atom metadata. Calculator/PES provenance is not independently verified, and the reference is not exhaustive. Unknown predictions are not automatically incorrect. Runtime is self-reported.

All three modes return first-hit entries instead of complete diagnostic identity lists; `score_detailed` searches the full catalog but is **not** exhaustive. The original full scorer remains available as source code for separate historical W2 rescoring. No greedy mode reports complete `entries[].matched_saddle_groups` populations.

## Tests

```bash
python -m pytest -q
```

Tests verify software and fixed sample references; they do not independently certify scientific reference completeness. The greedy full computation was already benchmarked on Saskatchewan in the preceding experimental sweep. This release changes only the public mode routing, output labelling and packaging; no new Saskatchewan performance sweep is required by this handoff.

**Legacy CLI note:** `saddlebench score` (the older installed subcommand consuming extxyz and a local reference path) is separate from this published Hugging Face `.traj` scoring interface and has **not** been repurposed. Select `score`, `score_detailed`, or `coverage` with `--mode` on `scripts/score_from_hf.py`, as shown above. This avoids silently changing existing extxyz API behavior.
