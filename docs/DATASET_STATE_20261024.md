# Dataset state - 2026-10-24

The published public Hugging Face scoring datasets are **unchanged** by this release candidate. Published references currently describe 123 starting cases, 2,387 eligible reaction identities, and 83,529 diagnostic saddle records. The independently produced unqualified LS6 reaction SQLite archive has a `completed=YES` metadata marker with expected counts of 217,716 saddles, 202,884 DoubleMin triplets, 71,647 canonical reactions, and 125 minima. The metadata probe did **not** independently verify row counts or run a new integrity check. These unqualified records must not be added to the public score denominators without eligibility and provenance audits. No Hugging Face upload is performed by the source package.

Archive: `/work/11588/ndn454/ls6/saddlebench_reaction_archives/reaction_archive_28098e06323645bc1dca2210.sqlite`
Registry identity: `28098e06323645bc1dca221058b35dce1c281c9441579783bf62e82f2ec216a1`
