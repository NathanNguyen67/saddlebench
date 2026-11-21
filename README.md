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

saddlebench score --case-id case0046 --submission benchmarks/uma/quick.traj \
  --recall-only --html quick.html
saddlebench baseline --dataset literature --attempts benchmarks/uma/attempts.parquet \
  --recall-only --workers 4 --output baseline.csv --html baseline.html
```

The quick example should score four frames, find one of one eligible reference group at all three cutoffs, and report two unmatched frames. The public baseline should report **28,000 attempts**, **26,284 converged**, and **22,880 geometries scored** using the public group representatives. It uses the 28 confident seeds in the historical 36-seed prefilled cohort. [Baseline results](benchmarks/uma/SUMMARY.md)

### How long it takes

Measured on a 16-core LS6 node with the cache on local disk (Nov 21, 2026): the full `baseline` command above takes about 70 s with `--workers 4` and 40 s with `--workers 16`, plus a first-run download of about 2 GB. Most of that time is the 409,348 SaddleMotif proposal frames behind the two motif-augmented rows; `--no-motif` takes about 17 s. Scoring costs 0.2–0.5 ms per predicted frame, the same for arbitrary dataset saddles as for benchmark frames. `score` on one case takes about 17 s the first time, mostly scoring the published baseline methods for comparison, and about 4 s afterwards, because that comparison is cached. Keep the Hugging Face cache on a local disk: on a Lustre scratch file system, reads are several times slower.

## Choose a dataset and download seeds

```sh
saddlebench cases
saddlebench cases --all-seeds
saddlebench cases --dataset lit_oc20
saddlebench seed --dataset lit_oc20 --output-dir seeds
saddlebench seed --case-id case0046 --output case0046.traj
```

`cases` prints one row per confident seed, with its dataset, parent structure, eligible reference count, and which tail criteria it passed. Add `--all-seeds` to include seeds below threshold. Their confidence and eligible group fields are blank. `seed --dataset` writes one ASE trajectory per confident case. A single published starting minimum is available by case ID, including cases outside the confident benchmark. The trajectory retains the exported cell, atom order, and attached ASE constraints.

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

1. Pick a confident case from `saddlebench cases` and download its starting structure with `saddlebench seed --case-id CASE_ID --output seed.traj`.
2. Run your saddle search with that seed and save one predicted saddle per frame in ASE `.traj` or extended XYZ. Preserve the starting minimum’s atom order, cell, and periodic boundary conditions.
3. Run `saddlebench score` on your file. For a whole dataset, place exactly one `CASE_ID.traj` or `CASE_ID.extxyz` per confident case into a directory. One-case results are JSON and dataset results are CSV.

```sh
saddlebench score --case-id case0046 --submission my-predictions.traj \
  --recall-only --output my-score.json --html my-score.html
saddlebench score --dataset lit_oc20 --submission-dir predictions \
  --recall-only --output my-dataset.csv --html my-dataset.html
```

Open the HTML in a browser. Its dataset selector filters both results and the catalog of all published seeds. The seed filter shows eligible and below-threshold cases. To populate results across all datasets with your own predictions, use `score --dataset all` with a file for every confident case. Add `--include-unconfident` to `score` or `baseline` to include published seeds below the threshold. A dataset submission then needs one file for every selected seed. Use the same flag with `seed --dataset` to download those starting minima. Predictions are scored locally.

The `score` report shows your submission beside the four published baseline methods on the same seeds: Sella PRFO, TS-BFGS dimer, and each one augmented with SaddleMotif proposals. These rows come from the prefilled attempts (bundled table in a checkout, otherwise the pinned public table) and the published proposals, so a seed without prefilled attempts shows your row only. Use `--no-baseline` to leave them out, and `--workers N` to compute them in parallel the first time. The CSV or JSON output contains your submission only.

### Structure explorer

Every HTML report writes a `*-explorer/` folder beside it. Keep the report and the folder together. Its index shows one card per scored structure, animating a representative reaction on the starting minimum, with how many reference saddles the chosen method found and missed. A table view lists the same structures. Each structure page groups saddles into SaddleMotif classes. Reference saddles are marked **Found** or **Missed** for the method and cutoff chosen at the top. Use the "Missed reference saddles" filter, or click a structure name in the results table, to go straight to the misses. A class opens its reactions with the reactant → saddle → product animation, energies, Search-mode curvature, DoubleMin endpoints and products. Literature structures carry their citation and intended reaction under the title.

Viewer controls: **Supercell** repeats the cell, **Fade others** makes atoms that are not moving, pinned or selected translucent, and clicking an atom pins it with its index (or type indices under Pin atoms). Explorer data are SaddleMotif Explorer payloads built from the same campaign database as the pinned export. They are fetched once per release and checksum-verified. The interpolation between stored frames is schematic, not a computed path. Curvature is along the final Search mode, not a Hessian eigenvalue.

Glossary used in the explorer: a **saddle** is one saddle group with a complete DoubleMin. A **reaction** is a saddle with the two minima its DoubleMin reached. A **seed reaction** is a distinct pair of end minima that includes the starting minimum, barrier below 1 eV. A **reference saddle** is a seed-reaction saddle that the benchmark scores; aligned slides are excluded.

For one case, `--elapsed-seconds` and `--physical-pes-calls` record optional search costs. `--recall-only` reports all unmatched frames and skips their distinct-group count. Omit it to group unmatched frames against earlier unmatched representatives in submission order. Unmatched frames can include predictions corresponding to excluded aligned slides.

Each prediction is assigned to the closest eligible saddle group with **core-drift maximum deviation at most 0.20 Å**. Saddle ID breaks distance ties. Each group counts once per seed and submission. The 0.10 and 0.05 Å fields use smaller cutoffs. Recall is found groups divided by eligible groups, with an empty or null value for a seed with zero eligible groups.

### Benchmark reference

The pinned dataset supplies the actual repeat tail in scheduling order and the confidence flag for the mean of 400 shuffled tails. SaddleBench accepts either criterion and reports which passed. The dataset also supplies the eligible saddle groups. Eligibility requires a seed-connected pair with complete DoubleMin, distinct endpoints and a representative barrier strictly below 1.0 eV. The scorer uses geometry for identity. Endpoint energies and the choice between multiple product minima play no role in matching or credit.

An aligned slide contains at least six free atoms in a moving component. Following SaddleMotif Explorer v2, moved atoms displace more than 0.5 Å after periodic alignment and drift removal. Edges join moved atoms initially closer than 3.3 Å whose pairwise distance changes by less than 0.15 Å. The directions within the group must have a resultant of at least 0.9. A reference saddle is excluded if **all** its eligible seed-connected endpoint pairs give an aligned slide. The filter uses the public canonical endpoints and excludes 194 of 4,908 confident reference groups. [SaddleMotif rule](https://github.com/NathanNguyen67/SaddleMotif/blob/93c30fa/explorer_v2/build/motion.py)

## Prefilled benchmark inputs

The bundled [attempt table](benchmarks/uma/attempts.parquet) preserves all 36,000 attempts from `bench_20261118` at UMA revision `acdac15b98282a9137a76c060913fcc0ce08462b`. It contains 500 attempts per method per seed for 36 seeds across 35 parent structures. Omit `--attempts` to fetch and filter the same pinned public table.

`baseline --dataset lit_bulk` (or another `lit_*` dataset) scores only its confident seeds with prefilled attempts. Other datasets can be scored using your own trajectories through `score --dataset`.

The baseline HTML opens on overall method results, with per-structure rows in a dropdown. Each eligible reference group has rate weight `exp(-barrier_eV / (k_B * 423 K))`, using a common prefactor. Rate coverage is the weight of matched groups divided by the weight of eligible groups. Unweighted coverage uses counts. At each of 0.20, 0.10 and 0.05 Å, the reported **equal-structure** value averages the per-structure fractions and the **pooled** value divides the sums of the numerators and denominators. Structures with zero denominator do not enter the average. Predictions compared includes matching and nonmatching predictions.

The coverage-versus-time plot places each first hit at its cumulative reported attempt time in sequence order. This is serial-equivalent search work, including unsuccessful attempts, and differs from parallel wall-clock elapsed time. SaddleMotif proposals are added as a single batch at the end of generation. Generation costs come from the runner's measured interval, or the start/end file timestamps for earlier runs. Unavailable time displays as N/A. The CSV retains reported PES costs, with a blank cost per hit when unavailable.

The public run uses the exported saddle-group representatives. For the 3,404 converged attempts on confident seeds with no public representative, the CSV records the geometry as unavailable while retaining the attempt in convergence, PES calls, and search time. If you have the exact Search benchmark trajectory ZIP, the same command can use it directly:

```sh
saddlebench baseline --dataset literature --attempts benchmarks/uma/attempts.parquet \
  --geometry-zip saddlebench_uma_benchmark_trajectories.zip --recall-only \
  --workers 4 --output baseline-exact.csv
```

The ZIP has one trajectory per case and method, ordered by converged attempt sequence. The exact run scores all 26,284 converged predictions on confident seeds. The two geometry sources can produce different recall and are labeled in the CSV. Both preserve all 28,000 qualifying attempts for convergence and cost accounting. Search wall time is summed in seconds and printed separately from scorer runtime.

The public baseline automatically fetches and caches the published SaddleMotif proposal files for its selected cases and scores four methods. Use `--no-motif` to compare the two original methods only. Use `--motif-dir proposals` to override the published proposals. A local override contains the generated ASE trajectory at `proposals/METHOD_ID/CASE_ID.traj`, using the original method IDs `sella_prfo_diag4_abs01` and `tsbfgs_dimer_rot4_frot01_r23_allprobes`. The CLI also discovers local proposals in `benchmarks/uma/motif`, or in `motif` beside a supplied `--attempts` file. The corresponding augmented row scores the original method's predictions plus every proposal in that file. Proposal geometry matches measure recall of the supplied guesses. An optimizer would be needed to establish which guesses converge to saddles. The report gives coverage only for the cases with supplied proposals, so compare methods on the same structures.

With the exact Search ZIP and a compatible SaddleMotif source tree and environment, generate the inputs with:

```sh
python scripts/generate_uma_motif.py --saddlemotif /path/to/SaddleMotif \
  --geometry-zip saddlebench_uma_benchmark_trajectories.zip \
  --attempts benchmarks/uma/attempts.parquet --output-dir proposals --workers 4
saddlebench baseline --geometry-zip saddlebench_uma_benchmark_trajectories.zip \
  --motif-dir proposals --recall-only --workers 4 --output motif.csv --html motif.html
```

Generation uses a separate library for each method and seed, built from the first exact converged frame in each Search saddle group from the 500 attempts. Any converged frame without a group ID is used individually. The library is placed on its own starting seed. The generator keeps every proposal in SaddleMotif's collated output and skips the starting-minimum frame. Pass `--include-unconfident` to both commands to generate and compare the full prefilled cohort.

Keep the proposal directory to reuse it without generating again. A compact cache can retain just the two method directories, `timings.json` if available, and the matching exact Search archive saved as `exact-search.zip`. When the cache is supplied with `--motif-dir`, the CLI discovers that archive automatically. Resume generation into an existing output directory only if the source version and input files are unchanged.

These attempts helped build the answer key, so their recall can be optimistic for an independently evaluated method. [Pinned UMA dataset](https://huggingface.co/datasets/traingletrain/saddlebench-uma/tree/acdac15b98282a9137a76c060913fcc0ce08462b)

## Cache and reference limitations

SaddleBench downloads only the structure trajectories used by the command and verifies source data against the pinned manifest. It caches decoded references and indexes in `saddlebench-uma-arrays` in the Hugging Face Hub cache. Use `--cache-dir` to choose a local directory. Scoring other datasets adds their structure shards to the cache as needed. Keep `OPENBLAS_NUM_THREADS=1` when using multiple workers.

Campaign DoubleMin endpoints are unverified. At the production 0.25 Å push, LBFGS sometimes stopped at a neighboring first-order saddle. Near-degenerate or symmetry-equivalent minima can also have different identities, so a saddle can connect to a nearby minimum and remain outside the published seed-specific reference.

The earlier reference formats are available through `coverage-score`, `legacy-score`, `provisional-score`, and `render`. [Previous release instructions](https://github.com/NathanNguyen67/saddlebench/blob/afa190746a5acc97e732219812cd7a2485161625/README.md)
