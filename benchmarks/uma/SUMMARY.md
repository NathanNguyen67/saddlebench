# SaddleBench UMA baseline

The published cohort `bench_20261118` comprises 500 attempts per method for each of 36 seeds across 35 structures. The grouping matches the published design. Counts use one nearest eligible saddle within 0.20 Å per prediction and count a saddle group once per seed and method. Each method was scored against 4,062 eligible seed–saddle groups.

| Method | Attempts | Converged | Geometry scored | Converged without geometry | Reference groups found | Recall | Physical PES calls | PES calls per group | Summed search time (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sella_prfo_diag4_abs01` | 18,000 | 16,001 (88.89%) | 13,647 | 2,354 | 1,028 | 25.31% | 5,070,685 | 4,932.57 | 288,425.74 |
| `tsbfgs_dimer_rot4_frot01_r23_allprobes` | 18,000 | 17,098 (94.99%) | 14,247 | 2,851 | 962 | 23.68% | 8,206,182 | 8,530.33 | 418,836.37 |

The [per-structure × method CSV](baseline.csv) has 70 rows. All attempts report PES call counts and search wall time. One row found zero reference groups and has an empty PES-calls-per-group value. The 0.10 Å totals are 997 and 942 groups respectively, and the 0.05 Å totals are 996 and 940.

The 5,205 converged attempts with missing geometry still contribute to convergence, PES calls and search time. Their saddle IDs exist in `saddle_groups.parquet` without a corresponding public frame in `saddles.parquet`. Al accounts for 779 of the missing geometries across both methods. The recorded recalls describe exported representatives. These same searches contributed to the answer key, which can make recall optimistic for an independently evaluated method.

Run the quick example and full benchmark from the [public README](../../README.md). Compare the generated `baseline.csv` with the values above and the published CSV. The fixed Hugging Face revision is `acdac15b98282a9137a76c060913fcc0ce08462b`. The scorer measures its elapsed time separately from the search times.

## Clean-install reproduction

A clean checkout of `e551ba2b3dc410e3e87ea91f60effbd8c18e569a` ran the README commands in a fresh Python 3.11 venv on CPU with an empty Hugging Face cache. The quick command produced the expected counts. The full command produced a byte-for-byte match to the checked-in CSV, with SHA-256 `2cf3201995ca1c335338af05d9560d2d294705280a163b7fb47a5cc50506c722`. The full command took about 121 seconds including remaining downloads, peaked at about 499 MiB of RAM, and cached about 1.33 GiB of verified blobs across both commands. Hardware and transfer speed will affect timing.
