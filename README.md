# SaddleBench

SaddleBench evaluates predicted saddle-point geometries against a published reference of reaction pathways. The Python scorer reports matched reaction identities, coverage, duplicate discoveries, and diagnostic classifications. It does not run saddle searches or energy calculations.

This is an **open-reference research prototype**. The reference data and scoring code are public. The current dataset is neither exhaustive nor independently certified as a benchmark ground truth.

## Data

| Dataset | Description |
| --- | --- |
| [Starting minima](https://huggingface.co/datasets/traingletrain/saddlebench-prototype) | 123 starting structures in ASE trajectory format, case metadata, and submission examples. |
| [Scoring reference](https://huggingface.co/datasets/traingletrain/saddlebench-reference-private) | 2,387 eligible reference reaction identities and a diagnostic catalog of 83,529 registered saddles. |

Both datasets are public. The `-private` suffix is part of the reference dataset's original name; authentication is not required.

The released reference corresponds to registry publication watermark **23,134** and registry SHA-256 `85d37e55448b544473b34e7bb244823e80ddc85091bbe6ae3e771c7beb4b0a41`. These counts describe this snapshot only, not any later registry publication.

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

Run a saddle-search method from the selected structure and save the proposed saddle geometries to an ASE `.traj` file, with one frame per candidate. Preserve atomic ordering, cell and periodic boundary conditions. The supplied minimum retains its constraints for the search; constraints on the output saddle frames are optional and are not used in matching.

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

Matching uses core-drift **maxdev ≤ 0.2 Å**, with compatible atom ordering, cell, periodic boundary conditions and case scope. Fixed-atom constraint metadata is retained when present but does not affect saddle matching or credit. This is a change from the original prototype constraint-gated matching policy; scores produced by the two policies should not be compared without rescoring. Connectivity topology is not used to declare geometric identity. The scorer does not rerun optimizations or Hessian calculations.

## Reference limitations

**Constraint metadata and score versions.** The published reference includes saddle geometries whose fixed-atom metadata is unknown. The original scorer rejected those comparisons, causing some zero-match reports without any geometric distance evaluation. The current matcher ignores constraint metadata *for saddle-to-saddle comparison only*; original results should be rescored. Starting minima preserve their verified constraints, and their use during Search remains the search method's responsibility. This matching-policy update does not verify unknown calculator/PES provenance or make the published reference exhaustive.

**Coverage.** The reference consists of registered observations, not an exhaustive enumeration of possible saddles. An unknown candidate is not automatically incorrect.

**Potential-energy surface.** The reference's calculator/PES scope has not been independently verified. Scores obtained using different calculators are not necessarily comparable.

**Benchmark use.** The reference is public and runtime is self-reported. Results should be treated as research diagnostics, not independently validated leaderboard rankings. The exploration-confidence field is a redundancy-based heuristic, not a calibrated probability.

## Tests

```bash
python -m pip install pytest
python -m pytest -q
```

The test fixtures exercise software behavior; they do not establish scientific completeness of the reference.

## Saddle-group population metadata

For each geometrically matched candidate, `entries[].matched_saddle_groups` lists any matching registered saddle groups and their `registered_saddle_count`. The count is the number of distinct canonical saddle IDs in the full published `saddles.jsonl` for that structure/group, including saddles ineligible for reaction credit. It is not the count of discovered predictions, supporting reference geometries, reactions, or eligible observations. When a prediction geometrically matches multiple groups, they are listed separately. A `null` count indicates historical reference data without a complete recorded population. This metadata does not affect credit or penalties.

All 123 published starting minima are verified to retain their original fixed-atom constraints after an ASE trajectory round trip. The reference is at publication watermark 23,134.
