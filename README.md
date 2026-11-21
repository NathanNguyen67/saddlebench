# SaddleBench UMA explorer payloads

Data-only branch. One SaddleMotif Explorer v2 payload (schema saddlemotif-explorer-v2) per literature structure,
plus Al and Pt, built by SaddleMotif explorer_v2/build/build_structure.py from the campaign database the UMA export
was made from (lite.epoch56_final_local_20261119_1256.db, Nov 19 2026). The scorer fetches these by commit hash
through saddlebench/reference/uma_explorer.json and verifies each SHA-256.
