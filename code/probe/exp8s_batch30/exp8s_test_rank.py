"""r1 单元测试；仅测试排序契约，不用模拟结果替代实验验收。"""
from __future__ import annotations

import argparse
import sys
import unittest

sys.dont_write_bytecode = True
from exp8s_rank import r1_key, rank_r1


class RankingTests(unittest.TestCase):
    def test_agreement_precedes_area(self):
        small = dict(sources=['B'], area=20)
        agreed = dict(sources=['B', 'G'], area=800)
        self.assertEqual(rank_r1([small, agreed]), [agreed, small])

    def test_area_breaks_agreement_ties(self):
        large = dict(sources=['B', 'C'], area=100)
        small = dict(sources=['H1', 'G'], area=21)
        self.assertEqual(rank_r1([large, small]), [small, large])

    def test_distinct_sources_and_c_subsources(self):
        self.assertEqual(r1_key(dict(sources=['C1', 'C2', 'C', 'G0.05', 'G', 'B', 'B'], area=20)), (-3, 20))

    def test_stable_ties_without_truth_or_probability(self):
        a = dict(sources=['B'], area=20, a=0, detector_mean=.9)
        b = dict(sources=['G'], area=20, a=20, detector_mean=.1)
        self.assertIs(rank_r1([a, b])[0], a)
        self.assertIs(rank_r1([b, a])[0], b)

    def test_k_and_input_not_mutated(self):
        first = dict(sources=['B'], area=100)
        second = dict(sources=['C', 'G'], area=20)
        items = [first, second]
        self.assertEqual(rank_r1(items, 1), [second])
        self.assertEqual(items, [first, second])
        self.assertEqual(rank_r1(items, 0), [])
        self.assertEqual(rank_r1(items, 10), [second, first])
        self.assertEqual(rank_r1([], None), [])
        with self.assertRaises(ValueError):
            rank_r1(items, -1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', help='接受 submit30 的统一参数；测试只输出到作业日志')
    parser.parse_args()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
