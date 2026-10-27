"""Cross-check two-image real smoke outputs against upstream masks and independent metrics."""
from exp8s7_common import *
from sklearn.metrics import roc_auc_score, average_precision_score


def verify(a):
    checks = []
    for split in ('train', 'val'):
        z = load_units(a, split)
        scheme = str(z['scheme'])
        for i, name in enumerate(z['images']):
            d = s5.load(a.dataset, split, str(name))
            kept = s6.units(d, scheme)
            rows = image_rows(z, i)
            assert len(rows) == len(kept)
            original_m1 = s5.image_scores(d)
            for j, original in zip(rows, kept):
                decoded = unpack(z, j)
                actual = np.zeros_like(d['gt']); expected = actual.copy()
                s5.add_mask(actual, decoded); s5.add_mask(expected, original)
                assert np.array_equal(actual, expected), (name, 'packed mask')
                assert not (actual & d['base']).any(), (name, 'not novel')
                precision = float(d['gt'][actual].mean())
                assert np.isclose(z['precision'][j], precision)
                assert z['label'][j] == (1 if precision >= .5 else 0 if precision < .1 else -1)
                assert int(z['novel'][j]) == actual.sum()
                ct = np.array([((d['cell'] == k) & actual).sum() for k in CELLS])
                assert z['majority'][j] == (CELLS[int(ct.argmax())] if ct.sum() else 0)
                assert np.array_equal(z['x'][j], d['z']['x'][original['index']])
                assert z['m1score'][j] == original_m1[original['index']]
                assert z['dscore'][j] == z['x'][j, 0]
            checks.append(dict(split=split, image=str(name), units=len(rows), masks_labels_scores='matched'))
    z = load_units(a, 'val')
    report = c.read_json(a.out / a.dataset / 'evaluation/comparison/val.json')
    frozen = c.read_json(a.out / a.dataset / 'evaluation/comparison/frozen.json')['methods']
    values = dict(D=z['dscore'], **{'M1-ign3': z['m1score']}, oracle=z['precision'],
                  **{'meta-m1': align_scores(a.out / a.dataset / 'scores/meta-m1/val.npz', z)})
    for method, score in values.items():
        take = cell2_selection(z)
        y = z['label'][take]
        if len(np.unique(y)) == 2:
            assert np.isclose(report['ranking'][method]['cell2']['auc'], roc_auc_score(y, score[take]))
            assert np.isclose(report['ranking'][method]['cell2']['ap'], average_precision_score(y, score[take]))
        for key, row in report['points'][method].items():
            chosen = frozen[method][key]
            assert row['delta_f1'] >= -float(key)-1e-9
            assert np.isclose(row['delta_f1'], chosen['delta_f1'])
            if row['object_recall2'] is not None:
                assert np.isclose(100*row['object_recall2'], chosen['object_recall2'])
        recalls = [report['budget'][method][str(k)]['object_recall2'] for k in KS]
        assert all(b >= a for a, b in zip(recalls, recalls[1:]) if a is not None and b is not None)
    expected_hits, expected_objects = 0, 0
    for name in z['images']:
        ref = c.read_json(s6.HERE / 'run' / a.dataset / 'filter6/val' / (Path(str(name)).stem+'.json'))['schemes'][str(z['scheme'])]
        expected_hits += ref['hits2']; expected_objects += ref['objects2']
    for method in values:
        all_budget = report['budget'][method]['all']
        assert all_budget['hits2'] == expected_hits and all_budget['objects2'] == expected_objects
    assert report['ranking']['D']['cell2']['bootstrap_replicates'] == 2000
    assert not (a.out / a.dataset / 'units/test.npz').exists()
    assert not list((a.out / a.dataset / 'models').glob('*/model.pt'))
    import csv
    from PIL import Image
    folder = a.out / a.dataset / 'errors/meta-m1'
    with (folder / 'annotations.csv').open(newline='') as f:
        annotations = list(csv.DictReader(f))
    for row in annotations:
        assert row['category'] == '' and row['explanation'] == ''
        with Image.open(folder / row['file']) as im:
            assert im.size == (1280, 350)
            im.verify()
    result = dict(dataset=a.dataset, checks=checks, metric_crosscheck='sklearn AUC/AP matched',
                  spatial_crosscheck='batch47 all-k hits and exact curve/direct frozen points matched',
                  bootstrap_replicates=2000, error_images=len(annotations), checkpoint_saved=False,
                  test_read=False, status='passed')
    write(a.out / a.dataset / 'verification.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    p = parser(__doc__)
    a = setup(p)
    if a.limit != 2:
        p.error('This verification is specifically for --limit 2 smoke outputs')
    verify(a)


if __name__ == '__main__':
    main()
