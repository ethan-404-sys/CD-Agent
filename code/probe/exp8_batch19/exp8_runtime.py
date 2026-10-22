"""Process-local SYSU adapters for the unchanged batch18 runtime."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import exp8_data as data

BATCH18 = Path(__file__).resolve().parent.parent / 'batch18'


def batch18_module(stem):
    spec = importlib.util.spec_from_file_location(f'_batch18_{stem}', BATCH18 / f'{stem}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r = batch18_module('exp8_runtime')
data.register(r.common)
r.DETECTOR['sysu_val'] = 'schanger_small'
r.DATASETS = ('sysu_val', 'sysu')
r.EXPECTED['sysu_val'] = 4000
r.SOURCES['sysu_val'] = ('B', 'G0.05')
r.DEFAULT_OUT = data.DEFAULT_OUT
r.REVISION = 'batch19-v2'


def image_names(dataset):
    if dataset == 'sysu_val':
        return data.image_names()
    if dataset != 'sysu':
        raise ValueError('batch19 only supports sysu_val/sysu')
    names = r.common.names(dataset)
    if len(names) != 4000:
        raise ValueError('SYSU test requires 4,000 images')
    return names


def prediction_dir(dataset, out):
    root = Path(out) / 'preds' if dataset == 'sysu_val' else r.common.PRED
    return root / dataset / r.DETECTOR[dataset]


def sam3_path(dataset, name, out):
    if dataset == 'sysu_val':
        return data.sam3_path(name, out)
    return data.OUT / 'exp4/sysu/sam3' / (Path(name).stem + '.npz')


def automatic_path(dataset, name, out):
    root = data.OUT / 'exp8_batch18/automatic/sysu' if dataset == 'sysu' else Path(out) / 'automatic/sysu_val'
    return root / (Path(name).stem + '.npz')


def geometry_key(dataset):
    if dataset not in r.DATASETS:
        raise ValueError(dataset)
    return 'sysu_val'


def load_scale(out, kind, allow_partial=False):
    if kind == 'slic':
        return dict(median_area=data.U0_MEDIAN, method='historical_sysu_6a',
                    source='exp6a_pieces.MEDIAN_AREA[sysu]', partial=False,
                    historical_test_derived=True, current_test_labels_read=False)
    if kind != 'u5':
        raise ValueError(kind)
    path = Path(out) / 'geometry/sysu_val.json'
    row = r.read_json(path)
    scope = 'pool_val'
    expected = data.image_names(scope=scope)
    if (row['test_gt_used'] or row['scope'] != scope or row['dataset'] != 'sysu_val'
            or row['kind'] != kind or row['split_source'] != str(data.SPLIT)
            or row['method'] != 'no_signal_missed_gt' or row['detector'] != 'schanger_small'
            or row['partial'] and not allow_partial
            or row['images'] != (expected[:len(row['images'])] if row['partial'] else expected)):
        raise ValueError(f'{path}: incompatible split or incomplete geometry')
    if not r.np.isfinite(row['median_area']) or row['median_area'] < r.e4.MIN_TARGET:
        raise ValueError(f'{path}: invalid median')
    return row


def load_geometry(dataset, out, allow_partial=False):
    geometry_key(dataset)
    return load_scale(out, 'u5', allow_partial)


# Existing helpers resolve these globals at call time. Only this process is changed.
for name in ('image_names', 'prediction_dir', 'sam3_path', 'automatic_path',
             'geometry_key', 'load_geometry'):
    setattr(r, name, globals()[name])


def __getattr__(name):
    return getattr(r, name)
