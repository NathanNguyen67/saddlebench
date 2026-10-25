# Scoring scope and dataset independence (candidate, 2026-10-24)

## Changed scoring policy (explicit scientific scoring-scope change)

- `saddlebench score` (default three-threshold coverage) and `saddlebench provisional-score` (`score`, `score_detailed`, `coverage` modes) now **ignore starting-minimum ID by default** when selecting reaction candidates. `--require-starting-minimum` restores the historical exact-start restriction.
- Structure ID and PES ID remain mandatory scoring scopes. Exact per-pair geometric identity remains **core-drift maxdev**, with the original 0.2, 0.1 and 0.05 Å coverage tiers; no topology identity or approximation replaces maxdev.
- The scorer consumes a **pre-adjudicated reaction reference**. It does not decide canonical DoubleMin validity or impose a new energy threshold on its own. Its eligible-reaction count `K` comes from *distinct reaction IDs in the selected reference and structure/PES scope*, not from a `known_reaction_count` column in a historical cases CSV. This can change scores and makes this a new scoring-scope policy. Output includes `scoring_scope_policy` or `starting_minimum_filter` metadata; original JSON field names are retained.
- Missing starting-minimum information in reference rows does not prevent matching under the default policy. Missing start in a submitted trajectory is accepted if structure/PES are provided. `--require-starting-minimum` requires the starting-minimum ID and scopes accordingly.
- Reference snapshot verification is dynamic and checksum-bound, not hard-coded to a historical registry SHA, 123 cases or 2,387 reactions. The Hugging Face repositories are named only by the optional downloader adapter (`saddlebench.reference.hf_compat`); the scorer accepts a separate, locally supplied reference and cases CSV.
- The first-hit scorer **does not** resolve ambiguous DoubleMin reactions that share a saddle; it reports greedy first-hit credits with the existing caveats. The submitted saddle geometry does not prove the predicted DoubleMin endpoints. No false claim of full reaction identification is made.

## Did not change

- Scientific eligibility, canonical Powell-damped DoubleMin protocol, Hessian order/force tolerances, canonical reaction identities, source registry, published reference, 1.5 eV historical reference construction, output score formula, and prepared-skew MIC kernel.
- The old archival `saddlebench legacy-score` path is not silently reinterpreted as the new policy; it retains its historical record-level contract. The public `score` and `provisional-score` routes use the default-off policy.
- The proposed **new 1.0 eV default barrier cutoff** is NOT implemented in this patch: the existing reference is prefiltered. That separate scoring-method change requires a cutoff-neutral verified reaction catalog; otherwise the scorer would silently omit excluded reactions.

## Dataset backfill (provenance-preserving implementation)

`tools/backfill_associated_starts.py` reads the completed lean JSONL export and writes a **new immutable projection**. For each row with a uniquely registered minimum for its structure, it sets:

- `associated_starting_minimum_id`: actual registered minimum ID for the row's structure.
- `associated_starting_minimum_id_basis`: `DERIVED_UNIQUE_REGISTERED_MINIMUM_FOR_STRUCTURE_NOT_OBSERVED_SEARCH_ORIGIN`.

This never fills `observed_starting_minimum_ids`, never asserts a search actually began at that minimum, and never changes the original `starting_minimum_id` field. It rejects contradictory existing IDs and any structure with zero/multiple registered minima for canonical saddle or reaction rows. DoubleMin observations with no resolvable structure remain unannotated and counted in the report. It does not infer canonical reaction IDs for unassigned observations.

The generated projection is **not** a rewrite of the production SQLite registry or existing Hugging Face source. Once verified, it may be considered for separate versioned dataset publication.

## Run locally (against a supplied verified reference)

```bash
saddlebench score --case-id case0006 --submission predictions.traj \
  --elapsed-seconds 532 --reference-root /path/to/reference_snapshot \
  --cases-csv /path/to/cases.csv --output new_result.json
```

Add `--require-starting-minimum` to select the historical scope. For the provisional metrics use `saddlebench provisional-score` with the same option. These are offline reference examples; the data must follow the existing verified reference schema.

## Limitations and gates

- The full, live 217,716-saddle export is **not** in this ZIP. Only the code and locally synthetic tests are bundled.
- The derived projection requires a real LS6 run, validating actual source structure and outputs before any HF update.
- The public scorer still needs a **new cutoff-neutral reference builder** before it can score the newly published full registry or apply a 1.0 eV policy without conflating eligibility with the dataset's historical filter.
- Before publication, compare historical-scoped and new structure/PES-only scores on a controlled fixed reference, and assign separate policy/version identifiers in leaderboard metadata.
