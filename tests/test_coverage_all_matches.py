"""CPU-only exact denominator/weighting tests for the opt-in all-match metric."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest

from saddlebench import coverage_tiers as sc
from saddlebench import coverage_leaderboard as rd

class AllMatchTests(unittest.TestCase):
    def test_all_predictions_vs_best_per_reaction(self):
        distances={'p1':('A',.005),'p2':('A',.015),'p3':('A',.025),'p4':('B',.045),'p5':None}
        def fake(stage,geo,t,bs):
            item=distances[geo['name']]
            return item if item is not None and item[1]<=t else None, {'exact':1}
        preds=[{'geometry':{'numbers':[1],'name':k}} for k in distances]
        with patch.object(sc,'_find_greedy',fake):
            n,tiers,_=sc._tally_predictions(preds,{(1,):object()},3,16)
        self.assertEqual(n,5)
        for tier in sc.THRESHOLDS:
            item=tiers[f'{tier:.2f}']
            self.assertEqual(item['matched_predictions'],4)
            self.assertEqual(item['greedy_unique_reactions_U'],2)
            self.assertEqual(item['matched_prediction_maxdev_A'],[.005,.015,.025,.045])
            self.assertAlmostEqual(item['median_matched_predictions_maxdev_A'],.020)
            self.assertAlmostEqual(item['best_observed_greedy_hit_median_maxdev_A'],.025)
            self.assertAlmostEqual(item['greedy_first_hit_median_maxdev_A'],.025)
        self.assertEqual(tiers['0.05']['greedy_unique_reactions_U'],2)

    def test_pool_all_matched_distances_not_per_case_medians(self):
        def case(cid,idx,ds):
            best=[{'reaction_id':f'R{idx}_{i}','best_observed_greedy_hit_maxdev_A':v,
                   'greedy_first_hit_maxdev_A':v} for i,v in enumerate(ds)]
            tier={'matched_reactions':best,'matched_predictions':len(ds),
                  'matched_prediction_maxdev_A':ds, 'known_eligible_reactions_K':5,
                  'greedy_unique_reactions_U':len(ds),'micro_coverage':len(ds)/5}
            return {'case_id':cid,'structure_id':f'lemat:{idx}','model':'M',
                    'self_reported_elapsed_seconds':10,'prediction_count':len(ds),
                    'thresholds':{f'{t:.2f}':dict(tier) for t in sc.THRESHOLDS}}
        rows=[case('c1',1,[.001,.003,.005]),case('c2',2,[.015])]
        a=sc._create_report(rows,123)
        self.assertAlmostEqual(a['model_aggregates'][0]['aggregate']['thresholds']['0.20']['median_matched_predictions_maxdev_A'],.004)
        b=rd.build_boards(a)[0]['models'][0]
        self.assertAlmostEqual(b['tiers']['0.20']['median'],.004)
        self.assertIn('BEST MED',rd.terminal_view(a,use_color=False))
        self.assertIn('Median',rd.html_view(a))

    def test_old_json_does_not_mislabel_old_median(self):
        rows=[{'case_id':'c1','structure_id':'lemat:1','model':'M',
               'self_reported_elapsed_seconds':10,'thresholds':{
                    x:{'greedy_unique_reactions_U':1,'known_eligible_reactions_K':2,
                       'matched_predictions':3,
                       'matched_reactions':[{'reaction_id':'A',
                           'greedy_first_hit_maxdev_A':.002,
                           'best_observed_greedy_hit_maxdev_A':.001}]}
                    for x in rd.TIERS}}]
        a={'schema_version':rd.SCHEMA,'case_results':rows}
        b=rd.build_boards(a)[0]['models'][0]['tiers']['0.20']
        self.assertIsNone(b['median'])
        self.assertAlmostEqual(b['best'],.001)
        self.assertNotIn('median first credited',rd.html_view(a))

    def test_mismatched_distance_count_fails_closed(self):
        data={'schema_version':rd.SCHEMA,'case_results':[{'case_id':'c1','model':'M',
            'structure_id':'lemat:1','self_reported_elapsed_seconds':2,
            'thresholds':{x:{'matched_reactions':[],
             'greedy_unique_reactions_U':0,'known_eligible_reactions_K':2,
             'matched_predictions':2,'matched_prediction_maxdev_A':[.001]}
             for x in rd.TIERS}}]}
        with self.assertRaisesRegex(ValueError,'DISTANCES_COUNT_MISMATCH'):
            rd.build_boards(data)

if __name__=='__main__':unittest.main()
