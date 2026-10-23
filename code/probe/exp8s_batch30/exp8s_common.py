"""历史读取接口、D1 适配和新增像素对象的公共操作。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
STAGING = WORKSPACE / 'wyh/exp6_staging'
sys.path.insert(0, str(STAGING / 'batch18'))
import exp8_runtime as rt
sys.path.insert(0, str(STAGING / 'batch19'))
_spec = importlib.util.spec_from_file_location('_exp8s_sysu', STAGING / 'batch19/exp8_runtime.py')
sysu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sysu)
import numpy as np
from objects import gt_label
from exp8_dedup_viz import groups_for, union_object, overlap

ROOT = rt.OUT
B18, B19 = ROOT / 'exp8_batch18', ROOT / 'exp8_batch19'
DATASETS = ('second_cv', 'second', 'sysu_val', 'sysu', 'levir', 'whu')
LEVELS = {'G': (.05, .1, .2, .3, .4), 'B': (0, 50, 100, 200, 500),
          'H1': (None, 1, 3, 5, 10, 20), 'C': (0, 50, 100, 200, 500)}
DEFAULTS = {s: values[0] for s, values in LEVELS.items()}
SOURCES = ('B', 'G', 'H1', 'C')
VERSION = 1


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp.json')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def runtime(dataset):
    return sysu if dataset in ('sysu_val', 'sysu') else rt


def upstream(dataset):
    return B19 if dataset in ('sysu_val', 'sysu') else B18


def names(dataset):
    if dataset == 'sysu_val':
        frozen = read_json(B19 / 'frozen/sysu_val.json')
        result = frozen['validation_images']
        if frozen['partial'] or len(result) != 800 or result != sysu.data.image_names(scope='pool_val'):
            raise ValueError('SYSU frozen validation must equal the 800 pool_val images')
        return result
    return rt.image_names(dataset)


def sam_path(dataset, name):
    return runtime(dataset).sam3_path(dataset, name, upstream(dataset))


def h1_path(dataset, name):
    return ROOT / 'exp5' / dataset / 'anychange' / (Path(name).stem + '.npz')


def u5_path(dataset, name):
    return upstream(dataset) / 'pool' / dataset / 'U5_x4' / (Path(name).stem + '.npz')


def level_key(source, level):
    return source + '_' + ('all' if level is None else str(level))


def full(obj, shape):
    return rt.legacy.full_mask(obj, shape)


def crop(mask, **metadata):
    return rt.make_object(mask, **metadata)


def put(union, obj):
    x0, y0, x1, y1 = obj['bbox']
    union[y0:y1, x0:x1] |= obj['mask']


class _Source(str):
    """只给旧 groups_for 的内部来源比较使用；其 NMS 查表仍可使用 'B'。"""
    def __new__(cls, identity):
        result = super().__new__(cls, 'B')
        result.identity = identity
        return result

    def __eq__(self, other):
        if isinstance(other, _Source):
            return self.identity == other.identity
        return str.__eq__(self, other)

    __hash__ = str.__hash__


def dedup(objects, internal=False):
    """调用原 D1。内部阶段将每个原候选视为独立标识；跨源阶段用来源名。"""
    tagged = [{**o, 'source': _Source(i if internal else o['source'])}
              for i, o in enumerate(objects)]
    groups = groups_for(tagged)['direct_pairs']
    result = []
    for ids in groups:
        obj = union_object(objects, ids)
        obj['sources'] = sorted({s for i in ids for s in objects[i].get('sources', [objects[i]['source']])})
        obj['source'] = objects[ids[0]]['source'] if internal else '+'.join(obj['sources'])
        obj['members'] = [m for i in ids for m in objects[i].get('members', [i])]
        obj['score'] = max(objects[i].get('score', 0.) for i in ids)
        result.append(obj)
    return result


def combined(by_source, selected, deduplicate=True):
    if not deduplicate:
        return [o for s in SOURCES if s in selected for o in by_source[s]]
    objects = [o for s in SOURCES if s in selected for o in dedup(by_source[s], internal=True)]
    return dedup(objects)


def target_objects(dataset, name, gt, base):
    """exp4 最小面积与语义划分；主对象是 FN 连通域，无信号保留原 GT 对象。"""
    gl, count, transitions = gt_label(dataset, name, gt)
    missed, no_signal = [], []
    for ident in range(1, count + 1):
        original = gl == ident
        area = int(original.sum())
        if area >= rt.e4.MIN_TARGET and float(base[original].mean()) < .1:
            no_signal.append(crop(original, parent=ident, transition=transitions.get(ident)))
        for mask, _ in rt.components(original & ~base, base):
            if mask.sum() >= rt.e4.MIN_TARGET:
                missed.append(crop(mask, parent=ident, transition=transitions.get(ident)))
    return missed, no_signal


def counts(pred, gt):
    return dict(tp=int((pred & gt).sum()), fp=int((pred & ~gt).sum()), fn=int((~pred & gt).sum()))


def scores(c):
    tp, fp, fn = (c[k] for k in ('tp', 'fp', 'fn'))
    return (2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.,
            tp/(tp+fp+fn) if tp+fp+fn else 0.)


def intersect_rows(obj, targets):
    return [[j, n] for j, target in enumerate(targets) if (n := overlap(obj, target))]


def annotate(obj, gt, prob, missed, no_signal):
    x0, y0, x1, y1 = obj['bbox']
    mask = obj['mask']
    a = int((gt[y0:y1, x0:x1] & mask).sum())
    return {**obj, 'a': a, 'b': obj['area']-a,
            'detector_mean': float(prob[y0:y1, x0:x1][mask].mean()),
            'intersections': intersect_rows(obj, missed),
            'no_signal_intersections': intersect_rows(obj, no_signal)}


def save_objects(path, objects, shape):
    path = Path(path)
    rt.save_units(path.with_name(path.name + '.npz'), objects, shape)
    write_json(path.with_name(path.name + '.json'), [{k: v for k, v in o.items() if k != 'mask'} for o in objects])


def load_objects(path):
    path = Path(path)
    objects, shape = rt.load_units(path.with_name(path.name + '.npz'))
    metadata = read_json(path.with_name(path.name + '.json'))
    if len(objects) != len(metadata):
        raise ValueError(f'{path}: mask/metadata length mismatch')
    return [{**o, **m} for o, m in zip(objects, metadata)], shape
