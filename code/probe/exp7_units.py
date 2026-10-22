"""7b oracle units: CPU U0/U1/U2/U1low/U3; U4 uses ViT-H automatic masks.

Reuses exp6a candidates, SLIC implementation and filtering constants. The
original split loop is nested in exp6a.process, so its short parameterized
geometry rule is reproduced here without writing into the 6a pool.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from common import load_label, load_pred, load_prob, load_rgb
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import Pool, SOURCES
from exp6a_pieces import candidates, slic, targets, e4
from exp7_common import ROOT, image_names, save_raw, write_json
from metrics_cd import aggregate, distribution, image_metrics, ratio

UNITS = ('U0', 'U1', 'U2', 'U1low', 'U3', 'U4')
GEOMETRY = {'U1': (150, 10), 'U2': (600, 10), 'U1low': (298, 1)}
UNIT_RULES = [
    '候选来源固定B、G0.05、H1，沿用6a面积与原预测占比过滤；U0直接读取6a补漏池。',
    'SLIC复用exp6a_pieces.slic，六通道RGB，n_segments=max(4,int(H*W/目标面积))，仅切分面积>4×目标面积的候选；小于MIN_CAND的交集丢弃。',
    '150/600是SLIC请求面积，候选相交与去重后中位数不保证等于请求值；逐图和总体都报告实得面积，不在测试集调参。',
    'U1low目标面积298、compactness=1；U1/U2 compactness=10；start_label=1。',
    '重叠在oracle判定前去重：按单元面积升序、原始顺序排序，先到者占用像素；后到者减去占用区；空单元删除，剩余形状保留为一个单元；残余<MIN_CAND也保留以免改变候选覆盖面。',
    'U0去重后会改变部分切块几何；另报U0_pool参考行，按原池标签取并集以核对现有oracle，不把两者混为同一口径。',
    'U4用ViT-H默认自动掩码参数，t1/t2各一次；每期按predicted_iou降序、面积升序、原始顺序分配重叠像素，二期标签对的共同细分为单元；两期均无掩码的像素排除，然后与候选求交并按共同规则去重。',
    '无信号目标复用6a.targets：真值目标按语义转移拆分，面积>=MIN_TARGET、概率>0.5预测覆盖<10%；覆盖按6a.covers的交集占比与最大框面积比。另报无信号真值像素覆盖率。',
    'V1重训未做。',
]


def disjoint_units(masks):
    if not masks:
        return []
    occupied = np.zeros_like(masks[0])
    result = []
    for i in sorted(range(len(masks)), key=lambda j: (int(masks[j].sum()), j)):
        remaining = masks[i] & ~occupied
        if remaining.any():
            result.append(remaining)
            occupied |= remaining
    return result


def period_partition(records, shape):
    labels = np.zeros(shape, np.int32)
    order = sorted(range(len(records)), key=lambda i: (-float(records[i]['predicted_iou']),
                                                      int(records[i]['area']), i))
    for i in order:
        labels[records[i]['segmentation'] & (labels == 0)] = i + 1
    return labels


def temporal_partition(first, second):
    pairs = np.stack([first.ravel(), second.ravel()], axis=1)
    _, inverse = np.unique(pairs, axis=0, return_inverse=True)
    labels = inverse.reshape(first.shape) + 1
    labels[(first == 0) & (second == 0)] = 0
    return labels


def pool_units(pool, name, shape):
    masks = load_piece_masks(pool.dataset, name)
    result = []
    sl = pool.slices[name]
    for i in range(sl.start, sl.stop):
        if SOURCES[pool.source[i]] not in ('B', 'G0.05', 'H1'):
            continue
        (x0, y0, x1, y1), local = masks[int(pool.piece_id[i])]
        mask = np.zeros(shape, bool)
        mask[y0:y1, x0:x1] = local
        result.append(mask)
    return result


def raw_candidates(dataset, name, prob):
    prediction = prob > 0.5
    return [mask for source, subsource, mask, _ in candidates(dataset, name, prob, prediction)
            if (subsource or source) in ('B', 'G0.05', 'H1')
            and mask.sum() >= e4.MIN_CAND and prediction[mask].mean() <= e4.MAX_PRED_FRAC]


def oracle(units, gt, base):
    added = np.zeros_like(base)
    for mask in units:
        if 2 * int((gt & mask).sum()) >= int(mask.sum()):
            added |= mask
    return base | added


def coverage(units, target_data):
    labels, areas, boxes = target_data
    count = len(areas) - 1
    hit = np.zeros(len(areas), bool)
    union = np.zeros_like(labels, bool)
    for mask in units:
        overlap = np.bincount(labels[mask], minlength=len(areas))
        hit |= (overlap >= e4.COVER_FRAC * areas) & (int(mask.sum()) <= e4.MAX_AREA_RATIO * boxes)
        union |= mask
    return dict(targets=count, targets_covered=int(hit[1:].sum()),
                pixels=int((labels > 0).sum()), pixels_covered=int((union & (labels > 0)).sum()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=('second_cv', 'second', 'sysu', 'levir', 'whu'), required=True)
    parser.add_argument('--units', nargs='+', choices=UNITS, default=list(UNITS[:-1]))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--auto-cache', type=Path, default=ROOT / 'automatic')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    images = image_names(args.dataset)
    if args.plan:
        print(json.dumps(dict(images=len(images), unit='U4', seconds_per_date_image=10,
                              estimated_gpu_hours=len(images)*20/3600,
                              stop_and_report=len(images)*20/3600 > 12), indent=2))
        return
    if (args.out / 'summary.json').exists():
        raise FileExistsError(args.out / 'summary.json')
    if len(set(args.units)) != len(args.units):
        parser.error('Duplicate units')
    args.out.mkdir(parents=True, exist_ok=True)
    pool = Pool(args.dataset) if 'U0' in args.units else None
    generator = None
    if 'U4' in args.units:
        import torch
        from torchange.models.segment_any_change.segment_anything import SamAutomaticMaskGenerator, sam_model_registry
        from exp7_refine import SAM_CKPT
        model = sam_model_registry['vit_h'](checkpoint=SAM_CKPT).to('cuda').eval()
        generator = SamAutomaticMaskGenerator(model)
        (args.auto_cache / args.dataset).mkdir(parents=True, exist_ok=True)
    keys = list(args.units) + (['U0_pool'] if pool is not None else [])
    results, areas, coverage_rows = ({k: [] for k in keys} for _ in range(3))
    started = time.monotonic()
    with (args.out / 'per_image.jsonl').open('w') as handle:
        for number, name in enumerate(images, 1):
            prob = load_prob(args.dataset, DETECTOR[args.dataset], name)
            base = load_pred(args.dataset, DETECTOR[args.dataset], name)
            gt = load_label(args.dataset, name)
            target_data = targets(args.dataset, name, gt, prob > 0.5)
            raw_start = time.perf_counter()
            raw = raw_candidates(args.dataset, name, prob) if any(u != 'U0' for u in args.units) else []
            raw_seconds = time.perf_counter() - raw_start
            rgb = None
            if any(u in GEOMETRY or u == 'U4' for u in args.units):
                rgb = [load_rgb(args.dataset, name, v) for v in ('t1', 't2')]
            for unit in args.units:
                start = time.perf_counter()
                gpu_seconds = 0.0
                if unit == 'U0':
                    units = pool_units(pool, name, base.shape)
                    original = list(units)
                elif unit == 'U3':
                    units = raw
                else:
                    if unit in GEOMETRY:
                        target_area, compactness = GEOMETRY[unit]
                        labels = slic(np.concatenate(rgb, axis=2),
                                      n_segments=max(4, int(prob.size/target_area)),
                                      compactness=compactness, channel_axis=-1, start_label=1)
                    else:
                        path = args.auto_cache / args.dataset / (Path(name).stem + '.npz')
                        if path.exists():
                            with np.load(path, allow_pickle=False) as z:
                                labels, gpu_seconds = z['labels'], float(z['gpu_seconds'])
                        else:
                            torch.cuda.synchronize()
                            gpu_start = time.perf_counter()
                            with torch.inference_mode():
                                parts = [period_partition(generator.generate(im), base.shape) for im in rgb]
                            torch.cuda.synchronize()
                            gpu_seconds = time.perf_counter()-gpu_start
                            labels = temporal_partition(*parts)
                            save_raw(path, dict(labels=labels, gpu_seconds=np.asarray(gpu_seconds)))
                        # Cached and fresh paths count original GPU time exactly once.
                        start = time.perf_counter()
                    units = []
                    for candidate in raw:
                        if unit in GEOMETRY and candidate.sum() <= 4*GEOMETRY[unit][0]:
                            units.append(candidate)
                            continue
                        for label in np.unique(labels[candidate]):
                            if label == 0:
                                continue
                            part = candidate & (labels == label)
                            if part.sum() >= e4.MIN_CAND:
                                units.append(part)
                units = disjoint_units(units)
                construction_seconds = time.perf_counter() - start + gpu_seconds + (0 if unit == 'U0' else raw_seconds)
                variants = [(unit, units)] + ([('U0_pool', original)] if unit == 'U0' else [])
                for key, current in variants:
                    infer_start = time.perf_counter()
                    final = oracle(current, gt, base)
                    seconds = construction_seconds + time.perf_counter()-infer_start
                    record = image_metrics(final, gt, before=base)
                    cov = coverage(current, target_data)
                    sizes = [int(m.sum()) for m in current]
                    record.update(image=name, unit=key, unit_count=len(current),
                                  area=distribution(sizes), inference_seconds=seconds,
                                  gpu_seconds=gpu_seconds, no_signal=cov)
                    results[key].append(record)
                    areas[key].extend(sizes)
                    coverage_rows[key].append(cov)
                    handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
            if number % 20 == 0:
                print(f'{number}/{len(images)} {time.monotonic()-started:.1f}s', flush=True)
    rows = {}
    for key in keys:
        cov = {k: sum(c[k] for c in coverage_rows[key]) for k in ('targets', 'targets_covered', 'pixels', 'pixels_covered')}
        cov.update(object_coverage=ratio(cov['targets_covered'], cov['targets'], None),
                   pixel_coverage=ratio(cov['pixels_covered'], cov['pixels'], None))
        rows[key] = dict(**aggregate(results[key]), no_signal=cov, unit_area=distribution(areas[key]),
                         unit_count=distribution(r['unit_count'] for r in results[key]),
                         inference_seconds=distribution(r['inference_seconds'] for r in results[key]))
    write_json(args.out / 'summary.json', dict(dataset=args.dataset, images=images, rows=rows,
                                              rules=UNIT_RULES, seconds=time.monotonic()-started))


if __name__ == '__main__':
    main()
