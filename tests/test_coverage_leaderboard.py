"""CPU-only tests for presentation without a scorer or reference download."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from saddlebench import coverage_leaderboard as mod

def case(caseid, structure, model, deviations, k=4, seconds=40):
    matched = [{'reaction_id': 'reaction' + str(i),
                'greedy_first_hit_maxdev_A': float(x),
                'best_observed_greedy_hit_maxdev_A': float(x) / 2}
               for i, x in enumerate(deviations)]
    thresholds = {}
    for t in mod.TIERS:
        thresholds[t] = {'known_eligible_reactions_K': k,
                         'greedy_unique_reactions_U': len(matched),
                         'matched_reactions': copy.deepcopy(matched),
                         'matched_predictions': len(deviations),
                         'matched_prediction_maxdev_A': [float(d) for d in deviations]}
    return {'case_id':caseid,'structure_id':structure,'model':model,
            'thresholds':thresholds,'self_reported_elapsed_seconds':seconds}


def report(rows):
    return {'schema_version':mod.SCHEMA,'case_results':rows,
            'available_cases':123,'model_aggregates':[]}


class PresentationTests(unittest.TestCase):
    def test_multiple_datasets_get_independent_boards_and_no_cross_ranking(self):
        rows = [case('c1','lemat:01','alpha',[.01,.02],5),
                case('c1','lemat:01','beta',[.03],5),
                case('c2','oc20:01','alpha',[.02],2),
                case('c2','oc20:01','beta',[.02],2)]
        boards = mod.build_boards(report(rows))
        self.assertEqual([x['dataset'] for x in boards], ['LEMAT','OC20'])
        self.assertEqual(boards[0]['models'][0]['tiers']['0.20']['k'],5)
        self.assertEqual(boards[1]['models'][0]['tiers']['0.20']['k'],2)
        rendered=mod.terminal_view(report(rows),use_color=False)
        self.assertIn('  LEMAT\n',rendered)
        self.assertIn('  OC20\n',rendered)
        self.assertNotIn('2/123',rendered)
        self.assertNotIn('TOTAL',rendered)
        self.assertEqual(mod.html_view(report(rows)).count('class="board"'),2)

    def test_different_case_sets_refuse_ranking(self):
        rows=[case('c1','lemat:01','b-model',[.005]),
              case('c2','lemat:02','a-model',[.02])]
        board=mod.build_boards(report(rows))[0]
        self.assertFalse(board['equal_sets'])
        self.assertEqual([m['model'] for m in board['models']],['a-model','b-model'])
        self.assertIn('not ranked',mod.terminal_view(report(rows),use_color=False))
        self.assertIn('ranking disabled',mod.html_view(report(rows)))

    def test_pooled_reaction_median_preserves_first_and_best_semantics(self):
        rows=[case('c1','lemat:01','x',[.01,.03,.04], k=5),
              case('c2','lemat:02','x',[.10], k=5)]
        b=mod.build_boards(report(rows))[0]['models'][0]['tiers']['0.20']
        self.assertEqual((b['u'], b['k']), (4,10))
        self.assertAlmostEqual(b['median'], .035)
        self.assertAlmostEqual(b['best'], .0175)
        self.assertEqual(mod.grade(.90),'good')
        self.assertEqual(mod.grade(.70),'mid')
        self.assertEqual(mod.grade(.6999),'low')

    def test_html_escaping_and_standalone_assets(self):
        text=mod.html_view(report([case('c1','lemat:01','<img src=x onerror=alert(1)>',[.01])]))
        self.assertNotIn('<img',text)
        self.assertIn('&lt;img',text)
        self.assertNotIn('<script',text)
        self.assertNotIn('http:',text)
        self.assertNotIn('https:',text)
        self.assertIn('@media(max-width',text)

    def test_json_read_only_existing_html_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'results.json'
            data=report([case('c1','lemat:01','model',[.01])])
            raw=json.dumps(data,indent=2)
            path.write_text(raw)
            self.assertEqual(mod.main([str(path), '--no-color']),0)
            htmlpath=path.with_suffix('.html')
            self.assertTrue(htmlpath.exists())
            self.assertEqual(path.read_text(),raw)
            with self.assertRaisesRegex(SystemExit,'HTML_OUTPUT_ALREADY_EXISTS'):
                mod.main([str(path), '--no-color'])
            self.assertEqual(mod.main([str(path), '--no-html', '--no-color']),0)
            self.assertEqual(path.read_text(),raw)

    def test_zero_known_reactions_uses_unranked_dash(self):
        txt=mod.terminal_view(report([case('c0','lemat:01','m',[],k=0)]),use_color=False)
        self.assertIn('0/0', txt)
        self.assertIn('       -', txt)

    def test_wrong_schema_rejected(self):
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_REPORT_SCHEMA'):
            mod.build_boards({'schema_version':'other','case_results':[]})

if __name__=='__main__':
    unittest.main()
