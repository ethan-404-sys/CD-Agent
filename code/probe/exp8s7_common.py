"""Shared batch48 inventory and file formats; upstream caches are read-only."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
for batch in ('batch43', 'batch46', 'batch47'):
    sys.path.insert(0, str(HERE.parent / batch))
import exp8s5_common as s5
import exp8s6_filter as s6
import numpy as np
from scipy import ndimage as ndi
os.environ['MPLCONFIGDIR'] = str(HERE / '.mplconfig')
c = s5.c
CELLS = (1, 2, 3, 5)
KS = (1, 2, 3, 5, 10, 20, 'all')
MODEL = '/guisongxia01/pangchao/wangyihan/models/dinov3/dinov3-vitl16-pretrain-sat493m'


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE) or path.is_relative_to(HERE / 'consult'):
        raise ValueError('Outputs must stay inside batch48, outside consult/')
    return path


def write(path, value):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def save(path, **arrays):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('wb') as f:
        np.savez_compressed(f, **arrays)
    temp.replace(path)


def parser(doc):
    p = argparse.ArgumentParser(description=doc)
    p.add_argument('--dataset', choices=('second', 'sysu'), required=True)
    p.add_argument('--out', type=Path, default=HERE / 'run')
    p.add_argument('--limit', type=int, default=0)
    return p


def setup(p):
    a = p.parse_args()
    a.out = local(a.out)
    if a.limit < 0:
        p.error('--limit must be nonnegative')
    if hasattr(a, 'shards') and not 0 <= a.shard < a.shards:
        p.error('Require 0 <= shard < shards')
    return a


def shard_args(p):
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard', type=int, default=0)


def unit_path(a, split):
    return a.out / a.dataset / 'units' / f'{split}.npz'


def load_units(a, split):
    z = c.archive(unit_path(a, split))
    if int(z['limit']) != a.limit or str(z['dataset']) != a.dataset or str(z['split']) != split:
        raise ValueError('Unit inventory has different dataset/split/limit')
    if z['images'].tolist() != s5.scope_names(a.dataset, split, a.limit):
        raise ValueError('Unit images differ from source manifest')
    return z


def image_rows(z, i):
    lo, hi = np.searchsorted(z['image_id'], [i, i + 1])
    return np.arange(lo, hi)


def unpack(z, j):
    x0, y0, x1, y1 = z['bbox'][j]
    lo, hi = z['mask_offsets'][j:j+2]
    mask = np.unpackbits(z['mask_data'][lo:hi], count=int((x1-x0)*(y1-y0)))
    return dict(id=int(z['id'][j]), bbox=(x0, y0, x1, y1), mask=mask.reshape(y1-y0, x1-x0).astype(bool))


def align_scores(path, z):
    q = c.archive(path)
    for k in ('images', 'image_id', 'id'):
        if not np.array_equal(q[k], z[k]):
            raise ValueError(f'{path}: {k} differs from filtered inventory')
    if q['score'].shape != z['id'].shape or not np.isfinite(q['score']).all():
        raise ValueError(f'{path}: invalid scores')
    return q['score']


def save_scores(path, z, score):
    if score.shape != z['id'].shape or not np.isfinite(score).all():
        raise ValueError('Invalid prediction array')
    save(path, score=score, **{k: z[k] for k in ('images', 'image_id', 'id')})


def rank_metrics(y, score):
    if len(np.unique(y)) != 2:
        return dict(auc=None, ap=None)
    auc, ap = c.b40.Ranking(y, score).evaluate(np.ones(len(y)))
    return dict(auc=auc, ap=ap)


def cell2_selection(z):
    return (z['label'] == 0) | ((z['label'] == 1) & (z['majority'] == 2))
