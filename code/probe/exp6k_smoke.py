#!/usr/bin/env python3
"""真实缓存小冒烟；不生成假审核，不放宽正式定稿配额。"""
from exp6k_common import *
import argparse
from collections import Counter
from contextlib import closing
import subprocess
import time

CPU = WORKSPACE / 'conda_envs/exp5-env/bin/python'
STORE = WORKSPACE / 'conda_envs/exp6-v2-env/bin/python'


def run(a, dataset, stage, *extra, store=False):
    command = [str(STORE if store else CPU), '-B', str(HERE / 'exp6k_prep.py'), stage,
               '--dataset', dataset, '--limit', str(a.limit), '--out', str(a.out), *extra]
    subprocess.run(command, check=True, cwd=HERE)


def verify(a):
    from exp6k_store import check_sets
    from exp6k_select import auto_positive
    from io import BytesIO
    result = {}
    for ds in ('second', 'sysu'):
        collections = {kind: read(a.out / f'exp6k_{kind}_{ds}.json') for kind in ('shortlist', 'extra', 'dev', 'test')}
        check_sets(collections, check_files=True)
        for kind, rows in collections.items():
            for row in rows:
                for k, path in row['inputs'].items():
                    with Image.open(path) as im:
                        im.load()
                        assert im.format == 'JPEG'
                        if k in ('t1', 't2'):
                            assert im.size == (384, 384)
                if row['split'] == 'train' and row['label']:
                    assert row['rationale'] == auto_positive(row)['rationale']
                    assert row['rationale_source'] == 'auto'
        training = collections['shortlist'] + collections['extra']
        assert len({key(r) for r in training}) == len(training)
        assert max(Counter(r['image'] for r in training).values(), default=0) <= 2
        for kind, split in [('dev', 'val'), ('test', 'test')]:
            table = read_csv(a.out / ds / f'table_{split}.csv')
            groups = read(a.out / f'exp6k_{kind}_{ds}_images.json')
            for stratum in STRATA:
                population = sum(r['stratum'] == stratum for r in table)
                chosen = [r for r in collections[kind] if r['stratum'] == stratum]
                assert len(chosen) == min(a.limit, population)
                assert abs(sum(r['weight'] for r in chosen)-population) < 1e-6
            for image, group in groups.items():
                selected = [r for r in collections[kind] if r['image'] == image]
                assert set(group['cids']) == {r['cid'] for r in selected}
                assert group['all_candidates'] >= group['eligible_candidates'] >= len(selected)
        annotation_path = a.out / f'annotations_{ds}.csv'
        with annotation_path.open(encoding='utf-8-sig', newline='') as f:
            annotations = list(csv.DictReader(f))
        negatives = [r for r in collections['shortlist'] if r['label'] == 0]
        assert {key(r) for r in annotations} == {key(r) for r in negatives}
        assert all(r['gt_doubt'] == '' and r['rationale'] == '' for r in annotations)
        assert not list((a.out / ds / 'sheets').glob('pos_*.jpg'))
        assert len(list((a.out / ds / 'sheets').glob('neg_*.jpg'))) == (len(negatives)+11)//12
        # 对一条真实训练候选逐像素核对 exp6j 输出经同样 JPEG 编码后的结果。
        row = collections['shortlist'][0]
        z = inventory(ds, 'train', masks=True)
        image_id = z['images'].tolist().index(row['image'])
        j = next(j for j in image_indices(z, image_id) if int(z['id'][j]) == row['unit_id'])
        rgb = rgb_pair(ds, 'train', row['image'])
        mask = np.zeros(rgb[0].shape[:2], bool)
        x0, y0, x1, y1 = row['bbox']
        mask[y0:y1, x0:x1] = unpack(z, j)
        crop_box, render_pair = load_crop_functions()
        reference = render_pair(rgb, mask, None, crop_box(row['bbox'], mask.shape))
        for k, im in zip(('t1', 't2'), reference):
            buffer = BytesIO()
            im.save(buffer, format='JPEG', quality=90, subsampling=0)
            buffer.seek(0)
            with Image.open(buffer) as expected, Image.open(row['inputs'][k]) as actual:
                assert np.array_equal(np.asarray(expected), np.asarray(actual))
        result[ds] = {kind: len(rows) for kind, rows in collections.items()}
    assert 'torch' not in sys.modules
    write(a.out / 'smoke_verification.json', dict(status='passed', counts=result,
          checks=['真实缓存字段与渲染路径', '384px JPEG', 'exp6j 近景逐像素一致',
                  '正例自动理由及门槛', '每图上限与候选不重叠', '分层权重还原',
                  '按图候选清单', '查询无真值语义输入', '负例审核表与接触图', '未加载 torch'],
          not_executed=['完整64/256定稿（等待真实负例审核）', 'Claude尚未提供的系统提示词', '正式全量Slurm运行']))
    print(json.dumps(result, ensure_ascii=False), flush=True)


def verify_store(a):
    os.environ['VERIFIER_DB'] = str(a.out / 'verifier.db')
    sys.path.insert(0, str(HERE / 'audit/verifier_ui'))
    import store
    import app
    expected = {f'exp6k_{kind}_{ds}': len(read(a.out / f'exp6k_{kind}_{ds}.json'))
                for ds in ('second', 'sysu') for kind in ('dev', 'test')}
    # 再登记真实样本，检查集合不会重复累积。
    for ds in ('second', 'sysu'):
        run(a, ds, 'writedb', '--sets', 'dev', 'test', '--samples-only', store=True)
    sets = app.sample_sets()
    assert {s['name']: s['n'] for s in sets} == expected
    assert app.checks() == []
    for head in sets:
        data = app.sample_set(head['id'])
        assert len(data['items']) == head['n']
        assert data['violations'] == []
    with closing(store.connect(a.out / 'verifier.db')) as db:
        assert db.execute('SELECT COUNT(*) FROM sample').fetchone()[0] == sum(expected.values())
        assert db.execute('SELECT COUNT(*) FROM sample_set_item WHERE review_status IS NOT NULL').fetchone()[0] == 0
    write(a.out / 'store_verification.json', dict(status='passed', sets=expected,
          checks=['重复登记不累积', '前端集合与详情读取', '按图划分检查', '审核状态保持未审'],
          scope='调用真实前端函数读取 SQLite；未启动 HTTP 服务'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit', type=int, default=4)
    p.add_argument('--out', type=Path, default=HERE / 'smoke_v2')
    p.add_argument('--verify-only', action='store_true')
    p.add_argument('--verify-store', action='store_true', help='使用 exp6-v2-env 执行真实前端读库验证')
    a = p.parse_args()
    a.out = local(a.out)
    if not 1 <= a.limit <= 4 or a.out == HERE / 'run':
        p.error('登录节点冒烟仅支持 1–4 图，且必须使用独立目录')
    started = time.monotonic()
    if a.verify_store:
        verify_store(a)
        return
    if not a.verify_only:
        for ds in ('second', 'sysu'):
            for split in ('train', 'val', 'test'):
                run(a, ds, 'table', '--split', split)
            run(a, ds, 'shortlist')
            run(a, ds, 'sample')
            run(a, ds, 'render')
            run(a, ds, 'sheets')
            run(a, ds, 'writedb', '--sets', 'dev', 'test', '--samples-only', store=True)
        run(a, 'second', 'summary')
    verify(a)
    if not a.verify_only:
        subprocess.run([str(STORE), '-B', __file__, '--limit', str(a.limit), '--out', str(a.out), '--verify-store'], check=True)
    print(f'smoke elapsed={time.monotonic()-started:.2f}s', flush=True)


if __name__ == '__main__':
    main()
