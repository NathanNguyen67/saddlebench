# SaddleBench

SaddleBench compares predicted saddle-point structures with a public collection of known chemical reactions and other saddle points. It tells you how many known reactions your search discovered and, if you choose, whether some predictions also resemble other saddle points in the reference dataset.

You can score results on your own computer. SaddleBench does not run the searches for you, and your predicted structures are not uploaded.

## Datasets

| Dataset | What it contains | Link |
| --- | --- | --- |
| **Starting structures** | 123 starting structures, case information, and example submissions. Choose a structure here to start a saddle-point search. | [SaddleBench starting structures](https://huggingface.co/datasets/traingletrain/saddlebench-prototype) |
| **Scoring reference** | Known eligible reactions and other registered saddle points used to evaluate your predictions. Includes 2,387 eligible reaction identities and 83,529 diagnostic saddle records. | [SaddleBench scoring reference](https://huggingface.co/datasets/traingletrain/saddlebench-reference-private) |

**Both datasets are public.** The word `private` in the second dataset's address is part of its name; you do not need permission to download it. These counts describe the currently published collection, not the number of reactions in any single starting structure.

## Scoring interface

**`saddlebench score` is the default public interface.** It scores predictions at
three independently rerun, published maxdev cutoffs: **0.20, 0.10 and 0.05 Å**.
The terminal displays a colored **per-dataset leaderboard**, coverage, the
median selected-reference maxdev across all matched predictions (including
repeats), and the median of each discovered reaction's best observed selected
match. This is **not** exhaustive nearest-reference geometry matching.

The command writes a native JSON report, standalone HTML, and dataset SVG/PNG
figures (PNG where a supported rasterizer is available). It does not automatically
open an X11 viewer; use `--open-image` if desired. The model is ranked only when
all submitted models evaluated identical case sets. All colors are for display,
not scientific classification. Searches happen outside SaddleBench.

The older provisional-score and diagnostic classifier is available explicitly via
`saddlebench provisional-score`. The original local-reference extxyz scorer is
`saddlebench legacy-score`. For backward compatibility, `saddlebench score`
with an explicit `--mode score|score_detailed|coverage` invokes the earlier
Hugging Face scorer, and `--reference` invokes the original local scorer.

## Installation

SaddleBench requires Python 3.10 or newer. Run:

```bash
git clone https://github.com/NathanNguyen67/saddlebench.git
cd saddlebench
python -m pip install -e .
```

Scoring runs on ordinary processor cores; a graphics processor is not required. After installation, `saddlebench` works from any directory with the installed Python environment active.

## Quick start

**1. Select a starting structure.** Browse the [starting structures dataset](https://huggingface.co/datasets/traingletrain/saddlebench-prototype) and choose a case. For example, download `case0001`:

```bash
hf download traingletrain/saddlebench-prototype minima/case0001.traj \
  --repo-type dataset --local-dir inputs
```

**2. Run your saddle-point search.** Use any method you want, starting from the downloaded structure. Save the predicted saddle geometries as an ASE trajectory file (`.traj`), with one frame for each prediction. Keep the same atoms in the same order and preserve the simulation cell and periodic boundary conditions. See [Submission format](docs/SUBMISSION_FORMAT.md) for requirements.

**3. Score your predictions.** By default this displays the **colored
three-tolerance coverage/geometry leaderboard** and saves JSON, HTML, and SVG/PNG:

```bash
saddlebench score \
  --case-id case0001 \
  --submission predicted_saddles.traj \
  --elapsed-seconds 3600 \
  --output results.json
```

For a purely terminal/JSON evaluation, add `--no-html --no-image`.
To force ANSI color in a piped terminal add `--color`; `--open-image` requests
an X11 viewer when available. Your predictions remain on your machine.

To reproduce the **earlier, separate provisional-score method**, run:

```bash
saddlebench provisional-score \
  --case-id case0001 --submission predicted_saddles.traj \
  --elapsed-seconds 3600 --mode score --output provisional.json
```

`--elapsed-seconds` is the **time spent running your saddle-point search**, including attempts that produced no usable result. It is **not** the time SaddleBench takes to evaluate the predictions.

SaddleBench downloads and verifies the public scoring reference automatically. It writes a JSON report to the path you provide and does not overwrite an existing output file. The predicted geometries remain on your machine.

## Scoring multiple starting structures

Make a file called `submission.csv` containing the case identifier, the trajectory path, and the total search time for each case:

```csv
case_id,trajectory,elapsed_seconds
case0001,predictions_case0001.traj,3600
case0002,predictions_case0002.traj,4800
```

Then run:

```bash
saddlebench score \
  --submission-csv submission.csv \
  --output batch_score.json
```

To use more than one processor core for **different cases**, set `--processes`:

```bash
saddlebench score \
  --submission-csv submission.csv \
  --mode score_detailed \
  --processes 2 \
  --output batch_detailed.json
```

**The default is `--processes 1`**, so scoring runs serially. Parallel processing divides separate cases among the requested workers; it does not divide a single case among cores. You can use `--processes 4` for four cases, for example, if your computer has sufficient memory. Relative trajectory paths in the spreadsheet are resolved relative to the spreadsheet file.

### Comparing different models on the same starting structure

**Leaderboard display:** `CASE` shows only the numeric suffix of the published
case ID (`case0006` -> `6`). `DATASET` is the uppercase namespace prefix of
the case's published `structure_id` (for example, `oc20:...` -> `OC20`). If
no dataset namespace is available, the table shows `-` rather than guessing.
The input CSV still uses full identifiers such as `case0006`; only terminal
presentation changes. All matching and score calculations are unchanged.


Add an optional `model` column to the same `submission.csv`. One row is one
**(model, case)** submission; different models may score the same case. The
`model` label is for reporting only and does not affect matching or scoring.

```csv
model,case_id,trajectory,elapsed_seconds
SEL04-M24,case0006,sella_case0006.traj,532
ForceBank-Fourier,case0006,forcebank_case0006.traj,575
SEL04-M24,case0022,sella_case0022.traj,1114
ForceBank-Fourier,case0022,forcebank_case0022.traj,1050
```

```bash
saddlebench score \
  --submission-csv submission.csv --mode score_detailed \
  --processes 2 --output comparison.json
```

This prints a **model-level aggregate leaderboard by default** (one row per model),
with reference coverage, mean historical reference-confidence heuristic,
first-hit diagnostic counts, penalized bad groups and score/hour. The score
and group totals use the existing aggregation; no formulas are changed.
Use `--view case` to display the previous per-case ranked table.
For unlabeled CSVs or single-case commands, the existing case view is used.
The model leaderboard ranks only when all models have identical submitted
case sets; this does **not** verify a complete benchmark cohort. ANSI color is automatic on compatible terminals and
absent when output is redirected; set `NO_COLOR=1` to disable colors.

CSV submissions **without** a `model` column retain the prior behavior and
native JSON schema. A model-labeled CSV writes a separate
`saddlebench-quick-model-comparison-v1` JSON report containing the existing
`case_results` plus `model` labels and `model_aggregates`; it does not merge
separate model results into a scientifically meaningless overall score.
Duplicate `(model, case_id)` pairs are rejected. Per-model aggregate ranks are
printed only when all models were scored on the same set of cases.
`--view` only changes the terminal display, not the JSON report.
Terminal options: `--view model` (default for model-labeled CSV) and
`--view case` (individual case comparison). Model-level `REF%` is the
arithmetic mean of the *published historical* case confidence heuristics,
shown only when all submitted cases have usable reference metadata; it is
not a calibrated probability of exploration completeness.

### Prediction-level percentages: RED% and ELIG%

- **RED%** = `100 * duplicate eligible-reaction predictions / N`. A duplicate
  means a prediction matched an eligible reaction identity already credited
  earlier in that same case and model submission. This is **not** an
  exhaustive geometric redundancy measurement.
- **ELIG%** = `100 * predictions matched to eligible reactions / N`,
  including both first credits and duplicates. This is a reference
  matching rate, **not** an independent connectedness measurement.
- **N** = total submitted prediction frames. Model-level RED% and ELIG%
  use totals across its submitted cases, rather than averaging case
  percentages. When `N=0`, both percentages display `-`.

These metrics affect **terminal presentation only**; scoring, JSON
output and reference definitions are unchanged.

**Display legend:** `N` predictions, `U/K` distinct discovered/known eligible
reactions, `COV%` coverage, `CONF%` historical reference-exploration heuristic,
`HIT` predictions matched by a checked stage, `DUP` eligible repeats,
`NF` known non-first-order first hits, `HB` known connected high-barrier first
hits, `OTH` other diagnostic first hits, `UNM` unmatched in checked stages,
`B` distinct first-hit penalized bad saddle groups, `SEC` search seconds,
`S/H` provisional score/hour. Unknown predictions are not automatically bad.
The confidence heuristic is not the probability that all saddles are known.

## Scoring formula

Reaction coverage for one starting structure is

```text
coverage = U / K
```

The reported provisional score per hour is

```text
score_per_hour = 100 * (U / K)^2 * (U / (U + 2*B)) * (3600 / T)
```

- **U:** distinct eligible reaction identities discovered by first-hit matching.
- **K:** number of known eligible reaction identities for this starting structure.
- **B:** distinct first-hit potential-bad *saddle groups*, including groups whose
  starting-minimum provenance is unrecorded. This is **not** a certified or
  exhaustive count of bad reactions.
  Existing JSON fields ending in `_with_known_start` retain their names for
  compatibility, but now count groups with unrecorded starts too.
- **T:** total self-reported search time in **seconds**, including unsuccessful
  attempts. This is search time, not the scoring program's runtime.

`coverage` mode skips bad-saddle diagnostics (`B=0` in the score preview).
`score` mode considers known non-first-order first hits; `score_detailed`
additionally considers known connected high-barrier saddles. The matching
modes may produce different first-hit estimates. A score is zero for `U=0`
and undefined (`null`) if `K=0`.

### Reference exploration confidence (heuristic)

The terminal summary separately shows the **published reference's historical
exploration-confidence heuristic for each case**. It is read from the verified
published `cases.csv`, and **not** calculated from the new submission.

```text
n_red = mean redundant eligible observations after the last new reaction
        across shuffled historical observation sequences
exploration_confidence = clamp(1 - 10 / n_red, 0, 1)
```

When there is insufficient historical rediscovery evidence, the confidence
is unavailable (`N/A`). This is a redundancy-based heuristic, **not** a
calibrated probability that all physical saddles/reactions are known, and
not a confidence interval on a new submission's `U/K` coverage.

### Terminal summary

After writing the unchanged, machine-readable JSON score,
`scripts/score_from_hf.py` also prints coverage, score preview, reference
exploration confidence, prediction-match classifications, and (when checked
by the mode) known non-first-order and connected high-barrier first hits.
First-hit bad-saddle matches and groups are **not** canonical verified counts
of bad reactions. Unknown predictions are not automatically wrong or novel.
This is display-only: scoring definitions, reference matching, and the native
JSON schema remain unchanged.

## Understanding the results

**Coverage** is the fraction of known eligible reactions discovered for a case. For example, discovering 4 distinct eligible reactions out of 10 gives 40% coverage; discovering the same reaction multiple times does not increase it.

**The provisional score** also considers how long the search took and possible matches to certain saddle points that are not eligible reactions. A search that finds more distinct eligible reactions in less time generally receives a higher score. The different modes examine different sets of reference saddles, so their provisional scores are not necessarily identical.

**Other known saddle points** are why `score_detailed` exists. A predicted structure might not qualify for reaction credit but could still match a previously registered saddle. The detailed mode checks the broader reference collection and reports the first matching diagnostic category, when one is found. Not finding a match does not prove the prediction is incorrect or novel.

### How matching and scoring work

SaddleBench compares saddle geometries within the same starting-structure and simulation-cell scope. A match must pass the same exact geometric criterion in every mode: a maximum core-drift displacement of **0.2 angstrom or less**. Atom order and periodic boundary conditions must be compatible. Matching is based on geometry, not on reaction-network connectivity inferred from atom bonds.

To keep evaluation fast, the scorer groups nearby reference structures and checks likely matches first. **It stops after the first confirmed match for a prediction.** Eligible reaction matches take priority over matches to other saddle-point categories. `score` checks known non-first-order saddles when no eligible reaction was found; `score_detailed` additionally searches the remaining diagnostic categories.

This stopping rule makes scoring faster, but it has an important limitation: a single prediction may resemble more than one reference reaction or saddle category. The reports do **not** list all such possibilities and do **not** determine an exhaustive count of bad saddle identities. Their provisional scores should therefore be interpreted as quick comparisons, not exhaustive classifications.

For users comparing results across multiple cases, the reported score also uses the **search times you supply**, not a timing measured by SaddleBench.

## Scope and limitations

The published reference includes reactions meeting its criteria for verified saddle and endpoint optimizations, connectivity, and an energy barrier strictly below 1.5 electronvolts. Not every previously registered saddle is an eligible reaction. The reference is not an exhaustive list of all possible reactions or saddles, and its potential-energy-model provenance has not been independently verified. Results from different energy models may not be comparable.

The benchmark is an openly available research resource rather than an independently certified leaderboard. Unknown predictions are not automatically wrong, and the results do not establish that every possible reaction has been discovered.

## Tests

To test the installed scoring software, install `pytest` and run:

```bash
python -m pip install pytest
python -m pytest -q
```

The tests check software behavior and example fixtures; they do not establish that the published scientific reference is complete.

**Other interface:** The separately installed `saddlebench score` command supports a local-reference/extxyz workflow. The `score`, `score_detailed`, and `coverage` settings documented above are selected with `--mode` in `scripts/score_from_hf.py` for the public dataset workflow.

### Experimental greedy coverage tiers (separate read-only CLI)

The normal `scripts/score_from_hf.py` scoring modes and JSON outputs remain unchanged.
Use `scripts/coverage_tiers_from_hf.py` with the same model-labeled (or legacy)
submission CSV to evaluate **eligible-reaction greedy coverage** independently
at core-drift maxdev cutoffs of 0.20, 0.10 and 0.05 Angstrom. No non-FO or
other diagnostic catalog matching is performed. The same verified Hugging Face
reference, eligible reactions, indexes, and case scoping are used.

```bash
PYTHONPATH="$HOME/saddlemill_jobs/saddlebench_published" \
/home/ndn454/fairchem_audit_222_223/venvs/fairchem222/bin/python \
"$HOME/saddlemill_jobs/saddlebench_published/scripts/coverage_tiers_from_hf.py" \
  --submission-csv "$HOME/saddlemill_jobs/sb_score_demo_20261014T164830Z/submission.csv" \
  --processes 2 --output "$HOME/saddlemill_jobs/coverage_tiers_$(date +%s).json"
```

`--view case` prints individual starting minima; model view is the default.
`COV@.20`, `COV@.10`, and `COV@.05` are **micro** coverage at three
independently rerun greedy first-hit thresholds. Macro coverage is in the
separate diagnostic JSON. `MED-G` (Angstrom) is the median selected-reference
maxdev of the FIRST credited prediction for each reaction at 0.20 A.
`MED-BESTG` is the median, over credited reactions, of the LOWEST observed
selected-reference maxdev across all predictions greedily assigned that
reaction. It does NOT exhaust the nearest reference geometry per prediction.
Neither median includes unmatched reactions. Each tier's own median is also
in the diagnostic JSON. The new diagnostic JSON schema is
`saddlebench-greedy-coverage-tiers-v1` and is NOT the native quick-score schema.
Existing `score`, `score_detailed`, and `coverage` scientific definitions and
thresholds are unchanged. This is a new experimental evaluation metric, not
independent proof of stationary-point validity or DoubleMin connectedness.
