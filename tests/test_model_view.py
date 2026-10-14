"""No pytest, network, reference dataset, or geometry calculation required."""
import tempfile
import unittest
from pathlib import Path
from saddlebench.terminal_summary import render_summary


def item(model, case, score):
    return {'model':model, 'case_id':case, 'structure_id':'oc20:example',
            'starting_minimum_id':'m', 'known_eligible_reactions_K':4,
            'greedy_unique_reactions_U':2, 'coverage':.5,
            'self_reported_elapsed_seconds':100.,'provisional_score_per_hour':score,
            'first_hit_potential_bad_groups_with_known_start':1,
            'entries':[{'stage':'eligible','status':'CREDIT'},
                       {'stage':'eligible','status':'DUPLICATE'},
                       {'stage':'known_nonfo','category':'KNOWN_NON_FIRST_ORDER',
                        'status':'NONFO_FIRST_HIT'}]}


def arm(label, cases, score):
    return {'model':label, 'case_ids':cases,
            'aggregate': {'greedy_unique_reactions_U':2*len(cases),
                          'reference_known_K':4*len(cases),
                          'micro_coverage':.5,
                          'first_hit_potential_bad_groups_with_known_start':len(cases),
                          'total_self_reported_seconds':100*len(cases),
                          'provisional_score_per_hour':score}}


class ModelViewTests(unittest.TestCase):
    def test_model_default_and_explicit_case_view_and_confidence(self):
        cases=['case0006','case0022']
        report={'mode':'score_detailed',
                'case_results':[item(m,c,score) for m,score in [('A',60),('B',85)] for c in cases],
                'model_aggregates':[arm('A',cases,60),arm('B',cases,85)]}
        with tempfile.TemporaryDirectory() as tmp:
            csv=Path(tmp)/'cases.csv'
            csv.write_text('case_id,structure_id,starting_minimum_id,exploration_confidence_heuristic\n'
                           'case0006,oc20:example,m,0.90\ncase0022,oc20:example,m,0.80\n')
            displayed=render_summary(report,cases_csv=csv,color=False)
        self.assertIn('MODEL LEADERBOARD',displayed)
        self.assertIn('U/K',displayed)
        self.assertIn('REF%',displayed)
        self.assertIn('85.0',displayed)
        table=[line for line in displayed.splitlines() if ' 4/8 ' in line]
        self.assertEqual(len(table),2)
        self.assertIn('B',table[0]);self.assertIn('A',table[1])
        caseview=render_summary(report,color=False,view='case')
        self.assertIn('PER-MODEL TOTALS',caseview)
        self.assertNotIn('MODEL LEADERBOARD',caseview)
        self.assertEqual(report['model_aggregates'][0]['aggregate']['provisional_score_per_hour'],60)

    def test_differing_case_sets_unranked_and_one_model(self):
        report={'mode':'coverage', 'case_results':[item('A','case0006',50),item('B','case0022',80)],
                'model_aggregates':[arm('A',['case0006'],50),arm('B',['case0022'],80)]}
        out=render_summary(report,color=False)
        self.assertIn('UNRANKED',out)
        self.assertIn('1/2',out)
        report['model_aggregates']=report['model_aggregates'][:1]
        self.assertIn('MODEL LEADERBOARD',render_summary(report,color=False))

    def test_legacy_fallback_and_bad_view(self):
        legacy={'mode':'coverage','case_results':[item('default','case0006',40)]}
        out=render_summary(legacy,color=False)
        self.assertIn('SADDLEBENCH | coverage',out)
        self.assertNotIn('MODEL LEADERBOARD',out)
        with self.assertRaisesRegex(ValueError,'INVALID_TERMINAL_VIEW'):
            render_summary(legacy,view='banana')

if __name__=='__main__':unittest.main()
