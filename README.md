# SaddleBench UMA

Score saddle-search trajectories against a published answer key on CPU with ASE, NumPy and PyArrow. A seed is eligible if either its actual repeat tail or its shuffled mean tail reaches 200. It uses saddle geometries to match predictions and excludes aligned-slide reference groups.

## Install and run

```sh
git clone --branch uma-20261119 --single-branch https://github.com/NathanNguyen67/saddlebench.git
cd saddlebench
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install "numpy==1.26.4" "scipy==1.13.1" "pyarrow==21.0.0" \
  "ase==3.29.0" "huggingface_hub==2.4.0" .
export OPENBLAS_NUM_THREADS=1
export HF_HUB_DISABLE_XET=1

saddlebench score --case-id case0046 --submission benchmarks/uma/quick.traj --recall-only
saddlebench baseline --dataset literature --attempts benchmarks/uma/attempts.parquet \
  --recall-only --workers 4 --output baseline.csv
```

The quick example should score four frames, find one of one eligible reference group at all three cutoffs, and report two unmatched frames. The public baseline should report **28,000 attempts**, **26,284 converged**, and **22,880 geometries scored** using the public group representatives. It uses the 28 confident seeds in the historical 36-seed prefilled cohort. [Baseline results](benchmarks/uma/SUMMARY.md)

## Choose a dataset and download seeds

```sh
saddlebench cases
saddlebench cases --dataset lit_oc20
saddlebench seed --dataset lit_oc20 --output-dir seeds
saddlebench seed --case-id case0046 --output case0046.traj
```

`cases` prints one row per confident seed, with its dataset, parent structure, eligible reference count, and which tail criteria it passed. `seed --dataset` writes one ASE trajectory per listed case. A single published starting minimum is available by case ID, including cases outside the confident benchmark. The trajectory retains the exported cell, atom order, and attached ASE constraints.

| Dataset | Structures |
| --- | --- |
| `lit_oc20` | Literature metal surfaces and the Al and Pt benchmark structures |
| `lit_oc22` | Literature oxide surfaces |
| `lit_bulk`, `lit_battery` | Literature bulk and battery systems |
| `oc20`, `oc22` | Confident cases from those datasets |
| `lemat`, `mp20bat` | Confident cases from those bulk datasets |
| `literature` | All four `lit_*` datasets together |
| `all` | All 125 confident seeds across the datasets |

Al and Pt belong to `lit_oc20`. Pt qualifies through its actual tail of 1,083 repeats. Al falls below both thresholds, so its prefilled attempts remain available in the full attempt table and are omitted from benchmark scoring.

## Score your own predictions

Use one predicted saddle per frame in ASE `.traj` or extended XYZ, with the starting minimum’s atom order, simulation cell, and periodic boundary conditions. For one case, the scorer reports JSON. For a dataset, name each file `CASE_ID.traj` or `CASE_ID.extxyz` and provide one file per confident case. The output is a CSV with one row per case.

```sh
saddlebench score --case-id case0046 --submission my-predictions.traj \
  --recall-only --output my-score.json
saddlebench score --dataset lit_oc20 --submission-dir predictions \
  --recall-only --output my-dataset.csv
```

For one case, `--elapsed-seconds` and `--physical-pes-calls` record optional search costs. `--recall-only` reports all unmatched frames and skips their distinct-group count. Omit it to group unmatched frames against earlier unmatched representatives in submission order. Unmatched frames can include predictions corresponding to excluded aligned slides.

Each prediction is assigned to the closest eligible saddle group with **core-drift maximum deviation at most 0.20 Å**. Saddle ID breaks distance ties. Each group counts once per seed and submission. The 0.10 and 0.05 Å fields use smaller cutoffs. Recall is found groups divided by eligible groups, with an empty or null value for a seed with zero eligible groups.

### Benchmark reference

The pinned dataset supplies the actual repeat tail in scheduling order and the confidence flag for the mean of 400 shuffled tails. SaddleBench accepts either criterion and reports which passed. The dataset also supplies the eligible saddle groups. Eligibility requires a seed-connected pair with complete DoubleMin, distinct endpoints and a representative barrier strictly below 1.0 eV. The scorer uses geometry for identity. Endpoint energies and the choice between multiple product minima play no role in matching or credit.

An aligned slide contains at least six free atoms in a moving component. Following SaddleMotif Explorer v2, moved atoms displace more than 0.5 Å after periodic alignment and drift removal. Edges join moved atoms initially closer than 3.3 Å whose pairwise distance changes by less than 0.15 Å. The directions within the group must have a resultant of at least 0.9. A reference saddle is excluded if **all** its eligible seed-connected endpoint pairs give an aligned slide. The filter uses the public canonical endpoints and excludes 194 of 4,908 confident reference groups. [SaddleMotif rule](https://github.com/NathanNguyen67/SaddleMotif/blob/93c30fa/explorer_v2/build/motion.py)

## Prefilled benchmark inputs

The bundled [attempt table](benchmarks/uma/attempts.parquet) preserves all 36,000 attempts from `bench_20261118` at UMA revision `acdac15b98282a9137a76c060913fcc0ce08462b`. It contains 500 attempts per method per seed for 36 seeds across 35 parent structures. Omit `--attempts` to fetch and filter the same pinned public table.

`baseline --dataset lit_bulk` (or another `lit_*` dataset) scores only its confident seeds with prefilled attempts. Other datasets can be scored using your own trajectories through `score --dataset`.

The public run uses the exported saddle-group representatives. For the 3,404 converged attempts on confident seeds with no public representative, the CSV records the geometry as unavailable while retaining the attempt in convergence, PES calls, and search time. If you have the exact Search benchmark trajectory ZIP, the same command can use it directly:

```sh
saddlebench baseline --dataset literature --attempts benchmarks/uma/attempts.parquet \
  --geometry-zip saddlebench_uma_benchmark_trajectories.zip --recall-only \
  --workers 4 --output baseline-exact.csv
```

The ZIP has one trajectory per case and method, ordered by converged attempt sequence. The exact run scores all 26,284 converged predictions on confident seeds. The two geometry sources can produce different recall and are labeled in the CSV. Both preserve all 28,000 qualifying attempts for convergence and cost accounting. Search wall time is summed in seconds and printed separately from scorer runtime.

These attempts helped build the answer key, so their recall can be optimistic for an independently evaluated method. [Pinned UMA dataset](https://huggingface.co/datasets/traingletrain/saddlebench-uma/tree/acdac15b98282a9137a76c060913fcc0ce08462b)

## Cache and reference limitations

SaddleBench downloads only the structure trajectories used by the command and verifies source data against the pinned manifest. It caches decoded references and indexes in `saddlebench-uma-arrays` in the Hugging Face Hub cache. Use `--cache-dir` to choose a local directory. Scoring other datasets adds their structure shards to the cache as needed. Keep `OPENBLAS_NUM_THREADS=1` when using multiple workers.

Campaign DoubleMin endpoints are unverified. At the production 0.25 Å push, LBFGS sometimes stopped at a neighboring first-order saddle. Near-degenerate or symmetry-equivalent minima can also have different identities, so a saddle can connect to a nearby minimum and remain outside the published seed-specific reference.

The earlier reference formats are available through `coverage-score`, `legacy-score`, `provisional-score`, and `render`. [Previous release instructions](https://github.com/NathanNguyen67/saddlebench/blob/afa190746a5acc97e732219812cd7a2485161625/README.md)
