"""Batch18 paths, existing-method adapters and per-image storage."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'Chang-Agent/probe'
BATCH17 = PROBE / 'batch17'
if not (BATCH17 / 'exp8_common.py').is_file():
    BATCH17 = WORKSPACE / 'wyh/exp6_staging/batch17'
sys.path[:0] = [str(BATCH17), str(PROBE)]
import exp8_common as legacy
from exp8_dedup_viz import METHODS, RULES as DEDUP_RULES, groups_for, union_object

# exp7_units must use the exact batch16 metrics and partition functions.
sys.path.insert(0, str(PROBE / 'batch16'))
import numpy as np
from PIL import Image
import common
from exp6a_pieces import components, e4, geometry, slic, targets
from exp6_pool_io import POOL, DETECTOR, load_piece_masks
from exp6b_common import verifier_split
from exp7_units import coverage, disjoint_units, oracle, period_partition, temporal_partition

metrics = legacy.metrics
read_json, write_json = legacy.read_json, legacy.write_json
OUT = common.OUT
DEFAULT_OUT = OUT / 'exp8_batch18'
DATASETS = ('second_cv', 'second', 'sysu', 'levir_val', 'levir', 'whu')
EXPECTED = dict(second_cv=297, second=1694, sysu=4000, levir_val=64, levir=128, whu=690)
SOURCES = {d: ('B', 'G0.05', 'H1') if d.startswith('second') else ('B', 'G0.05') for d in DATASETS}
common.DATASETS['levir_val'] = {**common.DATASETS['levir'],
                              'root': WORKSPACE / 'datasets/LEVIR-CD/val_original'}
common.DATASETS['sysu_train'] = {**common.DATASETS['sysu'], 'prefix': 'train_'}
common.DATASETS['whu_train'] = common.TRAIN_DATASETS['whu']
DETECTOR['levir_val'] = DETECTOR['levir']
DETECTOR['sysu_train'] = DETECTOR['sysu']
DETECTOR['whu_train'] = DETECTOR['whu']
REVISION = 1


def image_names(dataset):
    images = verifier_split()[1] if dataset == 'second_cv' else common.names(dataset)
    expected = EXPECTED.get(dataset, {'sysu_train': 16000, 'whu_train': 1260}.get(dataset))
    if expected is not None and len(images) != expected:
        raise ValueError(f'{dataset}: expected {expected} images, found {len(images)}')
    return images


def shard_type(value):
    i, n = map(int, value.split('/'))
    if n < 1 or not 0 <= i < n:
        raise ValueError('Require 0 <= i < n for --shard i/n')
    return i, n


def subset(images, args):
    if args.limit < 0:
        raise ValueError('--limit must be nonnegative')
    return images[:args.limit or None][args.shard[0]::args.shard[1]]


def add_arguments(parser):
    parser.add_argument('--dataset', choices=DATASETS, required=True)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--shard', type=shard_type, default=(0, 1), help='zero-based i/n')
    parser.add_argument('--limit', type=int, default=0, help='first N images before sharding; 0=all')


def prediction_dir(dataset, out):
    root = Path(out) / 'preds' if dataset == 'levir_val' else common.PRED
    return root / dataset / DETECTOR[dataset]


def probability(dataset, name, out):
    return np.load(prediction_dir(dataset, out) / 'prob' / (Path(name).stem + '.npy'),
                   allow_pickle=False).astype(np.float32)


def prediction(dataset, name, out):
    return np.asarray(Image.open(prediction_dir(dataset, out) / 'mask' / name)) > 0


def sam3_path(dataset, name, out):
    old = OUT / 'exp4' / dataset / 'sam3' / (Path(name).stem + '.npz')
    return old if old.is_file() else Path(out) / 'sam3' / dataset / (Path(name).stem + '.npz')


def automatic_path(dataset, name, out):
    root = OUT / 'exp7_batch16/automatic' if dataset.startswith('second') else Path(out) / 'automatic'
    return root / dataset / (Path(name).stem + '.npz')


def save_npz(path, **arrays):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp.npz')
    np.savez_compressed(temporary, **arrays)
    temporary.replace(path)


def make_object(mask, **metadata):
    box, local = legacy.crop_mask(mask)
    return dict(bbox=box, mask=local, area=int(local.sum()), **metadata)


def raw_objects(dataset, name, prob, out):
    """Reuse 6a component generation/eligibility; B for building data is XOR."""
    pred = prob > .5
    with np.load(sam3_path(dataset, name, out), allow_pickle=False) as z:
        a, b = z['cls_t1'], z['cls_t2']
        if a.shape != prob.shape or b.shape != prob.shape:
            raise ValueError(f'{dataset}/{name}: SAM3/probability shape mismatch')
        binary = ((a == 5) != (b == 5)) if dataset in ('levir', 'levir_val', 'whu') else (
            (a > 0) & (b > 0) & (a != b))
    objects = []

    def retain(source, mask):
        if mask.sum() >= e4.MIN_CAND and pred[mask].mean() <= e4.MAX_PRED_FRAC:
            objects.append(make_object(mask, id=len(objects), source=source))

    for source, binary_mask in (('B', binary), ('G0.05', prob > .05)):
        for mask, _ in components(binary_mask, prob):
            retain(source, mask)
    if 'H1' in SOURCES[dataset]:
        path = OUT / 'exp5' / dataset / 'anychange' / (Path(name).stem + '.npz')
        with np.load(path, allow_pickle=False) as z:
            if tuple(z['shape']) != prob.shape:
                raise ValueError(f'{path}: H1/probability shape mismatch')
            for packed in z['h1_masks']:
                retain('H1', np.unpackbits(packed, count=prob.size).reshape(prob.shape).astype(bool))
    return objects


def pool_index(dataset, images):
    path = POOL / dataset / 'pieces.jsonl'
    if not path.is_file():
        return None
    return path, legacy.index_jsonl(path, set(images))


def old_pool_units(dataset, name, shape, index):
    path, spans = index
    # Images with zero pieces are allowed only if the completed 6a summary says so.
    if name not in spans:
        summary = read_json(POOL / dataset / 'pieces_summary.json')
        row = next(r for r in summary['per_image'] if r['image'] == name)
        if row['pieces'] != 0:
            raise ValueError(f'{name}: missing piece records')
        return []
    masks = load_piece_masks(dataset, name)
    units = []
    for r in legacy.read_spans(path, spans[name]):
        source = r['subsource'] or r['source']
        if r['kind'] != 'recall' or source not in SOURCES[dataset]:
            continue
        box, local = masks[r['piece_id']]
        units.append(dict(bbox=box, mask=local, area=int(local.sum()),
                          sources=[source], members=[r['cand_id']], old_piece_id=r['piece_id']))
    return units


def full_masks(objects, shape):
    return [legacy.full_mask(o, shape) for o in objects]


def split_objects(objects, labels, shape, threshold=None):
    """Apply 7b's partition intersections and area cutoff, preserving parent IDs."""
    result = []
    for parent, obj in enumerate(objects):
        meta = {k: v for k, v in obj.items() if k not in ('mask', 'bbox', 'area')}
        meta['parent_object'] = parent
        if threshold is not None and obj['area'] <= threshold:
            result.append({**obj, 'parent_object': parent})
            continue
        x0, y0, x1, y1 = obj['bbox']
        local_labels = labels[y0:y1, x0:x1]
        for label in np.unique(local_labels[obj['mask']]):
            if label == 0:
                continue
            local = obj['mask'] & (local_labels == label)
            if local.sum() >= e4.MIN_CAND:
                full = np.zeros(shape, bool)
                full[y0:y1, x0:x1] = local
                result.append(make_object(full, **meta))
    return result


def save_units(path, objects, shape):
    packed = [np.packbits(o['mask'].ravel()) for o in objects]
    save_npz(path, image_shape=np.asarray(shape, np.int32),
             bboxes=np.asarray([o['bbox'] for o in objects], np.int32).reshape(-1, 4),
             offsets=np.concatenate(([0], np.cumsum([len(x) for x in packed], dtype=np.int64))),
             packed=np.concatenate(packed) if packed else np.empty(0, np.uint8))


def load_units(path):
    with np.load(path, allow_pickle=False) as z:
        shape = tuple(z['image_shape'])
        objects = []
        for i, box in enumerate(z['bboxes']):
            x0, y0, x1, y1 = box
            local = np.unpackbits(z['packed'][z['offsets'][i]:z['offsets'][i+1]],
                                  count=(y1-y0)*(x1-x0)).reshape(y1-y0, x1-x0).astype(bool)
            objects.append(dict(bbox=box.tolist(), mask=local, area=int(local.sum())))
    return objects, shape


def geometry_key(dataset):
    return {'second': 'second_cv', 'levir': 'levir_val'}.get(dataset, dataset)


def load_geometry(dataset, out, allow_partial=False):
    path = Path(out) / 'geometry' / f'{geometry_key(dataset)}.json'
    row = read_json(path)
    if row['test_gt_used'] or (row['partial'] and not allow_partial) or row['dataset'] != geometry_key(dataset):
        raise ValueError(f'{path}: require completed non-test geometry')
    if row['median_area'] < e4.MIN_TARGET or not np.isfinite(row['median_area']):
        raise ValueError(f'{path}: invalid target median')
    return row
