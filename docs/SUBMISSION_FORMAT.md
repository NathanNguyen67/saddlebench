# Submission format

SaddleBench accepts saddle candidates as ASE `.traj` files. Each frame represents one candidate for one case from the published starting-minima dataset.

## Single-case submission

Provide a readable trajectory with:

- The same ordered atomic numbers as the selected starting minimum.
- Compatible simulation cell and periodic boundary conditions.
- Fixed-atom constraints may be included as metadata but are not required for predicted saddle frames.
- One frame per candidate; multiple frames and repeated geometries are accepted, but duplicates do not earn additional credit.

Use the case ID from the public `cases.csv` and specify the total elapsed search time, including unsuccessful attempts:

```bash
python scripts/score_from_hf.py \
  --case-id case0001 \
  --submission predictions.traj \
  --elapsed-seconds 3600 \
  --output score.json
```

Case scope is supplied using `--case-id`. Explicit trajectory metadata must not contradict the case's starting minimum, structure identifier or PES label. Preserve atom indexing, cell and periodicity. Starting minima retain their fixed-atom constraints for Search, but repackaged saddle trajectories need not carry those constraints. Record the calculator/model and computational hardware alongside the results for reproducibility.

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

Fixed-atom constraint metadata is not a requirement for saddle matching. The original prototype rejected reference geometries with unknown constraint metadata, so earlier scores and `NO_MATCH` diagnostics may be invalid and should be recomputed with the revised matcher. Geometry matching still requires compatible ordered atoms, PBC and cell, and follows the published case scope. This policy change does not certify the reference PES or validate the completeness of eligible reactions.

The input and reference datasets are public. The scoring program downloads the reference without authentication; predictions remain local. The open-reference prototype is not a blind evaluation service.
