"""CPU regression: published GroupCatalog must accept the three SaddleBench tiers.

Uses the actual canonical maxdev matcher, not a mock, and checks both
single-row and microbatch matching plus the scorer's true integration call.
"""
import math
import unittest
from types import SimpleNamespace

from saddlebench.coverage_tiers import _find_greedy, _tally_predictions
from saddlebench.matching.geometry import core_drift_maxdev
from saddlebench.matching.group_radius_exists import GroupCatalog, build_index


def geom(offset):
    return {
        'numbers': [6, 6, 6, 6, 6],
        'positions_angstrom': [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, float(offset)]],
        'cell_angstrom': [[10, 0, 0], [0, 10, 0], [0, 0, 10]],
        'pbc': [False, False, False],
        'fixed_indices': [],
        'constraints_known': True,
    }


class ThresholdMatcherRegression(unittest.TestCase):
    def setUp(self):
        # Canonical grouping is used only as an index; actual geometry controls matches.
        self.members = [
            (None, {'reaction_id': 'A', 'saddle_group_id': 'group-A', 'geometry': geom(0)}),
            (None, {'reaction_id': 'A', 'saddle_group_id': 'group-A', 'geometry': geom(.02)}),
        ]
        self.stage = SimpleNamespace(members=self.members,
                                     catalog=GroupCatalog(self.members, build_index(self.members)))

    def found(self, delta, cutoff, batch_size=16):
        return bool(self.stage.catalog.find_first(geom(delta), threshold=cutoff,
                                                    batch_size=batch_size).get('found'))

    def test_real_geometry_and_tiers(self):
        self.assertAlmostEqual(core_drift_maxdev(geom(0), geom(.17)), .17)
        self.assertAlmostEqual(core_drift_maxdev(geom(.02), geom(.17)), .15)
        for batch_size in (1, 16):
            with self.subTest(batch_size=batch_size):
                self.assertTrue(self.found(.17, .20, batch_size))
                self.assertFalse(self.found(.17, .10, batch_size))
                self.assertFalse(self.found(.17, .05, batch_size))
                self.assertTrue(self.found(.08, .10, batch_size))
                self.assertFalse(self.found(.08, .05, batch_size))
                self.assertTrue(self.found(.04, .05, batch_size))
                self.assertFalse(self.found(.23001, .20, batch_size))

    def test_default_and_explicit_point_two_identical(self):
        for d in (0, .04, .08, .17, .20, .23001):
            with self.subTest(displacement=d):
                default = self.stage.catalog.find_first(geom(d), batch_size=16)
                explicit = self.stage.catalog.find_first(geom(d), batch_size=16, threshold=.20)
                self.assertEqual(default, explicit)

    def test_invalid_tolerance_rejected(self):
        for t in (0, -.1, .20001, .21, float('nan'), float('inf'), '0.1', True):
            with self.subTest(t=t):
                with self.assertRaisesRegex(ValueError, 'GREEDY_THRESHOLD_OUT_OF_RANGE'):
                    self.stage.catalog.find_first(geom(.17), threshold=t)

    def test_public_coverage_scorer_uses_actual_parameter(self):
        predicted = geom(.17)
        self.assertEqual(_find_greedy(self.stage, predicted, .2, 16)[0][0], 'A')
        self.assertIsNone(_find_greedy(self.stage, predicted, .1, 16)[0])
        n, tiers, _ = _tally_predictions([
            {'geometry': geom(.17)},
            {'geometry': geom(.04)},
        ], {tuple(predicted['numbers']): self.stage}, 1, 16)
        self.assertEqual(n, 2)
        self.assertEqual([tiers[t]['greedy_unique_reactions_U'] for t in ('0.20','0.10','0.05')], [1, 1, 1])
        self.assertEqual([tiers[t]['matched_predictions'] for t in ('0.20','0.10','0.05')], [2, 1, 1])
        self.assertGreater(tiers['0.20']['median_matched_predictions_maxdev_A'],
                           tiers['0.10']['median_matched_predictions_maxdev_A'])


if __name__ == '__main__':
    unittest.main()
