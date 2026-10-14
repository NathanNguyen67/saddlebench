#!/usr/bin/env python3
"""SaddleBench published HF scorer. Default --mode score; see README.

Legacy helpers verify_reference() and digest() remain importable for callers
which used the original scripts/score_from_hf.py API.
"""
from saddlebench.score_modes import main, verify_reference, _file_digest as digest

PUBLIC = 'traingletrain/saddlebench-prototype'
REFERENCE = 'traingletrain/saddlebench-reference-private'
REGISTRY_SHA = '85d37e55448b544473b34e7bb244823e80ddc85091bbe6ae3e771c7beb4b0a41'

if __name__=='__main__':
    raise SystemExit(main())
