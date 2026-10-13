# SaddleBench

SaddleBench evaluates predicted saddle-point geometries against a published reference of reaction pathways. The Python scorer reports matched reaction identities, coverage, duplicate discoveries, and diagnostic classifications. It does not run saddle searches or energy calculations.

This is an **open-reference research prototype**. The reference data and scoring code are public. The current dataset is neither exhaustive nor independently certified as a benchmark ground truth.

## Data

| Dataset | Description |
| --- | --- |
| [Starting minima](https://huggingface.co/datasets/traingletrain/saddlebench-prototype) | 123 starting structures in ASE trajectory format, case metadata, and submission examples. |
| [Scoring reference](https://huggingface.co/datasets/traingletrain/saddlebench-reference-private) | 2,231 eligible reference reaction identities and a diagnostic catalog of 73,839 registered saddles. |

Both datasets are public. The `-private` suffix is part of the reference dataset's original name; authentication is not required.

The released reference corresponds to registry publication watermark **7,522** and registry SHA-256 `e33f7ac55cec2bc208fd1bb4ee74d047224fa072a7a43be1effdb4891c495c88`. These counts describe this snapshot only, not any later registry publication.

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/NathanNguyen67/saddlebench.git
cd saddlebench
python -m pip install -e .
python -m pip install huggingface_hub
```

Scoring is CPU-only and can be run independently of the software used to generate predictions.

## Quick start

Select a case from `cases.csv` in the starting-minima dataset. Download its input structure:

```bash
hf download traingletrain/saddlebench-prototype minima/case0001.traj \
  --repo-type dataset --local-dir inputs
```

Run a saddle-search method from the selected structure and save the proposed saddle geometries to an ASE `.traj` file, with one frame per candidate. Preserve atomic ordering, periodic cell and fixed-atom constraints.

Score the candidate geometries:

```bash
python scripts/score_from_hf.py \
  --case-id case0001 \
  --submission predicted_saddles.traj \
  --elapsed-seconds 3600 \
  --output score.json
```

`--elapsed-seconds` is the total search runtime, including unsuccessful attempts, not the duration of scoring. The command downloads and checks the reference, performs local comparisons, and writes a JSON report. Predictions are not uploaded. Successful execution prints `SCORE_VERIFY=PASS`. Choose a new output path for each invocation; the script does not overwrite existing reports.

To score several cases, create `submission.csv`:

```csv
case_id,trajectory,elapsed_seconds
case0001,predictions_case0001.traj,3600
case0002,predictions_case0002.traj,4800
```

Then run:

```bash
python scripts/score_from_hf.py --submission-csv submission.csv --output batch_score.json
```

Relative trajectory paths are interpreted relative to the CSV. See [Submission format](docs/SUBMISSION_FORMAT.md) for file and scope requirements.

## Scoring definition

For a case with at least one eligible reference reaction, the provisional score is

```text
score = 100 * (U / K)^2 * (U / (U + 2*B)) / T
```

`K` is the number of eligible reference reaction identities for that case; `U` is the number of distinct credited reaction identities; `B` is the number of distinct matched known-bad saddle identities (non-first-order or connected high-barrier); and `T` is the reported search time in hours. Duplicate discoveries do not increase `U`. Known disconnected first-order saddles, ambiguous matches and unknown predictions are neutral. If `K = 0`, coverage and score are undefined.

The published reference uses a saddle force threshold of **0.005 eV/Å**, DoubleMin endpoint thresholds of **0.0025 eV/Å** for each endpoint, a connected-reaction requirement and a barrier **strictly below 1.5 eV**. Its `all_saddles` mode admits eligible historical DoubleMin protocols; it does not award credit for every first-order saddle in the registry.

Matching uses core-drift **maxdev ≤ 0.2 Å**, with compatible atom ordering, cell, periodic boundary conditions, fixed-atom metadata and case scope. It does not use connectivity topology to declare geometric identity. These eligibility criteria are evaluated from stored reference evidence; the scorer does not rerun optimizations or Hessian calculations.

## Reference limitations

**Constraint metadata.** Some published saddle geometries have unknown fixed-atom constraints and cannot be compared by the current strict matcher. In a read-only audit, `case0006` had 1,243 such reference saddle members and `case0022` had 2,196. Neither case had any comparable candidate-reference pairs in that audit. Consequently, a `NO_MATCH` or `UNKNOWN_UNMATCHED` result for affected cases must not be interpreted as evidence of geometric disagreement. The reference requires provenance repair before reliable coverage can be reported for those cases.

**Coverage.** The reference consists of registered observations, not an exhaustive enumeration of possible saddles. An unknown candidate is not automatically incorrect.

**Potential-energy surface.** The reference's calculator/PES scope has not been independently verified. Scores obtained using different calculators are not necessarily comparable.

**Benchmark use.** The reference is public and runtime is self-reported. Results should be treated as research diagnostics, not independently validated leaderboard rankings. The exploration-confidence field is a redundancy-based heuristic, not a calibrated probability.

## Tests

```bash
python -m pip install pytest
python -m pytest -q
```

The test fixtures exercise software behavior; they do not establish scientific completeness of the reference.
