# Submission format

SaddleBench accepts saddle candidates as ASE `.traj` files. Each frame represents one candidate for one case from the published starting-minima dataset.

## Single-case submission

Provide a readable trajectory with:

- The same ordered atomic numbers as the selected starting minimum.
- Compatible simulation cell and periodic boundary conditions.
- Consistent fixed-atom constraints represented by ASE `FixAtoms`, where applicable.
- One frame per candidate; multiple frames and repeated geometries are accepted, but duplicates do not earn additional credit.

Use the case ID from the public `cases.csv` and specify the total elapsed search time, including unsuccessful attempts:

```bash
python scripts/score_from_hf.py \
  --case-id case0001 \
  --submission predictions.traj \
  --elapsed-seconds 3600 \
  --output score.json
```

Case scope is supplied using `--case-id`. Explicit trajectory metadata must not contradict the case's starting minimum, structure identifier or PES label. Keep atom indexing, cell and constraints consistent with the input. Record the calculator/model and computational hardware alongside the results for reproducibility.

## Multi-case submission

Create a CSV with the following columns:

```csv
case_id,trajectory,elapsed_seconds
case0001,case0001_results.traj,3600
case0002,case0002_results.traj,4800
```

Submit it using:

```bash
python scripts/score_from_hf.py \
  --submission-csv submission.csv \
  --output batch_score.json
```

Trajectory paths may be absolute or relative to the CSV's directory. A case should appear only once per batch.

## Matching and interpretation

The reference matching threshold is core-drift maxdev **≤ 0.2 Å**; topology is not used for identity. Reference reaction eligibility requires saddle force maximum ≤ 0.005 eV/Å, DoubleMin endpoint force maxima ≤ 0.0025 eV/Å, a connected reaction and a barrier < 1.5 eV. These tests apply to stored reference evidence; submission scoring does not independently certify a candidate saddle or repeat endpoint minimizations.

A match requires compatible fixed-atom metadata. Currently, some reference saddle geometries lack verified constraint metadata. In such cases a candidate may receive `NO_MATCH` because the reference comparison was blocked, not because the predicted coordinates differ. Reports for affected cases should not be interpreted as definitive coverage measurements until that reference issue is corrected.

The input and reference datasets are public. The scoring program downloads the reference without authentication; predictions remain local. The open-reference prototype is not a blind evaluation service.
