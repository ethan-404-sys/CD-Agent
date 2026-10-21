"""Read-only adapters for exp8; never import a model or regenerate candidates."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import islice
from pathlib import Path

sys.dont_write_bytecode = True
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'Chang-Agent/probe'
sys.path.insert(0, str(PROBE))

import numpy as np
from scipy import ndimage
from common import OUT, DATASETS, load_label, load_pred, load_prob, load_rgb, names

# Resolve this exact file, independently of the caller's PYTHONPATH.
_spec = importlib.util.spec_from_file_location('exp8_metrics', PROBE / 'batch16/metrics_cd.py')
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)
DETECTOR = {'second': 'mambascd_base', 'sysu': 'schanger_small'}
SOURCES = {'second': ('B', 'G0.05', 'H1'), 'sysu': ('B', 'G0.05')}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def index_jsonl(path, wanted=None):
    """Store byte ranges per image, not millions of parsed piece records."""
    spans, previous = {}, None
    with Path(path).open('rb') as handle:
        while True:
            start = handle.tell()
            line = handle.readline()
            if not line:
                break
            row = json.loads(line)
            name = row['image']
            if wanted is None or name in wanted:
                if name == previous:
                    spans[name][-1][1] = handle.tell()
                else:
                    spans.setdefault(name, []).append([start, handle.tell()])
            previous = name
    return spans


def read_spans(path, spans):
    with Path(path).open('rb') as handle:
        for start, end in spans:
            handle.seek(start)
            while handle.tell() < end:
                yield json.loads(handle.readline())


def parallel_map(function, jobs, workers):
    """Bound queued inputs/results to two images per worker."""
    if workers < 1:
        raise ValueError('--workers must be positive')
    if workers == 1:
        yield from map(function, jobs)
        return
    with ProcessPoolExecutor(max_workers=workers) as executor:
        iterator = iter(jobs)
        while batch := list(islice(iterator, 2 * workers)):
            yield from executor.map(function, batch, chunksize=1)


def distribution(values):
    a = np.asarray(list(values), dtype=float)
    if not a.size:
        return dict(n=0, mean=None, p50=None, p90=None, max=None)
    return dict(n=int(a.size), mean=float(a.mean()), p50=float(np.quantile(a, .5)),
                p90=float(np.quantile(a, .9)), max=float(a.max()))


def score_counts(pred, gt):
    """Exact batch16 pixel/BF2 definitions, without unrelated expensive metrics."""
    c = metrics.pixel_counts(pred, gt)
    pb, gb = metrics.boundary(pred), metrics.boundary(gt)
    c.update(pred_boundary=int(pb.sum()), gt_boundary=int(gb.sum()))
    c['bf2_pred_matched'] = int((pb & (ndimage.distance_transform_edt(~gb) <= 2)).sum()) if gb.any() else 0
    c['bf2_gt_matched'] = int((gb & (ndimage.distance_transform_edt(~pb) <= 2)).sum()) if pb.any() else 0
    return c


def scores(c):
    p = metrics.ratio(c['bf2_pred_matched'], c['pred_boundary'])
    r = metrics.ratio(c['bf2_gt_matched'], c['gt_boundary'])
    return dict(f1=metrics.pixel_f1(c), bf2=metrics.ratio(2*p*r, p+r))


def add_counts(total, row):
    for key, value in row.items():
        total[key] = total.get(key, 0) + value


def crop_mask(mask):
    yy, xx = np.nonzero(mask)
    if not len(xx):
        raise ValueError('Empty candidate mask')
    box = [int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1]
    x0, y0, x1, y1 = box
    return box, mask[y0:y1, x0:x1].copy()


def candidate_objects(dataset, record):
    """Restore saved candidate identities in original per-source generation order.

    Replay only the original exp4/5 area/prediction-overlap eligibility to map
    the stored raw masks to the manifest. Require exact count/box/area agreement;
    never select the first bbox match when distinct raw masks share that box.
    Only local masks are retained. No SLIC or inference is performed.
    """
    name = record['image']
    selected = [c for c in record['candidates'] if c['source'] in SOURCES[dataset]]
    raw, multiplicities = {}, {}
    prob = load_prob(dataset, DETECTOR[dataset], name)
    pred = prob > .5

    def retain(source, box, local):
        area = int(local.sum())
        x0, y0, x1, y1 = box
        if area < 10 or float(pred[y0:y1, x0:x1][local].mean()) > .5:
            return
        raw.setdefault(source, []).append((list(box), local.copy(), area))
        key = source, tuple(box)
        multiplicities[key] = multiplicities.get(key, 0) + 1

    for source in SOURCES[dataset]:
        if not any(c['source'] == source for c in selected):
            continue
        if source in ('B', 'G0.05'):
            if source == 'B':
                with np.load(OUT / 'exp4' / dataset / 'sam3' / (Path(name).stem + '.npz')) as z:
                    binary = (z['cls_t1'] > 0) & (z['cls_t2'] > 0) & (z['cls_t1'] != z['cls_t2'])
            else:
                binary = prob > .05
            labels, _ = ndimage.label(binary, np.ones((3, 3), bool))
            for k, sl in enumerate(ndimage.find_objects(labels), 1):
                if sl is None:
                    continue
                box = (sl[1].start, sl[0].start, sl[1].stop, sl[0].stop)
                retain(source, box, labels[sl] == k)
        else:
            with np.load(OUT / 'exp5' / dataset / 'anychange' / (Path(name).stem + '.npz')) as z:
                shape = tuple(map(int, z['shape']))
                for packed in z['h1_masks']:
                    mask = np.unpackbits(packed, count=int(np.prod(shape))).reshape(shape).astype(bool)
                    if not mask.any():
                        continue
                    box, local = crop_mask(mask)
                    retain(source, box, local)
        expected = sum(c['source'] == source for c in selected)
        if len(raw.get(source, [])) != expected:
            raise ValueError(f'{dataset}/{name}/{source}: raw candidate count differs from saved manifest')
    result, cursor = [], {source: 0 for source in SOURCES[dataset]}
    for i, c in enumerate(selected):
        key = c['source'], tuple(c['bbox'])
        source = c['source']
        box, local, area = raw[source][cursor[source]]
        cursor[source] += 1
        if box != list(c['bbox']) or area != c['area']:
            raise ValueError(f'{dataset}/{name}/{i}: original candidate order/geometry differs from saved manifest')
        result.append(dict(id=i, source=c['source'], bbox=list(c['bbox']), mask=local,
                           area=int(local.sum()), bbox_matches=multiplicities[key]))
    return result


def full_mask(obj, shape):
    full = np.zeros(shape, bool)
    x0, y0, x1, y1 = obj['bbox']
    full[y0:y1, x0:x1] = obj['mask']
    return full
