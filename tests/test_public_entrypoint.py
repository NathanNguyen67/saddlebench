"""Default CLI routing, no GPU or remote reference downloads."""
import io,sys,types,unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from saddlebench.public_entrypoint import main

class PublicRouteTests(unittest.TestCase):
    def _stub(self, name, seen):
        m=types.ModuleType(name)
        def invoke(args):
            seen.append(list(args));return 17
        m.main=invoke
        return m
    def test_default_score_reaches_three_tiers(self):
        seen=[];a=['score','--submission-csv','x.csv','--output','x.json']
        with patch.dict(sys.modules,{'saddlebench.coverage_tiers':self._stub('saddlebench.coverage_tiers',seen)}):
            self.assertEqual(main(a),17)
        self.assertEqual(seen,[a[1:]])
    def test_previous_explicit_mode_remains(self):
        seen=[]
        with patch.dict(sys.modules,{'saddlebench.score_modes':self._stub('saddlebench.score_modes',seen)}):
            self.assertEqual(main(['score','--mode','score','--output','old.json']),17)
        self.assertEqual(seen,[['--mode','score','--output','old.json']])
    def test_explicit_previous_command(self):
        seen=[]
        with patch.dict(sys.modules,{'saddlebench.score_modes':self._stub('saddlebench.score_modes',seen)}):
            self.assertEqual(main(['provisional-score','--mode','score','--output','old.json']),17)
        self.assertEqual(seen,[['--mode','score','--output','old.json']])
    def test_reference_flag_preserves_local_legacy(self):
        seen=[]
        with patch.dict(sys.modules,{'saddlebench.cli':self._stub('saddlebench.cli',seen)}):
            self.assertEqual(main(['score','foo.extxyz','--reference=old']),17)
        self.assertEqual(seen,[['score','foo.extxyz','--reference=old']])
    def test_explicit_local_legacy(self):
        seen=[]
        with patch.dict(sys.modules,{'saddlebench.cli':self._stub('saddlebench.cli',seen)}):
            self.assertEqual(main(['legacy-score','foo.extxyz','--reference','old']),17)
        self.assertEqual(seen,[['score','foo.extxyz','--reference','old']])
    def test_help(self):
        buf=io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(['--help']),0)
        self.assertIn('DEFAULT',buf.getvalue())
