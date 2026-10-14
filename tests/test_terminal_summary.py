"""CPU-only regression checks for SaddleBench table and model-labeled CSV."""
import csv
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

from saddlebench.terminal_summary import render_summary
from saddlebench import score_modes


def sample_case(model='SEL04 M24', case='case0006', score=120.5):
    return {
        'model': model, 'case_id': case, 'structure_id': 'oc20:test',
        'starting_minimum_id': 'm0', 'known_eligible_reactions_K': 4,
        'greedy_unique_reactions_U': 2, 'coverage': .5,
        'self_reported_elapsed_seconds': 532., 'provisional_score_per_hour': score,
        'first_hit_potential_bad_groups_with_known_start': 1,
        'entries': [
            {'stage': 'eligible', 'status': 'CREDIT'},
            {'stage': 'eligible', 'status': 'DUPLICATE'},
            {'stage': 'known_nonfo', 'category': 'KNOWN_NON_FIRST_ORDER', 'status': 'NONFO_FIRST_HIT'},
            {'stage': 'remaining_diagnostics', 'category': 'KNOWN_FIRST_ORDER_DISCONNECTED', 'status': 'CATALOG_FIRST_HIT'},
            {'stage': None, 'status': 'UNKNOWN_UNMATCHED'},
        ]
    }


class LeaderboardTests(unittest.TestCase):
    def test_detailed_table_and_color_toggle(self):
        with tempfile.TemporaryDirectory() as td:
            cases = Path(td)/'cases.csv'
            cases.write_text('case_id,structure_id,starting_minimum_id,exploration_confidence_heuristic\n'
                             'case0006,oc20:test,m0,0.93\n')
            a = sample_case()
            b = sample_case('ForceBank-Fourier', score=180.9)
            report = {'mode':'score_detailed', 'case_results':[a,b]}
            text = render_summary(report, cases_csv=cases, color=False)
            self.assertIn('SEL04 M24', text)
            self.assertIn('ForceBank-Fourier', text)
            self.assertIn('  93.0 ', text)
            self.assertIn(' 180.90', text)
            self.assertIn('S/H', text)
            self.assertIn('NF', text)
            self.assertIn('B', text)
            self.assertIn('UNM', text)
            self.assertNotIn('\x1b[', text)
            painted = render_summary(report, cases_csv=cases, color=True)
            self.assertIn('\x1b[', painted)
            self.assertTrue(re.sub(r'\x1b\[[0-9;]*m', '', painted).startswith(text))
            # No model-name escaping or width drift in ANSI-free output.
            lines = [l for l in text.splitlines() if 'ForceBank-Fourier' in l or 'SEL04 M24' in l]
            self.assertEqual(len(lines), 2)
            self.assertLess(text.index('ForceBank-Fourier'), text.index('SEL04 M24'))

    def test_coverage_and_score_dont_mislabel_unchecked_saddles(self):
        for mode in ('coverage', 'score'):
            report={'mode':mode,'case_results':[sample_case()]}
            text=render_summary(report,color=False)
            self.assertIn('UNM',text)
            self.assertIn('NF',text)
            self.assertIn('not evaluated' if mode=='coverage' else 'does not check HB/OTH',text)

    def test_no_reference_confidence_on_mismatch_or_missing(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'cases.csv'
            p.write_text('case_id,structure_id,starting_minimum_id,exploration_confidence_heuristic\n'
                         'case0006,wrong,m0,0.99\n')
            result=render_summary(sample_case(),cases_csv=p,color=False)
            self.assertIn('   -',result)
            self.assertNotIn('99.0',result)
            self.assertIn('CONF%', render_summary(sample_case(), cases_csv=p.with_name('missing.csv')))

    def test_per_model_totals_only_same_case_sets(self):
        a=sample_case()
        b=sample_case('another', score=88.0)
        arms=[{'model':'SEL04 M24','case_ids':['case0006'], 'aggregate':score_modes._batch_report([a], 'score_detailed',123)['aggregate']},
              {'model':'another','case_ids':['case0006'], 'aggregate':score_modes._batch_report([b], 'score_detailed',123)['aggregate']}]
        report={'mode':'score_detailed','case_results':[a,b], 'model_aggregates': arms}
        text=render_summary(report,color=False)
        self.assertIn('MODEL LEADERBOARD',text)
        self.assertIn('PER-MODEL TOTALS', render_summary(report,color=False,view='case'))
        report['model_aggregates'][1]['case_ids']=['case0022']
        self.assertIn('UNRANKED', render_summary(report,color=False))
        self.assertIn('No aggregate ranking', render_summary(report,color=False,view='case'))

    def test_two_models_same_case_and_legacy_batch(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            cases=td/'cases.csv'
            cases.write_text('case_id\ncase0006\ncase0022\n')
            for name in ('a.traj','b.traj','c.traj'):
                (td/name).write_text('fake')
            model_csv=td/'models.csv'
            model_csv.write_text('model,case_id,trajectory,elapsed_seconds\n'
                                 'SEL04 M24,case0006,a.traj,532\n'
                                 'ForceBank,case0006,b.traj,575\n'
                                 'SEL04 M24,case0022,c.traj,1114\n')
            kwargs=dict(casefile=cases, public_root=td, private=td, case_id=None,
                        submission=None,elapsed_seconds=None,submission_csv=model_csv,
                        mode='coverage',microbatch_size=16,nonfo_cache=None,index_cache=td/'cache')
            tasks,total=score_modes._collect_tasks(**kwargs)
            self.assertEqual([x['model'] for x in tasks], ['SEL04 M24','ForceBank','SEL04 M24'])
            self.assertEqual(total,2)
            original=score_modes._case_worker
            def fake_worker(task):
                c=sample_case(model=task.get('model'),case=task['case']['case_id'])
                c.pop('model',None)
                c['self_reported_elapsed_seconds']=task['elapsed_seconds']
                return c
            score_modes._case_worker=fake_worker
            try:
                result=score_modes.run(casefile=cases,private=td,submission_csv=model_csv,
                                       mode='coverage',index_cache=td/'cache')
                self.assertEqual(result['schema_version'],'saddlebench-quick-model-comparison-v1')
                self.assertEqual(len(result['case_results']),3)
                self.assertEqual(result['selected_cases'],2)
                self.assertEqual(len(result['model_aggregates']),2)
                self.assertEqual(result['model_aggregates'][0]['selected_cases'],2)
                legacy=td/'legacy.csv'
                legacy.write_text('case_id,trajectory,elapsed_seconds\ncase0006,a.traj,532\ncase0022,c.traj,1114\n')
                old=score_modes.run(casefile=cases,private=td,submission_csv=legacy,
                                    mode='coverage',index_cache=td/'cache')
                self.assertEqual(old['schema_version'],'saddlebench-quick-batch-v1')
                self.assertNotIn('model_aggregates',old)
                self.assertNotIn('model',old['case_results'][0])
                model_csv.write_text('model,case_id,trajectory,elapsed_seconds\n'
                                     'SEL04 M24,case0006,a.traj,532\n'
                                     'SEL04 M24,case0006,b.traj,575\n')
                with self.assertRaisesRegex(ValueError,'DUPLICATE_MODEL_CASE'):
                    score_modes._collect_tasks(**kwargs)
                model_csv.write_text('model,case_id,trajectory,elapsed_seconds\n'
                                     ',case0006,a.traj,532\n')
                with self.assertRaisesRegex(ValueError,'EMPTY_MODEL_LABEL'):
                    score_modes._collect_tasks(**kwargs)
            finally:
                score_modes._case_worker=original


if __name__ == '__main__':
    unittest.main()
