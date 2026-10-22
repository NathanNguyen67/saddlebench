# SaddleBench scientific reference candidate: schema and provenance

## Status

This is an **additive, unqualified data export** of a pinned published SaddleMill registry snapshot. It does not certify reactions, change SaddleMill's maxdev or DoubleMin policies, or replace the existing private SaddleBench scoring reference. No old snapshot should be deleted or overwritten.

Original RC14 six files and their exact JSONL formats remain intact:

- `evidence_unqualified/saddles.jsonl`, `doublemin_observations.jsonl`, `reactions.jsonl`, `minima.jsonl`, `geometries.jsonl`, `source_artifacts.jsonl`
- `evidence_unqualified/manifest.json` with source registry SHA-256, publication watermark, row counts, source artifact uncertainty, immutable input checks and file hashes.

New `scientific_dataset_unqualified/` files:

| File | Grain | Meaning |
|---|---|---|
| `saddles_enriched.jsonl` | One row per canonical saddle | Stable saddle ID, geometry ID, all DoubleMin observation IDs, all recorded mode-source artifact links, chemistry IDs. No arbitrary selection. |
| `reaction_triplets.jsonl` | One row per DoubleMin observation | `minimum_A` = recorded minus endpoint, `saddle` = canonical saddle, `minimum_B` = recorded plus endpoint; geometry ID/hash/atom count by role; protocol, canonical reaction ID and status; missingness and source provenance. |
| `hessian_modes.jsonl` | One row per hashed saddle/Hessian source artifact | Recorded `lowest_eigenmode` availability, exact source hash, linkage to saddle/observation, numerical metadata, source-geometry association status and pointer into compressed mode arrays. |
| `mode_arrays/modes_XXXXXX.npz` | Up to 128 mode arrays/shard | Native stored NumPy arrays keyed by `m0000`, etc.; shape/dtype unchanged. `np.load(...,allow_pickle=False)`; array bytes SHA-256 in corresponding JSONL record. |
| `manifest_science.json`, `SHA256SUMS.txt` | One row/file per snapshot | Link to the *exact original* source registry/manifest, row counts, statuses and checksums. |

**Critical scientific semantics**

1. **Triplet is an observed endpoint pairing**, not a uniquely identified chemical forward reaction. Naming `minimum_A` and `minimum_B` deliberately avoids inventing reactant/product direction. One saddle may link to multiple distinct observations and reaction IDs.
2. The source Hessian mode is recorded verbatim if present in a hashed `.npz` or `.traj` source. We do not rotate, reshape, sign-flip, normalize or recompute it. We do **not** claim a mode is aligned with the exported canonical saddle unless the source geometry SHA and atom shape evidence show that. `mode.exact_geometry_sha_match=null` means the source offers no direct SHA attestation. `mode.full_atom_shape=false` means this cannot be used as a full per-atom ASE vector without a validated free-DOF mapping.
3. Geometry IDs refer to the unchanged `geometries.jsonl`. The core exporter is still conservative about missing FixAtoms constraints, source artifact validity, Hessian/PES identity, and source publication completeness.
4. Do **not** infer Hessian order or saddle certification from the exported eigenvector alone. `mode_scientific_certification=NOT_ESTABLISHED` is intentional.
5. No new reaction eligibility, barrier, force threshold, matching metric, topology test, or probability/confidence policy is introduced.

## Independent ASE consumer

```python
from tools.registry_export.ase_loader import GeometryIndex, to_ase_atoms, load_mode
import json
from pathlib import Path
base = Path('/path/to/evidence_unqualified')
science = Path('/path/to/scientific_dataset_unqualified')
with (science / 'reaction_triplets.jsonl').open() as f:
    t = json.loads(next(f))
with GeometryIndex(base / 'geometries.jsonl') as geom:
    saddle = to_ase_atoms(geom.get(t['geometry']['saddle']['geometry_id']))
    # minimum_A/B may be missing; check before loading.
with (science / 'hessian_modes.jsonl').open() as f:
    m = json.loads(next(f))
if m['mode_array']:
    mode = load_mode(science, m)
    # Must inspect m['mode'] for shape, registration and qualification.
```

## Candidate publication policy

`upload_hf_candidate.py` is dry-run by default. With explicit `--upload-candidate` it uploads the immutable raw/science layers to *new paths* under `candidates/registry_<sha16>/` in an **already existing private** Hugging Face dataset repo (`traingletrain/saddlebench-reference-private`). It never updates the active scoring reference or deletes old snapshots. Uploaded candidates can be audited independently and promoted later under a separate, approved release procedure. No automatic promotion script is included because qualification and source-to-reference mapping may change the official denominators.

Before any promotion: verify source registry watermark and counts against the 120k saddle update; constraint repair status, source accessibility, mode/saddle exact geometry and PES correspondence, rate-relevant endpoint qualification, and scorer regression against the archived previous reference. Preserve both old and new reference release identifiers.
