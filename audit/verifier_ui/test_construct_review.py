"""CPU-only integration checks. Run through the project Slurm experiment loop."""
import copy
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import construct_review_store as store
import construct_review_qc as qc
import construct_review_import as prepare
import construct_review_api as api


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = store.connect(str(self.root / 'review.db'))
        self.env = patch.dict(os.environ, TASK_DATA_DB=str(self.root / 'review.db'), CONSTRUCT_REVIEW_BACKUP='')
        self.env.start()
        self.task = self.fixture()

    def tearDown(self):
        self.env.stop()
        self.db.close()
        self.temp.cleanup()

    def fixture(self):
        a = np.ones((64, 64), np.uint8)
        b = a.copy()
        b[4:7, 4:7] = 5
        b[50:53, 50:53] = 5
        b[4:7, 12:15] = 4
        candidate = b == 5
        reference = b == 4
        gt = np.zeros_like(candidate)
        gt[4:7, 4:7] = True
        for key, image in dict(s1=a, s2=b, gt=gt.astype('uint8') * 255,
                               reference=reference.astype('uint8') * 255, candidate=candidate.astype('uint8') * 255).items():
            Image.fromarray(image).save(self.root / (key + '.png'))
        for key in ('t1', 't2'):
            Image.new('RGB', (64, 64), (60, 80, 100)).save(self.root / (key + '.png'))
        return dict(id='example', task_type='T4.4', image_t1=str(self.root / 't1.png'), image_t2=str(self.root / 't2.png'),
                    instruction='Find newly built buildings near new water.', target='building', change_direction='appeared',
                    gt_mask=str(self.root / 'gt.png'), meta=dict(review_id='stable-example', data_version='v1', dataset='synthetic_SECOND',
                    split='dev', scene_id='scene1', source_annotations=[dict(path=str(self.root / f's{i}.png'), version='synthetic', feature_ids=[]) for i in (1, 2)],
                    relation_type='near', relation_params=dict(distance_pixels=20, reference_selection='all_new_class',
                        foreground_connectivity=8, background_connectivity=4, coordinate_system='image_x_right_y_down', unit='pixel'),
                    reference_class_id=4, reference_mask=str(self.root / 'reference.png'), candidate_mask=str(self.root / 'candidate.png'),
                    target_component_ids=[1], candidate_count=2, filtered_count=1))

    def record(self, task=None, report=None):
        task = task or self.task
        return dict(payload=task, qc=report or qc.task_qc(task), assets={},
                    observations=store.observe(list(qc.assets_for(task).values()) + qc.source_paths(task)), source='fixture.jsonl')

    def insert(self, record=None):
        record = record or self.record()
        with self.db:
            result = store.import_task(self.db, **record)
        return result

    def approve(self, confirmations=None):
        r = store.get(self.db, self.task['meta']['review_id'])
        with self.db:
            store.decide(self.db, r['task_id'], r['revision'], 'approved', 'synthetic test review', 'test-reviewer',
                         r['qc']['manual_checks'] if confirmations is None else confirmations)

    def test_complete_qc_and_manual_gate(self):
        report = qc.task_qc(self.task)
        self.assertTrue(report['passed'], report)
        self.assertIn('reference_visual', report['manual_checks'])
        self.insert()
        self.assertEqual(store.export(self.db)['total'], 0)
        with self.assertRaises(ValueError):
            self.approve([])
        self.approve()
        result = store.export(self.db)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['tasks'][0]['meta']['reviewer'], 'test-reviewer')

    def test_revision_reset_and_stale_decision(self):
        record = self.record()
        self.insert(record)
        self.approve()
        self.assertEqual(self.insert(record), (1, False))
        revised = copy.deepcopy(record)
        revised['payload']['instruction'] += ' Revised.'
        revised['payload']['meta']['data_version'] = 'v2'
        self.assertEqual(self.insert(revised), (2, True))
        latest = store.get(self.db, 'stable-example')
        self.assertEqual(latest['status'], 'pending_review')
        self.assertEqual(latest['confirmations'], [])
        self.assertEqual(store.get(self.db, 'stable-example', 1)['status'], 'approved')
        self.assertEqual(len(latest['history']), 2)
        with self.assertRaises(ValueError):
            store.decide(self.db, 'stable-example', 1, 'excluded', '', 'test-reviewer')

    def test_auto_unknown_and_error_never_export(self):
        for severity in ('hard_error', 'unverified'):
            report = qc.task_qc(self.task)
            report['passed'] = False
            report['findings'].append(qc.issue('test_issue', severity, 'fixture'))
            self.insert(self.record(report=report))
            self.approve()
            self.assertEqual(store.export(self.db)['total'], 0)

    def test_source_change_invalidates_approval(self):
        self.insert()
        self.approve()
        path = Path(self.task['gt_mask'])
        path.write_bytes(path.read_bytes() + b'x')
        self.assertIn('source_changed_since_qc', store.export(self.db)['excluded'][0]['reasons'])

    def test_relation_and_source_recompute(self):
        bad = copy.deepcopy(self.task)
        bad['meta']['filtered_count'] = 0
        codes = {f['code'] for f in qc.task_qc(bad)['findings']}
        self.assertIn('relation_filtered_count', codes)
        Image.fromarray(np.zeros((64, 64), np.uint8)).save(self.root / 'gt.png')
        self.assertIn('relation_gt_mismatch', {f['code'] for f in qc.task_qc(self.task)['findings']})

    def test_duplicates_and_split_leak(self):
        other = copy.deepcopy(self.task)
        other['meta'].update(review_id='other', split='test')
        reports, summary = qc.batch_qc([self.task, other])
        self.assertEqual(summary['passed'], 0)
        self.assertTrue(all({'scene_split_leak', 'duplicate_task'}.issubset({f['code'] for f in r['findings']}) for r in reports))

    def test_full_import_retires_and_preserves_legacy(self):
        with self.db:
            self.db.execute('INSERT INTO task_example(id,payload,verdict) VALUES (?,?,?)', ('old', '{}', 'reasonable'))
            prepare.import_bundle(self.db, dict(format_version=1, full_snapshot=True, records=[self.record()]))
        self.assertEqual(store.get(self.db, 'stable-example')['status'], 'pending_review')
        self.assertEqual(self.db.execute('SELECT verdict FROM task_example').fetchone()[0], 'reasonable')
        self.approve()
        with self.db:
            prepare.import_bundle(self.db, dict(format_version=1, full_snapshot=True, records=[]))
        self.assertEqual(store.export(self.db)['total'], 0)
        self.assertEqual(store.get(self.db, 'stable-example', 1)['status'], 'approved')

    def test_review_backup_restores_history_and_reports_failure(self):
        self.insert()
        target = self.root / 'shared' / 'review-backup.db'
        body = api.Decision(ident='stable-example', revision=1, status='needs_revision',
                            note='Check source boundary', reviewer='test-reviewer')
        with patch.dict(os.environ, CONSTRUCT_REVIEW_BACKUP=str(target)):
            response = api.review(body)
            self.assertTrue(response['ok'])
            # Opening the snapshot alone restores the committed review and its history.
            with sqlite3.connect(target) as restored:
                state = restored.execute('SELECT status,note,reviewer FROM construct_revision').fetchone()
                self.assertEqual(state, ('needs_revision', 'Check source boundary', 'test-reviewer'))
                history = restored.execute('SELECT action,after_value FROM construct_review_history').fetchall()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0][0], 'human_review')
                self.assertEqual(json.loads(history[0][1])['note'], body.note)
            body.note = 'Second decision remains local'
            with patch.object(store, 'backup', side_effect=OSError('shared disk unavailable')):
                failure = api.review(body)
            self.assertEqual(failure.status_code, 503)
            self.assertTrue(json.loads(failure.body)['saved_local'])
            local = store.get(self.db, 'stable-example')
            self.assertEqual(local['note'], body.note)
            self.assertEqual(len(local['history']), 2)
            # An explicit retry after import/review replaces the snapshot with current state.
            store.backup(self.db)
            with sqlite3.connect(target) as restored:
                self.assertEqual(restored.execute('SELECT COUNT(*) FROM construct_review_history').fetchone()[0], 2)
            self.assertEqual(list(target.parent.glob('*.tmp')), [])

    def test_build_all_rows_and_api(self):
        tasks = []
        for index in range(5):
            task = copy.deepcopy(self.task)
            task['id'] = f'task-{index}'
            task['meta']['review_id'] = f'review-{index}'
            task['instruction'] += f' Test {index}.'
            tasks.append(task)
        manifest = self.root / 'tasks.jsonl'
        manifest.write_text(''.join(json.dumps(t) + '\n' for t in tasks))
        summary = prepare.build([manifest], self.root / 'bundle')
        self.assertEqual(summary['total'], 5)
        bundle = json.loads((self.root / 'bundle/review_bundle.json').read_text())
        with self.db:
            prepare.import_bundle(self.db, bundle)
        listed = api.tasks(status='pending_review')
        self.assertEqual(listed['total'], 5)
        self.assertEqual(len(listed['items']), 5)
        detail = api.item('review-0')
        self.assertTrue(detail['sources_current'])
        self.assertEqual(api.image('review-0', 1, 'reference').media_type, 'image/png')
        self.assertEqual(json.loads(api.export('approved').body)['total'], 0)
        # Rebuilding unchanged files reuses immutable preview paths and preserves approval.
        record = store.get(self.db, 'review-0')
        with self.db:
            store.decide(self.db, 'review-0', 1, 'approved', '', 'test-reviewer', record['qc']['manual_checks'])
        prepare.build([manifest], self.root / 'bundle')
        with self.db:
            imported = prepare.import_bundle(self.db, json.loads((self.root / 'bundle/review_bundle.json').read_text()))
        self.assertEqual(imported['new_revisions'], 0)
        self.assertEqual(store.get(self.db, 'review-0')['status'], 'approved')


if __name__ == '__main__':
    unittest.main(verbosity=2)
