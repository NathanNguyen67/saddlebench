# Associated-start derived projection provenance

Target host: TACC Lonestar6 `vm-small` interactive compute shell (CPU-only).
Python: `/work/11588/ndn454/ls6/conda_libraries/saddlemill/bin/python`.
NumPy overlay: `/work/11588/ndn454/ls6/conda_libraries/saddlemill_vm_numpy`.
Environment reference: `LS6_ENVIRONMENTS_20261023_v2.md`.

The complete source is the LS6 lean JSONL export; output is a **new** projection. The script reads original shards, validates each shard row count, reconstructs a unique `structure_id -> minimum_id` map, cross-checks each reaction-observation structure against linked reaction and saddle structures, then writes only derived association fields to its own copy. The source manifest SHA and output shard checksums are recorded. The output is published atomically only after all files, checks, and manifest have succeeded. `PROJECTION_COMPLETE=PASS` is required for a valid projection. It does not run new saddle searches or modify canonical identity.

The `starting_minimum_id` search-origin field is deliberately **not** filled from this derived association; such an edit would counterfeit provenance. Original rows are preserved in the source export.
