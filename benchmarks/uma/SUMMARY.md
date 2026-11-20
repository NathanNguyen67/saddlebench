# Confident literature benchmark

The bundled attempt table covers 36 seeds. Twenty pass the published confidence threshold and enter the benchmark. Each contributes 500 attempts per method. Thirty-five aligned-slide reference groups are excluded from this cohort, leaving 614 eligible groups per method.

| Method | Attempts | Converged | Public representative frames scored | Public groups found at 0.20 Å | Exact Search groups found at 0.20 Å |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sella_prfo_diag4_abs01` | 10,000 | 9,228 | 8,327 | 373 | 375 |
| `tsbfgs_dimer_rot4_frot01_r23_allprobes` | 10,000 | 9,807 | 8,359 | 341 | 348 |

The [public results CSV](baseline.csv) has 40 structure–method rows. The public run uses the representative geometry linked by the export and keeps the 2,349 converged attempts without a public representative in convergence and cost totals. With the optional exact Search geometry ZIP, all 19,035 converged frames can be scored. The two runs use the same confident reference and alignment exclusions. The CSV identifies the prediction geometry source.

The data come from the pinned UMA revision `acdac15b98282a9137a76c060913fcc0ce08462b`. See the [README](../../README.md) for the exact public commands, dataset selection, and instructions for scoring the ZIP. These attempts helped build the answer key, so their recall can be optimistic for a method evaluated independently.
