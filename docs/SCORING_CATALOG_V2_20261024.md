# SaddleBench cutoff-neutral catalog scorer — release candidate v2

The **general scoring engine** takes an immutable `saddlebench-reaction-catalog-v2`
reference. It knows nothing about registry schema versions, `traingletrain`, historical
fixed denominators, or a rate-relevance flag. An adapter creates records containing
stable reaction ID, structure/PES scope, geometry members, barrier, and explicit
evidence status. Structure/PES/maxdev are matching scope; starting-minimum
filtering is OFF by default and opt-in with `--require-starting-minimum`.

The new scoring energy policy defaults to **barrier strictly below 1.0 eV**.
`--barrier-max-eV 1.5` and `--no-barrier-cutoff` override it without changing
canonical scientific identities. Unknown energy is excluded if a cutoff applies
and can be retained (as unknown) only under the no-cutoff option.

**Science limitation:** The October lean export does not contain measured saddle
and endpoint fmax evidence, so the generated catalog explicitly marks
`science_verified=false` for every entry. Default strict scoring therefore does
not count these as scientifically certified reactions. Optional
`--allow-registry-reported-evidence` enables **EXPLORATORY** registry-based
coverage, with that status unmistakably recorded in the JSON. This is not a
scientific override of the historical 0.005 and 0.0025 eV/A requirements and
not permission to call the newly counted reactions experimentally validated.

Full scores can be reported under official policy only after a source adapter
receives provenance and measured force evidence sufficient to set
`science_verified=true`. Geometry matching remains exact core-drift maxdev;
thresholds 0.20/0.10/0.05 A and the certified skew-cell QR fallback remain
unchanged. For saddle-only prediction, multiple reactions sharing one saddle
can make reaction assignment ambiguous; greedy first-hit coverage is not
endpoint-resolved reaction identification.

## Run from local catalog

```bash
saddlebench score --catalog-root /path/to/catalog_candidate_af77f0143700c4a3 \
  --case-id 'oc20:EXAMPLE' --submission predictions.traj \
  --elapsed-seconds 100 --output score.json --no-html --no-image
```

To compare *registry-reported* connections (exploratory), append
`--allow-registry-reported-evidence`. To choose a different energy cutoff,
append `--barrier-max-eV 1.5` or `--no-barrier-cutoff`.

To score a published catalog without manually downloading it:

```bash
saddlebench score --catalog-repo traingletrain/saddlebench-reference-private \
  --catalog-prefix catalogs/af77f0143700c4a3 \
  --case-id 'oc20:EXAMPLE' --submission predictions.traj \
  --elapsed-seconds 100 --output new_score.json --no-html --no-image \
  --allow-registry-reported-evidence
```

The HF transport adapter downloads only the requested structure shards and
verifies their hashes; the scoring engine receives ordinary catalog records.
The old scorer's historical prefiltered reference and results remain available.
