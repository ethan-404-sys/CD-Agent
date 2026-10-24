"""batch43 shared paths, immutable run inventory and cache readers. See README_batch43.md."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time
from functools import lru_cache

sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
HERE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
STAGING = HERE.parent
sys.path.insert(0, str(STAGING / 'batch40'))
import exp8s2_judge as b40
import numpy as np
from scipy import ndimage
from common import OUT, DATASETS, load_label
from objects import gt_label

DATASETS['sysu_val'] = {**DATASETS['sysu'], 'prefix': 'train_'}
SPLITS = ('train', 'val', 'test')
SEEDS = (1020, 1021, 1022)
METHODS = ('M0', 'D', 'O', 'M1', 'M1-s1021', 'M1-s1022', 'M2', 'M3',
           'M1-25', 'M1-25-s1021', 'M1-25-s1022', 'M1-50', 'M1-50-s1021',
           'M1-50-s1022', 'M1-ign3', 'M1-pF', 'M1-p25')
BUDGETS = (.3, .5, 1., 2.)
CELLS = (1, 2, 3, 5)
EIGHT = np.ones((3, 3), bool)
read_json = b40.read_json
write_json = b40.write_json
save_npz = b40.save_npz


def root(dataset):
    return OUT / ('exp8_batch22' if dataset == 'second' else 'exp8_batch24/u5')


def raw_dataset(dataset, split):
    return dataset if split == 'test' else ('second_cv' if dataset == 'second' else 'sysu_val')


def pool_dir(dataset, split):
    if dataset == 'sysu':
        return OUT / 'exp8_batch19/pool' / raw_dataset(dataset, split)
    return OUT / ('exp8_batch20/pool/second_cv_train' if split == 'train'
                  else 'exp8_batch18/pool/' + raw_dataset(dataset, split))


def pool_objects(dataset, split, name):
    return b40.load_units(pool_dir(dataset, split) / 'U5_x4' / (Path(name).stem + '.npz'))


def archive(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def unit_path(args, split, name):
    return args.out / args.dataset / 'units' / split / (Path(name).stem + '.npz')


def score_path(args, method, split):
    return args.out / args.dataset / 'scores' / method / (split + '.npz')


def method_info(method):
    seed = int(method.rsplit('-s', 1)[1]) if '-s' in method else SEEDS[0]
    return method.split('-s')[0], seed


def parser(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument('--out', type=Path, default=HERE / 'run')
    p.add_argument('--dataset', choices=('second', 'sysu'), required=True)
    p.add_argument('--limit', type=int, default=0)
    return p


def setup(args):
    args.out = args.out.resolve()
    if not args.out.is_relative_to(HERE) or args.out == HERE:
        raise ValueError('--out must be a subdirectory of batch43')
    if args.limit < 0:
        raise ValueError('--limit must be nonnegative')
    args.out.mkdir(parents=True, exist_ok=True)
    return args


@lru_cache(maxsize=4)
def get_manifest(out, dataset):
    return read_json(Path(out) / dataset / 'manifest.json')


def manifest(args):
    m = get_manifest(str(args.out), args.dataset)
    if m['limit'] != args.limit:
        raise ValueError('Run --limit differs from initialized inventory')
    return m


def names(args, split, sharded=False):
    items = manifest(args)['images'][split]
    return items[args.shard::args.shards] if sharded else items


def shard_arguments(p):
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard', type=int, default=0)


def check_shard(args):
    if not 0 <= args.shard < args.shards:
        raise ValueError('Require 0 <= shard < shards')


def load_parts(args, split):
    result = [archive(unit_path(args, split, n)) for n in names(args, split)]
    for name, part in zip(names(args, split), result):
        if str(part['image']) != name or str(part['rule']) != manifest(args)['rule_text']:
            raise ValueError(f'{name}: unit inventory/rule mismatch')
    return result


def assemble(args, split):
    parts = load_parts(args, split)
    keys = ('id', 'x', 'label', 'precision', 'majority', 'novel', 'truth', 'cell_truth')
    data = {k: np.concatenate([p[k] for p in parts]) for k in keys}
    data['image_id'] = np.concatenate([np.full(len(p['id']), i, np.int32) for i, p in enumerate(parts)])
    data['counts'] = [len(p['id']) for p in parts]
    data['baseline'] = np.sum([p['baseline'] for p in parts], axis=0)
    if data['x'].shape[1] != len(manifest(args)['feature_names']) or not np.isfinite(data['x']).all():
        raise ValueError('Invalid V1 feature dimensions or nonfinite cached values')
    return data


def load_scores(args, method, split, data=None):
    z = archive(score_path(args, method, split))
    if z['images'].tolist() != names(args, split):
        raise ValueError('Scores have different image inventory')
    if data is not None and (not np.array_equal(z['id'], data['id']) or
                             not np.array_equal(z['image_id'], data['image_id'])):
        raise ValueError('Score unit keys differ')
    if not np.isfinite(z['score']).all():
        raise ValueError('Nonfinite scores')
    return z


def timing(args, stage, started, **extra):
    row = dict(stage=stage, dataset=args.dataset, seconds=time.monotonic()-started,
               peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, **extra)
    suffix = f'{stage}-{getattr(args, "method", "all")}-{getattr(args, "split", "all")}-{getattr(args, "shard", 0)}'
    write_json(args.out / args.dataset / 'timing' / (suffix + '.json'), row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def train_pixels(dataset, name):
    ds = raw_dataset(dataset, 'train')
    detector = 'mambascd_base' if dataset == 'second' else 'schanger_small'
    predroot = OUT / ('preds' if dataset == 'second' else 'exp8_batch19/preds')
    prob = np.load(predroot / ds / detector / 'prob' / (Path(name).stem + '.npy'), allow_pickle=False).astype(np.float32)
    gt = load_label(ds, name)
    base = prob > .5
    if prob.shape != gt.shape or not np.isfinite(prob).all():
        raise ValueError(f'{ds}/{name}: invalid cached probability')
    gl, count, _ = gt_label(ds, name, gt)
    cell = np.zeros(gt.shape, np.uint8)
    touching = ndimage.binary_dilation(base, structure=EIGHT)
    for ident, slices in enumerate(ndimage.find_objects(gl), 1):
        if slices is None:
            continue
        obj = gl[slices] == ident
        area = int(obj.sum())
        signal = int((obj & base[slices]).sum()) / area >= .1
        components, n = ndimage.label(obj & ~base[slices], EIGHT)
        for k, local in enumerate(ndimage.find_objects(components), 1):
            mask = components[local] == k
            size = int(mask.sum())
            c = 5 if min(area, size) < 20 else 3 if signal else 1 if (touching[slices][local] & mask).any() else 2
            cell[slices][local][mask] = c
    counts = np.array([(gt & base).sum(), (~gt & base).sum(), (gt & ~base).sum()], np.int64)
    return gt, base, prob, cell, counts


def pixels(dataset, split, name):
    if split == 'train':
        return train_pixels(dataset, name)
    gt, base, prob, cell, counts = b40.pixels(raw_dataset(dataset, split), name)
    return gt, base, prob, cell, counts[:3]


def features(dataset, split, name, objects, prob, details):
    path = root(dataset) / 'features' / split / (Path(name).stem + '.npz')
    if split != 'test':
        z = archive(path)
        if str(z['image']) != name or len(z['label']) != len(objects):
            raise ValueError(f'{path}: feature alignment mismatch')
        return np.concatenate([z['meta'][:, :11], z['extra']], axis=1), z['label']
    sources = ('B', 'G0.05', 'H1') if dataset == 'second' else ('B', 'G0.05')
    meta = np.zeros((len(objects), 11), np.float32)
    extra = np.zeros((len(objects), 2 ** len(sources)), np.float32)
    for i, (obj, row) in enumerate(zip(objects, details)):
        x0, y0, x1, y1 = obj['bbox']
        v = prob[y0:y1, x0:x1][obj['mask']]
        meta[i, :5] = (v.mean(), v.max(), v.min(), np.percentile(v, 90), np.log(obj['area']))
        code = sum(1 << sources.index(s) for s in row['sources'])
        for s in row['sources']:
            meta[i, 5 + b40.SOURCES.index(s)] = 1
        extra[i, 0], extra[i, code] = len(row['sources']), 1
    labels = np.array([{'positive': 1, 'negative': 0, 'middle': -1}[r['label']] for r in details], np.int8)
    return np.concatenate([meta, extra], axis=1), labels
