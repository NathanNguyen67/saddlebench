# Confident literature benchmark

The bundled attempt table covers 36 seeds. Twenty-eight qualify through an actual tail of at least 200 repeats or a mean shuffled tail of at least 200. Each contributes 500 attempts per method. Fifty-one aligned-slide reference groups are excluded from this cohort, leaving 1,265 eligible groups per method.

| Method | Attempts | Converged | Public representative frames scored | Public groups found at 0.20 Å | Exact Search groups found at 0.20 Å |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sella_prfo_diag4_abs01` | 14,000 | 12,798 | 11,352 | 589 | 591 |
| `tsbfgs_dimer_rot4_frot01_r23_allprobes` | 14,000 | 13,486 | 11,528 | 548 | 557 |

The [public results CSV](baseline.csv) has 56 structure–method rows. The public run uses the representative geometry linked by the export and keeps the 3,404 converged attempts without a public representative in convergence and cost totals. With the optional exact Search geometry ZIP, all 26,284 converged frames can be scored. Both runs use the same confident reference and aligned-slide exclusions. The CSV identifies the prediction geometry source.

The data come from the pinned UMA revision `acdac15b98282a9137a76c060913fcc0ce08462b`. See the [README](../../README.md) for the public commands, dataset selection, and instructions for scoring the ZIP. These attempts helped build the answer key, so their recall can be optimistic for an independently evaluated method.
