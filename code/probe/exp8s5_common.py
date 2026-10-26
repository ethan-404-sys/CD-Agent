"""Shared cache readers and exact mask accounting; see README_batch46.md."""
from __future__ import annotations
import argparse
from functools import lru_cache
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['MPLCONFIGDIR'] = str(HERE / '.mplconfig')
sys.path.insert(0, str(HERE.parent / 'batch43'))
import exp8s3_common as c
import numpy as np
from scipy import ndimage as ndi
from common import load_rgb

SOURCE = HERE.parent / 'batch43/run'
OLD_PAGE = HERE.parent / 'batch44/run'
EIGHT = np.ones((3, 3), bool)
CELLS = (1, 2, 3, 5)
FILTERS = ('F0', 'F1', 'F2', 'F3')
KS = (1, 2, 3, 5, 10, 20, 50, 'all')
SEEDS = (1020, 1021, 1022)
SMOKE_FIRST = {'second': dict(train='train_0.png', val='train_1049.png', test='test_0.png'),
               'sysu': dict(train='train_12044.png', val='train_12600.png', test='test_1000.png')}
read = c.read_json


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE) or path == HERE:
        raise ValueError('Output must be inside batch46')
    return path


def write(path, value):
    c.write_json(local(path), value)


def parser(description, stages):
    p = argparse.ArgumentParser(description=description)
    p.add_argument('stage', choices=stages)
    p.add_argument('--out', type=Path, default=HERE / 'run')
    p.add_argument('--dataset', choices=('second', 'sysu'), default='second')
    p.add_argument('--split', choices=('train', 'val', 'test'), default='val')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard', type=int, default=0)
    return p


def setup(p):
    a = p.parse_args()
    a.out = local(a.out)
    if a.limit < 0 or not 0 <= a.shard < a.shards:
        raise ValueError('Require limit >= 0 and 0 <= shard < shards')
    a.out.mkdir(parents=True, exist_ok=True)
    return a


@lru_cache(maxsize=2)
def manifest(ds):
    m = read(SOURCE / ds / 'manifest.json')
    r = m['rule']
    if m['partial'] or r['tau'] != .1 or r['prefilter_novel_lt'] is not None or r['A_max'] is not None:
        raise ValueError('Requires complete, unfiltered own-r < 0.1 batch43 inventory')
    return m


def scope_names(ds, split, limit):
    seq = manifest(ds)['images'][split]
    if limit:
        first = SMOKE_FIRST[ds][split]
        if first not in seq:
            raise ValueError('Fixed real smoke image no longer belongs to the split')
        seq = ([first] + [name for name in seq if name != first])[:limit]
    return seq


def names(a, sharded=False):
    seq = scope_names(a.dataset, a.split, a.limit)
    return seq[a.shard::a.shards] if sharded else seq


def part_path(a, stage, name):
    return a.out / a.dataset / stage / a.split / (Path(name).stem + '.json')


def envelope(a, name, **value):
    return dict(dataset=a.dataset, split=a.split, image=name, limit=a.limit,
                partial=bool(a.limit), **value)


def parts(a, stage):
    for name in names(a):
        row = read(part_path(a, stage, name))
        if any(row[k] != v for k, v in dict(dataset=a.dataset, split=a.split,
                                           image=name, limit=a.limit).items()):
            raise ValueError(f'{stage}/{name}: different inventory or smoke scope')
        yield row


def fraction(n, d):
    return float(n / d) if d else None


def pixels(ds, split, name):
    if split == 'train':
        return c.train_pixels(ds, name)[:4]
    # Read the same batch30/34 data as b40.pixels, but retain bad cell labels for
    # the requested before/after audit instead of rejecting them before repair.
    raw = c.raw_dataset(ds, split)
    z = c.archive(c.b40.base_dir(raw) / Path(name).stem / 'pixels.npz')
    shape = tuple(z['shape'])
    gt, base = [np.unpackbits(z[k], count=int(np.prod(shape))).reshape(shape).astype(bool)
                for k in ('gt', 'base')]
    prob = z['probability']
    cell = c.archive(c.OUT / 'exp8s_batch34/interim' / raw / (Path(name).stem + '.npz'))['cell']
    if cell.shape != shape or prob.shape != shape or not np.isfinite(prob).all():
        raise ValueError(f'{ds}/{name}: invalid pixel cache')
    if not np.array_equal(prob > .5, base):
        raise ValueError(f'{ds}/{name}: D differs from probability > 0.5')
    return gt, base, prob, cell


def geometry(ds, split, name):
    gt, base, prob, old = pixels(ds, split, name)
    gl, ng, transitions = c.gt_label(c.raw_dataset(ds, split), name, gt)
    ga = np.bincount(gl.ravel(), minlength=ng + 1)
    gb = np.bincount(gl[base], minlength=ng + 1)
    chess = ndi.distance_transform_cdt(~base, metric='chessboard') if base.any() else None
    labels = np.zeros(gt.shape, np.int32)
    cell = np.zeros(gt.shape, np.uint8)
    relaxed = np.zeros_like(cell)
    rows = []
    for target, sl in enumerate(ndi.find_objects(gl), 1):
        if sl is None:
            continue
        cc, _ = ndi.label((gl[sl] == target) & ~base[sl], EIGHT)
        for k, box in enumerate(ndi.find_objects(cc), 1):
            mask = cc[box] == k
            ys = slice(sl[0].start + box[0].start, sl[0].start + box[0].stop)
            xs = slice(sl[1].start + box[1].start, sl[1].start + box[1].stop)
            area = int(mask.sum())
            distance = int(chess[ys, xs][mask].min()) if chess is not None else None
            group = (5 if min(ga[target], area) < 20 else 3 if gb[target] / ga[target] >= .1
                     else 1 if distance is not None and distance <= 1 else 2)
            wide = 1 if group == 2 and distance is not None and distance <= 3 else group
            ident = len(rows) + 1
            labels[ys, xs][mask] = ident
            cell[ys, xs][mask] = group
            relaxed[ys, xs][mask] = wide
            old_values, old_counts = np.unique(old[ys, xs][mask], return_counts=True)
            rows.append(dict(id=ident, target=target, cell=group, relaxed_cell=wide, area=area,
                             target_area=int(ga[target]), detector_pixels=int(gb[target]),
                             detector_coverage=float(gb[target] / ga[target]), distance=distance,
                             transition=transitions.get(target), bbox=[xs.start, ys.start, xs.stop, ys.stop],
                             old_cells={str(int(v)): int(n) for v, n in zip(old_values, old_counts)}))
    if not np.array_equal(cell > 0, gt & ~base):
        raise ValueError('Reconstructed cells fail to partition FN')
    return dict(ds=ds, split=split, name=name, gt=gt, base=base, prob=prob, old=old,
                cell=cell, relaxed=relaxed, gl=gl, labels=labels, rows=rows, ga=ga, gb=gb)


def load(ds, split, name):
    d = geometry(ds, split, name)
    objects, shape = c.pool_objects(ds, split, name)
    z = c.archive(SOURCE / ds / 'units' / split / (Path(name).stem + '.npz'))
    if tuple(shape) != d['gt'].shape or str(z['image']) != name:
        raise ValueError('Unit cache shape/image mismatch')
    routed = []
    for ident, o in enumerate(objects):
        x0, y0, x1, y1 = o['bbox']
        sl = np.s_[y0:y1, x0:x1]
        if d['base'][sl][o['mask']].mean() < .1:
            routed.append(ident)
    if not np.array_equal(routed, z['id']):
        raise ValueError(f'{name}: cached fill group differs from own r < 0.1')
    candidates = []
    for j, ident in enumerate(z['id']):
        o = objects[int(ident)]
        x0, y0, x1, y1 = o['bbox']
        sl = np.s_[y0:y1, x0:x1]
        novel = o['mask'] & ~d['base'][sl]
        truth = int((novel & d['gt'][sl]).sum())
        if int(novel.sum()) != z['novel'][j] or truth != z['truth'][j]:
            raise ValueError(f'{name}/{ident}: novel cache mismatch')
        candidates.append(dict(id=int(ident), bbox=o['bbox'], mask=novel,
                               original=o['mask'], positive=bool(z['label'][j] == 1),
                               dscore=float(z['x'][j, 0]), index=j))
    d.update(objects=objects, z=z, candidates=candidates)
    return d


def filtered(d, scheme):
    result = []
    for u in d['candidates']:
        mask = u['mask']
        if scheme == 'F2':
            mask = ndi.binary_opening(mask, structure=EIGHT)
        if scheme in ('F1', 'F2') and int(mask.sum()) < 100:
            continue
        # A 2px inscribed radius removes one/two-pixel strokes but preserves
        # compact small regions; padding makes the image boundary explicit.
        if scheme == 'F3' and ndi.distance_transform_edt(np.pad(mask, 1)).max() < 2:
            continue
        x0, y0, x1, y1 = u['bbox']
        truth = int((mask & d['gt'][y0:y1, x0:x1]).sum())
        n = int(mask.sum())
        result.append(dict(u, mask=mask, novel=n, precision=truth / n if n else 0.,
                           positive=bool(n and 2 * truth >= n),
                           area=int((u['original'] & d['base'][y0:y1, x0:x1]).sum()) + n))
    return result


def add_mask(union, u):
    x0, y0, x1, y1 = u['bbox']
    union[y0:y1, x0:x1] |= u['mask']


def coverage(d, union):
    ga, gb, gl = d['ga'], d['gb'], d['gl']
    added = np.bincount(gl[union], minlength=len(ga))
    comp_added = np.bincount(d['labels'][union], minlength=len(d['rows']) + 1)
    stats = {}
    for group in (1, 2):
        ids = np.unique(gl[d['cell'] == group])
        rows = [r for r in d['rows'] if r['cell'] == group]
        stats.update({f'pixels{group}': int((d['cell'] == group).sum()),
                      f'covered{group}': int((union & (d['cell'] == group)).sum()),
                      f'objects{group}': len(ids),
                      f'hits{group}': int((2 * (gb[ids] + added[ids]) >= ga[ids]).sum()),
                      f'components{group}': len(rows),
                      f'component_hits{group}': sum(2 * comp_added[r['id']] >= r['area'] for r in rows)})
    return {k: int(v) for k, v in stats.items()}


def rates(row):
    r = dict(row)
    for g in (1, 2):
        r[f'pixel_recall{g}'] = fraction(r[f'covered{g}'], r[f'pixels{g}'])
        r[f'object_recall{g}'] = fraction(r[f'hits{g}'], r[f'objects{g}'])
        r[f'component_recall{g}'] = fraction(r[f'component_hits{g}'], r[f'components{g}'])
    r['candidates_per_image'] = fraction(r['candidates'], r['images'])
    r['positive_fraction'] = fraction(r['positives'], r['candidates'])
    r['positive_per_image'] = fraction(r['positives'], r['images'])
    r['mllm_calls'] = r['candidates']
    r['gpu_hours'] = r['candidates'] * .44 / 3600
    if 'dropped' in r:
        r['dropped_positive_fraction'] = fraction(r['dropped_positive'], r['dropped'])
    return r


def sum_rows(rows):
    result = {}
    for row in rows:
        for k, v in row.items():
            result[k] = result.get(k, 0) + v
    return result


@lru_cache(maxsize=2)
def scores(ds, split):
    z = c.archive(SOURCE / ds / 'scores/M1-ign3' / (split + '.npz'))
    if z['images'].tolist() != manifest(ds)['images'][split] or not np.isfinite(z['score']).all():
        raise ValueError('M1 score inventory differs or contains nonfinite values')
    if (np.diff(z['image_id']) < 0).any():
        raise ValueError('M1 image IDs must be in inventory order')
    return z


def image_scores(d):
    z = scores(d['ds'], d['split'])
    idx = manifest(d['ds'])['images'][d['split']].index(d['name'])
    lo, hi = np.searchsorted(z['image_id'], [idx, idx + 1])
    if not np.array_equal(z['id'][lo:hi], d['z']['id']):
        raise ValueError('M1 score unit IDs differ')
    return z['score'][lo:hi]


def frozen(a, ds=None):
    row = read(a.out / (ds or a.dataset) / 'frozen_filter.json')
    if row['limit'] != a.limit:
        raise ValueError('Frozen filter belongs to a different smoke/full scope')
    return row


def timed(a, stage, started, **extra):
    row = dict(stage=stage, dataset=a.dataset, split=a.split, limit=a.limit,
               shard=a.shard, shards=a.shards, seconds=time.monotonic() - started,
               torch_imported='torch' in sys.modules, **extra)
    if row['torch_imported']:
        raise RuntimeError('Unexpected torch import')
    write(a.out / 'timing' / f'{a.dataset}-{stage}-{a.split}-{a.shard}.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
