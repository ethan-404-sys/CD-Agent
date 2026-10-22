"""Shared batch20 inputs; batch18 algorithms are imported from their staging path."""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
STAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(STAGE.parent / 'batch18'))
import exp8_runtime as rt
# The runtime inserts its own dependency paths; restore batch18 for this import.
sys.path.insert(0, str(STAGE.parent / 'batch18'))
import exp8_pool as pool18

BATCH18 = rt.OUT / 'exp8_batch18'
DEFAULT_OUT = rt.OUT / 'exp8_batch20'
DATASETS = ('second_cv_train', 'second_cv', 'levir', 'whu')
MAIN = dict(second_cv_train='U5_x4', second_cv='U5_x4', second='U5_x4',
            levir='U5_x16', whu='objects_direct_pairs')
LABELS = ('positive', 'negative', 'middle')
FLAGS = ('tiny50', 'tiny100', 'thin', 'fragment', 'middle')
RULES = {'R1': ['tiny50', 'middle'],
         'R2': ['tiny100', 'thin', 'middle'],
         'R3': ['tiny100', 'thin', 'fragment', 'middle']}


def arguments(parser, datasets=DATASETS):
    parser.add_argument('--dataset', choices=datasets, required=True)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--batch18', type=Path, default=BATCH18)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--shard', type=rt.shard_type, default=(0, 1))


def source(dataset):
    return 'second_cv' if dataset == 'second_cv_train' else dataset


def names(dataset):
    if dataset in ('second_cv_train', 'second_cv'):
        train, val = rt.verifier_split()
        if (len(train), len(val)) != (2671, 297):
            raise ValueError('Expected SECOND verifier_train=2671 / verifier_val=297')
        return train if dataset == 'second_cv_train' else val
    return rt.image_names(dataset)


def selected_names(args, sharded=True):
    images = names(args.dataset)
    if not sharded and args.shard != (0, 1):
        raise ValueError('Aggregation/selection/audit requires --shard 0/1')
    return rt.subset(images, args)


def frozen(args):
    record = rt.read_json(args.batch18 / 'frozen/second_cv.json')
    geom = rt.load_geometry('second_cv', args.batch18)
    if (record['partial'] or record['validation_dataset'] != 'second_cv'
            or record['selection_metrics'] != ['pixel_f1', 'pixel_iou']
            or record['geometry'] != geom or geom['images'] != names('second_cv')
            or record['multiplier'] != 4 or record['threshold'] != 1088
            or record['threshold'] != 4 * geom['median_area']):
        raise ValueError('Expected complete batch18 SECOND frozen U5 x4 / 1088 px')
    return record


def pool_dir(args):
    root = args.out if args.dataset == 'second_cv_train' else args.batch18
    return root / 'pool' / args.dataset


def read_pool(args, name):
    row = rt.read_json(pool_dir(args) / 'images' / f'{Path(name).stem}.json')
    if row['image'] != name or row['config']['dataset'] != args.dataset:
        raise ValueError(f'{name}: wrong pool image/dataset')
    expected_partial = bool(args.limit) if args.dataset == 'second_cv_train' else False
    if row['config']['partial'] != expected_partial:
        raise ValueError(f'{name}: smoke and full pools must use separate output roots')
    if args.dataset.startswith('second'):
        if row['config']['geometry'] != frozen(args)['geometry']:
            raise ValueError(f'{name}: SECOND pool geometry differs from frozen parameters')
    elif args.dataset == 'levir':
        freeze = rt.read_json(args.batch18 / 'frozen/levir_val.json')
        if (freeze['partial'] or freeze['multiplier'] != 16
                or row['config']['geometry'] != freeze['geometry']
                or row['config']['status'] != 'frozen_validation'):
            raise ValueError('LEVIR requires frozen batch18 U5 x16 pool')
    return row


def checked_units(args, name, key, row):
    objects, shape = rt.load_units(pool_dir(args) / key / f'{Path(name).stem}.npz')
    details = row['units'][key]['details']
    if len(objects) != len(details) or len(objects) != row['units'][key]['unit_count']:
        raise ValueError(f'{name}/{key}: wrong saved unit count')
    for index, (obj, detail) in enumerate(zip(objects, details)):
        if (detail['unit_id'] != index or obj['bbox'] != detail['bbox']
                or obj['area'] != detail['area']):
            raise ValueError(f'{name}/{key}/{index}: saved mask metadata mismatch')
    return objects, shape, details


def add_mask(target, obj):
    x0, y0, x1, y1 = obj['bbox']
    target[y0:y1, x0:x1] |= obj['mask']


def oracle(objects, details, base, removed=()):
    excluded = set(removed)
    result = base.copy()
    for index, (obj, detail) in enumerate(zip(objects, details)):
        if index not in excluded and detail['label'] == 'positive':
            add_mask(result, obj)
    return result


def score(mask, gt, base, name):
    return dict(rt.metrics.image_metrics(mask, gt, before=base), image=name)


def ratio(a, b):
    return a / b if b else None


def write_fixed(path, value):
    if path.exists() and rt.read_json(path) != value:
        raise ValueError(f'{path}: existing definition differs; use a fresh output directory')
    rt.write_json(path, value)
