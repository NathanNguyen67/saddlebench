"""Compatibility resolver never substitutes unchecked publication identities."""
import csv, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from saddlebench.reference.hf_compat import _compatible_cases, resolve_cases
REGISTRY_SHA='85d37e55448b544473b34e7bb244823e80ddc85091bbe6ae3e771c7beb4b0a41'

class ReferencePairTests(unittest.TestCase):
    def test_identity_selection(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'cases.csv'
            p.write_text('case_id,registry_sha256\n' +
                         ''.join(f'case{i:04d},{REGISTRY_SHA}\n' for i in range(1,124)))
            self.assertTrue(_compatible_cases(p,REGISTRY_SHA))
            p.write_text('case_id,registry_sha256\ncase0001,bad\n')
            self.assertFalse(_compatible_cases(p,REGISTRY_SHA))
    def test_wrong_manifest_cannot_fallback(self):
        def fail(ref,cases):raise RuntimeError('REFERENCE_CHECKSUM_FAILED:foo')
        with self.assertRaisesRegex(RuntimeError,'REFERENCE_CHECKSUM_FAILED'):
            resolve_cases('reference','bad.csv','cache',fail)
    def test_existing_verified_pair_is_retained(self):
        calls=[]
        def verify(ref,cases):calls.append((ref,cases))
        self.assertEqual(resolve_cases('ref','cases.csv','cache',verify),Path('cases.csv'))
        self.assertEqual(calls,[('ref','cases.csv')])
