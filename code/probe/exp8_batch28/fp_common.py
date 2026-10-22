"""batch28 的路径、逐图单元缓存和 6b 特征适配。"""
from __future__ import annotations

import importlib.util
import os
import sys
from functools import lru_cache
from pathlib import Path

sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
STAGE = Path(__file__).resolve().parent
WORKSPACE = STAGE.parents[2]
PROBE = WORKSPACE / 'Chang-Agent/probe'
sys.path[:0] = [str(STAGE.parent / 'batch18'), str(STAGE.parent / 'batch16'), str(PROBE)]
import metrics_cd as metrics
import exp7_units  # 固定使用 staging/batch16 的划分实现。
import exp8_runtime as rt
import exp6b_common as old
import exp6_pool_io as pool_io
from exp6b_train import weighted_bce
import numpy as np
from scipy import ndimage
from PIL import Image

spec = importlib.util.spec_from_file_location('batch28_sysu_data', STAGE.parent / 'batch19/exp8_data.py')
sysu_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sysu_data)
rt.common.DATASETS['sysu_val'] = {**rt.common.DATASETS['sysu'], 'prefix': 'train_'}

OUT = rt.OUT
DEFAULT_OUT = OUT / 'exp8_batch28'
DETECTOR = dict(second='mambascd_base', sysu='schanger_small', levir='schanger_base', whu='schanger_base')
EXPECTED = dict(second=dict(train=2671, val=297, test=1694),
                sysu=dict(train=3200, val=800, test=4000), levir=dict(val=64, test=128), whu=dict(test=690))
UNITS = ('slic', 'object')
FEATURES = ['det_mean', 'det_max', 'det_min', 'det_p90', 'log_area'] + [
    f'source_{s}' for s in old.SOURCES] + ['unit_mean_probability']
LIMITATIONS = {
    'second': '训练及验证使用交叉拟合检测器，测试使用官方 MambaSCD-Base；概率可能偏移。SLIC 沿用历史 test-derived 298 px 尺度。',
    'sysu': sysu_data.LIMITATION + ' SLIC 沿用历史 test-derived 972 px 尺度。',
    'levir': '无训练池，仅检测器概率规则；官方 val 64 张选择阈值。',
    'whu': '无训练及验证集；两种单元分别迁移 LEVIR val 的阈值。两者均为 SChanger-base 建筑变化，迁移有效性未知。',
}
read_json, write_json = rt.read_json, rt.write_json


def fixed(path, value):
    if path.exists():
        if read_json(path) != value:
            raise ValueError(f'{path}: 配置已改变，请使用新的输出目录')
    else:
        write_json(path, value)


def arguments(parser):
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--limit', type=int, default=0, help='仅冒烟；测试阶段禁止截取')


def protocol(args):
    if args.limit < 0 or (args.limit and args.out.resolve() == DEFAULT_OUT.resolve()):
        raise ValueError('冒烟必须使用独立输出目录，limit 必须非负')
    return dict(revision=1, limit=args.limit, partial=bool(args.limit), features=FEATURES,
                units=list(UNITS), object_min_area=10, oracle_min_area=10, oracle_gt_fraction_lt=.1,
                positive_gt_fraction_ge=.5, negative_gt_fraction_lt=.1,
                prediction='saved_0.5_mask', connectivity=8,
                selection=['pixel_f1', 'pixel_iou'], deletion='score < threshold',
                mlp_training_datasets=['second', 'sysu'], mlp_unit='object',
                limitations=LIMITATIONS)


def initialize(args):
    fixed(args.out / 'protocol.json', protocol(args))


def raw_dataset(dataset, split):
    if split == 'test':
        return dataset
    return dict(second='second_cv', sysu='sysu_val', levir='levir_val')[dataset]


def names(dataset, split, limit=0):
    if split not in EXPECTED[dataset]:
        raise ValueError(f'{dataset} 没有 {split} 划分')
    if dataset == 'second' and split != 'test':
        images = old.verifier_split()[0 if split == 'train' else 1]
    elif dataset == 'sysu' and split != 'test':
        images = sysu_data.image_names(scope='pool_' + split)
    else:
        images = rt.common.names(raw_dataset(dataset, split))
    if len(images) != EXPECTED[dataset][split] or len(set(images)) != len(images):
        raise ValueError(f'{dataset}/{split}: 图像数量不符')
    return images[:limit or None]


def inputs(dataset, split, name):
    raw = raw_dataset(dataset, split)
    root = OUT / 'preds'
    if raw == 'sysu_val':
        root = OUT / 'exp8_batch19/preds'
    elif raw == 'levir_val':
        root = OUT / 'exp8_batch18/preds'
    root = root / raw / DETECTOR[dataset]
    prob = np.load(root / 'prob' / (Path(name).stem + '.npy'), allow_pickle=False).astype(np.float32)
    with Image.open(root / 'mask' / name) as image:
        base = np.asarray(image) > 0
    gt = rt.common.load_label(raw, name)
    if base.shape != gt.shape or base.shape != prob.shape or not np.isfinite(prob).all():
        raise ValueError(f'{raw}/{name}: 预测、概率或标签不符')
    return base, gt, prob


def automatic(dataset, split, name):
    if dataset == 'second':
        root = OUT / ('exp8_batch20/automatic/second_cv_train' if split == 'train' else
                      'exp7_batch16/automatic/' + raw_dataset(dataset, split))
    elif dataset == 'sysu' and split != 'test':
        root = OUT / 'exp8_batch19/automatic/sysu_val'
    else:
        root = OUT / 'exp8_batch18/automatic' / raw_dataset(dataset, split)
    return root / (Path(name).stem + '.npz')


@lru_cache(maxsize=None)
def pool_index(dataset, split):
    raw = raw_dataset(dataset, split)
    root = OUT / ('exp8_batch19/exp6/pool' if raw == 'sysu_val' else 'exp6/pool')
    path = root / raw / 'pieces.jsonl'
    summary = read_json(root / raw / 'pieces_summary.json')
    counts = {r['image']: r['pieces'] for r in summary['per_image']}
    spans = rt.legacy.index_jsonl(path)
    return root, path, counts, spans


def slic_units(dataset, split, name, base):
    # 唯一无现成 pred 池的 LEVIR val，使用 LEVIR 已冻结的训练尺度。
    if dataset == 'levir' and split == 'val':
        median, _ = rt.geometry('levir')
        labels, _ = ndimage.label(base, structure=np.ones((3, 3), bool))
        segments = None
        result = []
        for k, sl in enumerate(ndimage.find_objects(labels), 1):
            region = labels[sl] == k
            if int(region.sum()) > 4 * median:
                if segments is None:
                    rgb = np.concatenate([rt.common.load_rgb('levir_val', name, v) for v in ('t1', 't2')], 2)
                    segments = rt.slic(rgb, n_segments=max(4, int(base.size / median)),
                                       compactness=10, channel_axis=-1, start_label=1)
                pieces = (region & (segments[sl] == i) for i in np.unique(segments[sl][region]))
            else:
                pieces = (region,)
            for piece in pieces:
                full = np.zeros_like(base)
                full[sl] = piece
                result.append(rt.make_object(full))
        return result
    root, path, counts, spans = pool_index(dataset, split)
    if name not in counts:
        raise ValueError(f'{path}: 缺少图像 {name}')
    rows = list(rt.legacy.read_spans(path, spans.get(name, [])))
    if len(rows) != counts[name]:
        raise ValueError(f'{path}: {name} 记录数不符')
    pool_io.POOL = root  # 只修改进程内读路径。
    masks = pool_io.load_piece_masks(raw_dataset(dataset, split), name)
    result = []
    for row in rows:
        if row['kind'] == 'pred':
            box, mask = masks[row['piece_id']]
            result.append(dict(bbox=box, mask=mask, area=int(mask.sum())))
    return result


def object_units(dataset, split, name, base):
    with np.load(automatic(dataset, split, name), allow_pickle=False) as z:
        labels = z['labels']
    if labels.shape != base.shape:
        raise ValueError(f'{name}: 自动划分尺寸不符')
    # 预测连通域互斥，交集也互斥；U4 的 disjoint_units 无需再次展开全图掩码。
    # 相交和 >=10 px 截断直接复用 batch18；排序与 U4 一致。
    components, _ = ndimage.label(base, structure=np.ones((3, 3), bool))
    result = []
    for k, sl in enumerate(ndimage.find_objects(components), 1):
        region = components[sl] == k
        obj = dict(bbox=[sl[1].start, sl[0].start, sl[1].stop, sl[0].stop],
                   mask=region, area=int(region.sum()), sources=['pred'])
        result.extend(rt.split_objects([obj], labels, base.shape))
    return sorted(result, key=lambda obj: obj['area'])


def cache_root(args, dataset, split, unit):
    return args.out / 'units' / dataset / split / unit


def prepare_image(args, dataset, split, unit, name, raw=None):
    root = cache_root(args, dataset, split, unit)
    stem = Path(name).stem
    done = root / 'images' / (stem + '.json')
    if done.exists():
        row = read_json(done)
        if row['image'] != name or row['protocol'] != protocol(args):
            raise ValueError(f'{done}: 缓存协议不符')
        for folder in ('masks', 'features'):
            if not (root / folder / (stem + '.npz')).is_file():
                raise FileNotFoundError(f'{done}: 缓存文件缺失')
        return
    base, gt, prob = inputs(dataset, split, name) if raw is None else raw
    objects = (slic_units if unit == 'slic' else object_units)(dataset, split, name, base)
    meta = np.zeros((len(objects), 12), np.float32)
    area = np.zeros(len(objects), np.int64)
    truth = np.zeros(len(objects), np.int64)
    occupied = np.zeros_like(base)
    for i, obj in enumerate(objects):
        x0, y0, x1, y1 = obj['bbox']
        mask = obj['mask']
        view = occupied[y0:y1, x0:x1]
        if (view & mask).any() or (mask & ~base[y0:y1, x0:x1]).any():
            raise ValueError(f'{name}/{unit}: 单元重叠或超出原预测')
        view |= mask
        values = prob[y0:y1, x0:x1][mask]
        area[i], truth[i] = int(mask.sum()), int(gt[y0:y1, x0:x1][mask].sum())
        meta[i, :5] = [values.mean(), values.max(), values.min(), np.quantile(values, .9), np.log(area[i])]
        meta[i, 10] = 1  # 6b 六来源位中的 pred。
        meta[i, 11] = values.mean()  # 按任务要求额外增加的单元平均概率。
    if unit == 'slic' and not np.array_equal(occupied, base):
        raise ValueError(f'{name}: pred 切块未完整覆盖原预测')
    fraction = truth / np.maximum(area, 1)
    label = np.where(fraction >= .5, 1, np.where(fraction < .1, 0, -1)).astype(np.int8)
    rt.save_units(root / 'masks' / (stem + '.npz'), objects, base.shape)
    old.save_npz(root / 'features' / (stem + '.npz'), meta=meta, area=area, truth=truth, label=label)
    write_json(done, dict(image=name, protocol=protocol(args), units=len(objects),
                         uncovered_prediction_pixels=int((base & ~occupied).sum())))


def read_image(args, dataset, split, unit, name):
    root = cache_root(args, dataset, split, unit)
    row = read_json(root / 'images' / (Path(name).stem + '.json'))
    if row['protocol'] != protocol(args) or row['image'] != name:
        raise ValueError(f'{name}: 缓存协议不符')
    with np.load(root / 'features' / (Path(name).stem + '.npz'), allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    return arrays


def remove(args, dataset, split, unit, name, base, selected):
    path = cache_root(args, dataset, split, unit) / 'masks' / (Path(name).stem + '.npz')
    objects, shape = rt.load_units(path)
    if shape != base.shape or len(objects) != len(selected):
        raise ValueError(f'{path}: 掩码和分数不符')
    final = base.copy()
    for obj, delete in zip(objects, selected):
        if delete:
            x0, y0, x1, y1 = obj['bbox']
            final[y0:y1, x0:x1][obj['mask']] = False
    return final


class Pool:
    def __init__(self, args, dataset, split, unit='object'):
        self.images = names(dataset, split, args.limit)
        self.slices = {}
        arrays = {k: [] for k in ('meta', 'label', 'area', 'truth')}
        start = 0
        for name in self.images:
            row = read_image(args, dataset, split, unit, name)
            count = len(row['label'])
            self.slices[name] = slice(start, start + count)
            start += count
            for key in arrays:
                arrays[key].append(row[key])
        for key, parts in arrays.items():
            setattr(self, key, np.concatenate(parts))
        if not np.isfinite(self.meta).all():
            raise ValueError('存在非有限特征')

    def __len__(self):
        return len(self.label)


class Features:
    input_dim = len(FEATURES)

    def __init__(self, pool):
        self.pool = pool

    def batches(self, selected, batch_size, rng=None):
        idx = np.flatnonzero(selected)
        if rng is not None:
            rng.shuffle(idx)
        for start in range(0, len(idx), batch_size):
            take = idx[start:start + batch_size]
            yield take, self.pool.meta[take].copy()


def require_reference(args):
    row = read_json(args.out / 'reference' / 'verification.json')
    if row['protocol'] != protocol(args) or not row['passed']:
        raise ValueError('历史复核未通过')
    if not args.limit and row['partial']:
        raise ValueError('正式流程不能使用局部历史复核')
    return row


def schemes(dataset):
    return ['baseline', 'slic_detector', 'object_detector'] + (['object_mlp'] if dataset in ('second', 'sysu') else [])


def pixel(tp, fp, fn):
    return dict(f1=metrics.ratio(2 * tp, 2 * tp + fp + fn), iou=metrics.ratio(tp, tp + fp + fn))
