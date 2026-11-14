"""Focused semantic bridge regression, executed in the smoke Slurm allocation."""
import unittest
import tempfile
from unittest.mock import patch
import numpy as np
from agent_baseline.analysis.t3_semantic_bridge import (
    canonical, bridge_scores, rank, single_date_views, PARSE_QUESTION, union, cached_dino_ranking)


class BridgeTests(unittest.TestCase):
    def test_historical_dino_identity_and_order(self):
        masks = np.array([[[1,0],[0,0]], [[0,1],[0,0]], [[0,0],[1,0]]],bool)
        indices = np.array([2,0,1])
        saved = dict(indices=indices, shape=np.array([2,2]), scores=np.array([.9,.7,.1]),
            masks=np.packbits(masks[indices].reshape(3,-1),axis=1))
        with patch('agent_baseline.analysis.t3_semantic_bridge.read_npz',return_value=saved):
            self.assertEqual(cached_dino_ranking('unused',masks),[2,0,1])
            changed = masks.copy(); changed[0,1,1] = True
            with self.assertRaisesRegex(ValueError,'masks differ'):
                cached_dino_ranking('unused',changed)

    def test_online_opt_in_and_current_unchanged(self):
        from agent_baseline.tools import Toolbox, session_schemas
        from agent_baseline import instances
        rgb = np.zeros((2,2,3), np.uint8)
        a, b = np.zeros((7,2,2)), np.zeros((7,2,2))
        a[1] = .7; b[5] = .8
        def backend(operation, **kwargs):
            return a if kwargs['time']=='t1' else b
        with tempfile.TemporaryDirectory() as folder:
            toolbox = Toolbox([rgb,rgb], folder, vision=backend)
            current = toolbox.session.add_mask(np.eye(2,dtype=bool))
            toolbox.session.current_mask = current
            props = toolbox._props(instances.from_dense(np.ones((1,2,2),bool),np.ones(1)), 't1')['props']
            args = dict(props=props,before='low vegetation',after='building')
            self.assertFalse(toolbox.execute('rank_semantic_transition',args).public['ok'])
            off = [s['function']['name'] for s in session_schemas(toolbox.session)]
            self.assertNotIn('interpret_exemplar', off)
            toolbox.t3_semantic_bridge = True
            result = toolbox.execute('rank_semantic_transition',args)
            self.assertTrue(result.public['ok'], result.public)
            self.assertAlmostEqual(result.public['scores'][0]['score'], .56)
            self.assertEqual(toolbox.session.current_mask,current)
            self.assertIn('interpret_exemplar', [s['function']['name'] for s in session_schemas(toolbox.session,t3_semantic_bridge=True)])

    def test_vocab_unknown(self):
        self.assertEqual(canonical(' BUILDING '), 'building')
        self.assertEqual(canonical('roof'), 'unknown')
        self.assertEqual(canonical(None), 'unknown')

    def test_single_date_isolation(self):
        image = np.zeros((4,4,3), np.uint8)
        mask = np.ones((4,4), bool)
        with patch('agent_baseline.exemplar_semantics.ask_images', return_value=['a','b','c','d']) as render:
            self.assertEqual(single_date_views(image, mask), [('close','a'),('wide','c')])
        dates = render.call_args.args[0]
        self.assertIs(dates[0], image)
        self.assertIs(dates[1], image)
        self.assertNotIn('ground truth', PARSE_QUESTION)

    def test_direction_and_unknown(self):
        masks = np.array([[[1,0]], [[0,1]]], bool)
        a, b = np.zeros((7,1,2)), np.zeros((7,1,2))
        a[1] = [1,.2]; b[5] = [.8,.9]
        a[5] = [.1,.9]; b[1] = [.1,.9]
        self.assertEqual(rank(bridge_scores(masks,[a,b],[1,5])), [0,1])
        self.assertEqual(rank(bridge_scores(masks,[a,b],[5,1])), [1,0])
        self.assertEqual(rank(bridge_scores(masks,[a,b],[None,5])), [])
        self.assertEqual(rank(bridge_scores(masks,[a,b],[5,5])), [])
        self.assertEqual(rank([.5,.5]), [0,1])

    def test_exemplar_excluded(self):
        masks = np.ones((2,2,2), bool)
        exemplar = np.eye(2, dtype=bool)
        self.assertFalse(union(masks,[0],exemplar)[exemplar].any())
        self.assertFalse(union(masks,[],exemplar).any())


if __name__ == '__main__':
    unittest.main()
