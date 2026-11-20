# SaddleBench UMA

SaddleBench scores predicted saddle geometries against a published per-seed reference for UMA-S-1.2.1. The scorer runs on a CPU with ASE, NumPy and PyArrow.

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

saddlebench score --case-id case0046 --submission benchmarks/uma/quick.traj \
  --confident-only --output quick.json
saddlebench baseline --attempts benchmarks/uma/attempts.parquet --output baseline.csv
```

The quick example is the published L05 rutile saddle `sad_3062f5ed8766c797cdb1` and a perturbed copy, each submitted twice. Expect four predictions scored, one eligible group, one match at each threshold, recall 1.0, and two outside-reference predictions forming one outside group.

The full benchmark writes one CSV row per structure and method. Expect 36,000 attempts and 33,099 converged Search geometries scored. The other 2,901 attempts did not converge. See the [baseline results](benchmarks/uma/SUMMARY.md) for the method totals and reproduction details. Allow about 1.5 GiB for the Hugging Face cache. The first run downloads the case list, seed and reference records, saddle geometry index, the reference trajectory shards, and the separate [benchmark Search trajectories](https://huggingface.co/datasets/traingletrain/saddlebench-uma-benchmark-search). The quick example additionally fetches the endpoint-pair table. Subsequent runs reuse those files and check them against the export manifest. Use a fresh output filename for each run. On a multicore machine, add `--workers 16` to the baseline command to score independent structures in parallel. Keep `OPENBLAS_NUM_THREADS=1` when using multiple workers.

## Score your own trajectory

Find the ID for your starting minimum in the pinned [case list](https://huggingface.co/datasets/traingletrain/saddlebench-uma/blob/acdac15b98282a9137a76c060913fcc0ce08462b/cases.csv). Use an ASE `.traj` or extended XYZ file containing one predicted saddle per frame, with the seed's atom order, simulation cell and periodic boundary conditions. The command assigns all frames to the specified case. Report search wall time in seconds and total physical potential energy surface (PES) calls if available.

```sh
saddlebench score --case-id case0046 --submission my-predictions.traj \
  --elapsed-seconds 532 --physical-pes-calls 1250 --output my-score.json
```

Add `--confident-only` to require a seed that reached the export's shuffled-tail confidence criterion. The command prints JSON and can also save it using `--output`. Use `--cache-dir` to set a separate Hugging Face cache location.

The eligible reference comes from the published per-seed records for seed-connected saddle groups with a complete DoubleMin, distinct endpoints and a representative barrier strictly below 1.0 eV. A prediction gets credit for the closest eligible group within core-drift maximum deviation of 0.20 Å, with ties resolved by saddle ID. A group counts once per seed and submission. The tighter 0.10 and 0.05 Å columns report the same rule at smaller thresholds. Recall is matches divided by eligible groups, or null when there are zero eligible groups.

An outside-reference prediction has no eligible geometry within 0.20 Å. The report gives both the number of these frames and the number of distinct outside groups. To count distinct outside groups, visit unmatched frames in submission order and add a representative only when it is farther than 0.20 Å from every earlier representative under the same geometry criterion. Repeated frames therefore contribute one outside group. Add `--recall-only` to `score` or `baseline` to skip outside-group counting while keeping recall and the total number of unmatched frames.

The score returns the selected original endpoint-pair record for each matched group, including its published observation counts. For matched reference groups, pair selection uses complete pairs with distinct endpoints. It prefers a connection to the case's seed, then the lowest sum of canonical `energy_a_eV` and `energy_b_eV`, then canonical endpoint IDs for ties. All original pairs are available in the public `endpoint_pairs.parquet`.

## Baseline data and metrics

The bundled [attempt table](benchmarks/uma/attempts.parquet) includes all columns and all 36,000 rows from `benchmark_cohort == 'bench_20261118'` at Hugging Face revision `acdac15b98282a9137a76c060913fcc0ce08462b`. The cohort contains 500 attempts per method per seed for 36 seeds spanning 35 literature, Al and Pt structures. The two method IDs are `sella_prfo_diag4_abs01` and `tsbfgs_dimer_rot4_frot01_r23_allprobes`. To fetch and filter the pinned public table directly, run the same command with the `--attempts` option omitted.

Each converged row is scored using that attempt’s saved Search geometry, joined by attempt sequence number. This includes 5,205 attempts whose saddle group has no completed DoubleMin. A geometry match credits the eligible reference group and its published reaction evidence. It does not independently validate the predicted saddle’s endpoints. All attempts contribute to the denominator for convergence and to the summed reported search wall time in seconds. Reference group counts and eligibility are computed per seed and summed within the structure and method. An empty cost-per-group field means the run found zero eligible groups or some PES call counts are missing. The scorer prints its elapsed time separately from the search times in the CSV.

These searches helped build the answer key. Their recall can be optimistic for a method evaluated independently. [Pinned UMA dataset and export documentation](https://huggingface.co/datasets/traingletrain/saddlebench-uma/tree/acdac15b98282a9137a76c060913fcc0ce08462b)

## Reference limitations

- The campaign omitted Hessian checks and the stored DoubleMin endpoints are unverified. At the production 0.25 Å push, LBFGS sometimes stopped at a neighboring first-order saddle. Three Al examples are `min_d1023d…`, `min_088eac…` and `min_1ff305…`, each about 0.168 eV above its seed. The export preserves all observed pairs and flags groups with more than two distinct endpoints as `multi_endpoint_saddle`.
- Near-degenerate or symmetry-equivalent minima can have different identities. Roughly 10,000 saddles outside the reference have an endpoint within 20 meV of the seed and farther than 0.20 Å. For C06_flat, two N₂ dissociation saddles with a barrier of about 0.954 eV came from a minimum 0.018 eV above the seed and 0.46 Å away. The published seed-specific reference excludes them.

## Previous scoring commands

The former default coverage leaderboard is available as `saddlebench coverage-score`, including its submission CSV format. Explicit `--reference` scoring, `legacy-score`, `provisional-score` and `render` remain available for the earlier reference formats. [Previous release instructions](https://github.com/NathanNguyen67/saddlebench/blob/afa190746a5acc97e732219812cd7a2485161625/README.md)
