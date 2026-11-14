import unittest
from agent_baseline.analysis.night_missed_rank import ordering

class NightRankTest(unittest.TestCase):
    def test_rankings_are_explicit_stable_and_label_free(self):
        rows=[dict(area=10,dino=.2,evidence_score=.04),dict(area=20,dino=.1,evidence_score=.02),dict(area=10,dino=.2,evidence_score=.04)]
        self.assertEqual(ordering(rows,'balanced'),[0,1,2])
        self.assertEqual(ordering(rows,'area'),[1,0,2])
        self.assertEqual(ordering(rows,'evidence'),[0,2,1])
        rows[0]['dino']=None
        self.assertIsNone(ordering(rows,'evidence'))

if __name__=='__main__':unittest.main()
