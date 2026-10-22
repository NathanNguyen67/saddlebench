"""Default scorer integration and presentation without network/GPU."""
import contextlib, io, json, tempfile, types, unittest, sys
from pathlib import Path
from unittest.mock import patch
from saddlebench import coverage_tiers as ct

class DefaultCoverageIntegration(unittest.TestCase):
    def test_default_csv_writes_single_board_and_assets(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'submission.csv').write_text('model,case_id,trajectory,elapsed_seconds\nSella,case0006,fake.traj,532\n')
            (p/'cases.csv').write_text('case_id\ncase0006\n')
            (p/'reference').mkdir()
            empty=ct._tally_predictions([],{},6,16)[1]
            sample={'case_id':'case0006','structure_id':'lemat:demo','model':'Sella',
                    'prediction_count':0,'self_reported_elapsed_seconds':532.0,
                    'thresholds':empty}
            fake=types.ModuleType('saddlebench.score_modes')
            fake.verify_reference=lambda a,b:True
            fake._collect_tasks=lambda **kwargs:([{'model':'Sella'}],123)
            capture=io.StringIO()
            with patch.dict(sys.modules,{'saddlebench.score_modes':fake}), \
                 patch.object(ct,'_case_worker',return_value=sample), \
                 contextlib.redirect_stdout(capture):
                result=ct.main(['--submission-csv',str(p/'submission.csv'),
                                '--reference-root',str(p/'reference'),
                                '--cases-csv',str(p/'cases.csv'),
                                '--output',str(p/'results.json'),'--color'])
            self.assertEqual(result,0)
            text=capture.getvalue()
            self.assertEqual(text.count('Reference coverage & geometry'),1)
            self.assertIn('LEMAT',text)
            self.assertIn('COVERAGE_TIERS=PASS',text)
            self.assertTrue((p/'results.json').is_file())
            self.assertTrue((p/'results.html').is_file())
            self.assertTrue((p/'results_LEMAT.svg').is_file())
            self.assertEqual(json.loads((p/'results.json').read_text())['schema_version'],ct.SCHEMA)
            with patch.dict(sys.modules,{'saddlebench.score_modes':fake}), \
                 patch.object(ct,'_case_worker',return_value=sample):
                with self.assertRaisesRegex(SystemExit,'OUTPUT_ALREADY_EXISTS'):
                    ct.main(['--submission-csv',str(p/'submission.csv'),
                        '--reference-root',str(p/'reference'),
                        '--cases-csv',str(p/'cases.csv'), '--output',str(p/'results.json')])
    def test_single_case_supported(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'cases.csv').write_text('case_id\ncase0006\n')
            (p/'reference').mkdir()
            empty=ct._tally_predictions([],{},6,16)[1]
            sample={'case_id':'case0006','structure_id':'lemat:demo','model':'default',
                    'prediction_count':0,'self_reported_elapsed_seconds':532.0,'thresholds':empty}
            fake=types.ModuleType('saddlebench.score_modes')
            fake.verify_reference=lambda a,b:True
            def get_tasks(**kw):
                self.assertEqual(kw['case_id'],'case0006')
                self.assertEqual(kw['submission'],'pred.traj')
                self.assertEqual(kw['elapsed_seconds'],532)
                return ([{}],123)
            fake._collect_tasks=get_tasks
            with patch.dict(sys.modules,{'saddlebench.score_modes':fake}), \
                 patch.object(ct,'_case_worker',return_value=sample), \
                 contextlib.redirect_stdout(io.StringIO()):
                result=ct.main(['--case-id','case0006','--submission','pred.traj',
                        '--elapsed-seconds','532','--reference-root',str(p/'reference'),
                        '--cases-csv',str(p/'cases.csv'),'--output',str(p/'out.json'),
                        '--no-html','--no-image'])
            self.assertEqual(result,0)
            self.assertTrue((p/'out.json').is_file())
