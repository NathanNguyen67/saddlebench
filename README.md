# SaddleBench — open-source prototype scorer

**Source code is public; scoring reference data are PRIVATE.** This repository does
not contain any reference saddle geometries, DoubleMin reaction identities or
private diagnostic catalog. It uses the frozen published-registry reference hosted
privately at `traingletrain/saddlebench-reference-private` (HF dataset), and
public starting minima at https://huggingface.co/datasets/traingletrain/saddlebench-prototype.

Prototype source baseline: SaddleBench RC9, registry watermark 7522,
registered canonical registry SHA256 `e33f7ac55cec2bc208fd1bb4ee74d047224fa072a7a43be1effdb4891c495c88`.
The registry-derived reference currently contains 2,231 eligible reaction identities,
and a 73,839-saddle diagnostic index. This is a historical snapshot, not a PES-certified
or complete ground-truth dataset.

## Install on ANY cluster (CPU side of a GPU search)

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install huggingface_hub
python -m pytest -q  # optional: pip install pytest
```

The scorer requires ASE and NumPy, but NO GPU, Flux, Slurm, FairChem, or LS6
runtime. It works with `.traj` files produced on another GPU cluster.

## Get a starting minimum

Visit the public dataset, read `cases.csv`, and download `minima/<case_id>.traj`.
To fetch via the CLI (with `huggingface_hub` installed):

```bash
hf download traingletrain/saddlebench-prototype minima/case0001.traj --repo-type dataset --local-dir ./inputs
```

You can inspect/download cases with your browser. The target minimum has an
explicit set of ASE constraints. Provide your predicted saddles as a readable ASE
`.traj` for the SAME case, respecting the same atomic ordering/cell/PBC/constraints,
and give actual elapsed seconds. The `pes_id` in the prototype remains an
UNVERIFIED scope placeholder, so compare under a consistent calculator.

## Official-style scoring with your *authorized* private reference

First authenticate locally on your research cluster as the owner (never share a token):

```bash
hf auth login
```

Then:

```bash
python scripts/score_from_hf.py \
 --case-id case0001 \
 --submission ./predicted_saddles.traj \
 --elapsed-seconds 3600 \
 --output ./score_case0001.json
```

This script downloads the public `cases.csv` and the private reference snapshot
*only with authorization*; verifies SHA256, the frozen registry identity and
private manifest; runs `saddlebench.prototype.release_mvp.grade_case` locally;
then writes detailed JSON results. Use `--cache-dir /path/to/cache` to retain
the private data between submissions. Running it will not upload predictions.
Avoid placing the private HF cache in a shared or publicly readable directory.

For multiple selected cases, supply `--submission-csv file.csv` instead of
`--case-id/--submission/--elapsed-seconds` and see the included
`docs/SUBMISSION_FORMAT.md`. Scoring a private reference requires a HF token
that grants read access to that dataset. **Ordinary public users do not have
that access yet.** A trusted hosted submission service is a future requirement
for fully public self-service scores.

## Scoring definition (prototype)

For each selected case with K>0 known eligible reactions, U unique reaction
identities found, B unique registered bad saddle identities, and elapsed
hours T: score = `100 * (U/K)**2 * U/(U+2*B) / T`. High coverage is rewarded,
repeated credited reactions add no credit. Known higher-order or connected
high-barrier saddles cause one penalty per identity even if both classifications
apply. Known disconnected first-order, unknown and ambiguous matches are
neutral. For K=0, coverage and score are undefined. The reference is filtered
at saddle fmax <=0.005 eV/A, both DoubleMin endpoint fmax <=0.0025 eV/A,
connected reaction with barrier strictly <1.5 eV. Non-Powell historical
DoubleMin is allowed in `all_saddles`; canonical-only is a future switch.
Matching uses the existing core-drift maxdev (<=0.2 A), not topology.

Exploration confidence is an uncalibrated shuffle-based redundancy heuristic,
NOT a statistical guarantee. Search runtime is self-reported; this is NOT a
trusted public leaderboard. PES identity has not been independently verified.

## Testing the scorer on a SECOND GPU cluster

1. Use a GPU to search from a chosen `caseNNNN.traj` using the intended PES.
2. Save predictions to `predicted_saddles.traj` with compatible ASE metadata.
3. Run the scorer above (CPU only). It downloads private data on first use.
4. Confirm the JSON includes reaction credit, duplicate handling, higher-order,
   high-barrier, unknown/ambiguous and coverage/score categories as applicable.
5. For a genuine end-to-end test, report the method/PES, GPU model, runtime,
   case ID, prediction count and scored JSON; do not share the HF token.

Repository: https://github.com/NathanNguyen67/saddlebench (once published).

This project is independently packaged from standalone SaddleMill and does not
modify SaddleMill, Benchmark or the production registry.
