"""Cross-source deduplication controls on the saved SECOND/SYSU test candidates."""
from __future__ import annotations

import argparse
import colorsys
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from exp8_common import (OUT, SOURCES, DETECTOR, candidate_objects, distribution,
                         full_mask, index_jsonl, load_label, load_pred, load_rgb, metrics,
                         names, np, ndimage, parallel_map, read_spans, write_json)

METHODS = ('none', 'direct_pairs', 'transitive', 'nms_area')
RULES = dict(
    eligible='Different original sources only; IoU >= 0.5 OR intersection/min(area) >= 0.8.',
    direct_pairs='Sort eligible original pairs by IoU descending, then candidate IDs ascending; '
                 'accept only if both candidates are unused. Each candidate participates in at most one pair. '
                 'Union the pair; never compare a newly merged mask. Best available edge wins.',
    transitive='Union-find over all eligible original cross-source edges; union masks in each component.',
    nms_area='Original area descending, then source B/G0.05/H1, then manifest order. '
             'A kept original mask suppresses directly eligible remaining candidates; no mask union.',
    inflation='Object area / largest original constituent area. Also report per-image largest output '
              'area / largest input area, and total object area / total input object area (overlap counted).',
    multiple_gt='At least one pixel in each of >=2 binary GT 8-connected components; denominator all objects.',
    oracle='Whole-object GT fraction >=0.5, union accepted masks into the saved detector prediction.',
    selection='Fixed SECOND 759/518/900; unique additional images: largest per-image maximum-area ratio, '
              'largest absolute count decrease, nearest median rank on both quantities. '
              'SECOND 2 each; SYSU 2 inflation + 1 count decrease + 1 median. Ties by filename.',
    mask_identity='Original per-source raw-mask order and exp4/5 eligibility, checked against manifest count/box/area; '
                  'distinct masks with equal bboxes remain distinct; duplicate-bbox candidates counted.',
)


def overlap(a, b):
    ax0, ay0, ax1, ay1 = a['bbox']
    bx0, by0, bx1, by1 = b['bbox']
    x0, y0, x1, y1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    if x1 <= x0 or y1 <= y0:
        return 0
    return int((a['mask'][y0-ay0:y1-ay0, x0-ax0:x1-ax0] &
                b['mask'][y0-by0:y1-by0, x0-bx0:x1-bx0]).sum())


def groups_for(objects):
    edges, adjacent = [], [set() for _ in objects]
    for i, a in enumerate(objects):
        for j in range(i+1, len(objects)):
            b = objects[j]
            if a['source'] == b['source']:
                continue
            inter = overlap(a, b)
            iou = inter / (a['area'] + b['area'] - inter)
            if iou >= .5 or inter / min(a['area'], b['area']) >= .8:
                edges.append((-iou, i, j))
                adjacent[i].add(j)
                adjacent[j].add(i)
    edges.sort()
    used, direct = set(), []
    for _, i, j in edges:
        if i not in used and j not in used:
            direct.append([i, j])
            used.update((i, j))
    direct.extend([i] for i in range(len(objects)) if i not in used)
    parent = list(range(len(objects)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for _, i, j in edges:
        parent[find(j)] = find(i)
    components = {}
    for i in range(len(objects)):
        components.setdefault(find(i), []).append(i)
    nms, suppressed = [], set()
    priority = {s: i for i, s in enumerate(('B', 'G0.05', 'H1'))}
    for i in sorted(range(len(objects)), key=lambda k: (-objects[k]['area'], priority[objects[k]['source']], k)):
        if i in suppressed:
            continue
        nms.append([i])
        suppressed.update(adjacent[i])
    return dict(none=[[i] for i in range(len(objects))], direct_pairs=sorted(direct),
                transitive=sorted(components.values()), nms_area=nms)


def union_object(objects, ids):
    boxes = [objects[i]['bbox'] for i in ids]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    mask = np.zeros((y1-y0, x1-x0), bool)
    for i in ids:
        a, b, c, d = objects[i]['bbox']
        mask[b-y0:d-y0, a-x0:c-x0] |= objects[i]['mask']
    area = int(mask.sum())
    return dict(bbox=[x0, y0, x1, y1], mask=mask, area=area, members=ids,
                sources=sorted({objects[i]['source'] for i in ids}),
                inflation=area / max(objects[i]['area'] for i in ids))


def read_objects(job):
    dataset, name, path, spans = job
    rows = list(read_spans(path, spans))
    if len(rows) != 1 or rows[0]['image'] != name:
        raise ValueError(f'{path}: expected one candidate row for {name}')
    return candidate_objects(dataset, rows[0])


def evaluate(job):
    dataset, name, _, _ = job
    objects = read_objects(job)
    gt = load_label(dataset, name)
    base = load_pred(dataset, DETECTOR[dataset], name)
    gl, _ = ndimage.label(gt, np.ones((3, 3), bool))
    result = dict(image=name, input_objects=len(objects),
                  equal_bbox_candidates=sum(o['bbox_matches'] > 1 for o in objects), methods={})
    result['baseline'] = metrics.image_metrics(base, gt, before=base)
    result['baseline']['image'] = name
    raw_max = max((o['area'] for o in objects), default=0)
    raw_total = sum(o['area'] for o in objects)
    for method, groups in groups_for(objects).items():
        merged = base.copy()
        areas, inflation, multi, accepted = [], [], 0, 0
        purity_bins = [0, 0, 0]
        mixed_area = total_area = new_area = new_gt = 0
        for ids in groups:
            o = union_object(objects, ids)
            x0, y0, x1, y1 = o['bbox']
            mask = o['mask']
            truth = gt[y0:y1, x0:x1][mask]
            fraction = float(truth.mean())
            labels = np.unique(gl[y0:y1, x0:x1][mask])
            multi += int(np.count_nonzero(labels) >= 2)
            purity_bins[2 if fraction >= .5 else 0 if fraction < .1 else 1] += 1
            mixed_area += min(int(truth.sum()), o['area'] - int(truth.sum()))
            total_area += o['area']
            residual = mask & ~base[y0:y1, x0:x1]
            new_area += int(residual.sum())
            new_gt += int((residual & gt[y0:y1, x0:x1]).sum())
            if fraction >= .5:
                merged[y0:y1, x0:x1] |= mask
                accepted += 1
            areas.append(o['area'])
            inflation.append(o['inflation'])
        metric = metrics.image_metrics(merged, gt, before=base)
        metric['image'] = name
        largest = max(areas, default=0)
        result['methods'][method] = dict(
            objects=len(groups), max_area=largest, areas=distribution(areas),
            max_area_ratio=largest / raw_max if raw_max else None,
            total_area_ratio=sum(areas) / raw_total if raw_total else None,
            inflation=distribution(inflation), inflation_sum=sum(inflation),
            multi_gt_objects=multi, multi_gt_fraction=multi / len(groups) if groups else None,
            oracle_accepted=accepted, purity_counts=dict(negative=purity_bins[0], middle=purity_bins[1], positive=purity_bins[2]),
            mixed_area=mixed_area, total_object_area=total_area,
            residual_area=new_area, residual_gt=new_gt, metrics=metric)
    return result


def select_examples(rows, dataset):
    available = {r['image']: r for r in rows}
    selected = {}
    if dataset == 'second':
        for name in ('test_759.png', 'test_518.png', 'test_900.png'):
            if name in available:
                selected[name] = 'fixed'
    inflation_order = sorted(rows, key=lambda r: (-(r['methods']['transitive']['max_area_ratio'] or 0), r['image']))
    decrease_order = sorted(rows, key=lambda r: (-(r['input_objects']-r['methods']['transitive']['objects']), r['image']))
    ranks = [{r['image']: i for i, r in enumerate(order)} for order in (inflation_order, decrease_order)]
    middle = (len(rows)-1)/2
    median_order = sorted(rows, key=lambda r: (sum(abs(rank[r['image']]-middle) for rank in ranks), r['image']))
    for reason, order, count in (
        ('largest_area_inflation', inflation_order, 2),
        ('largest_count_decrease', decrease_order, 2 if dataset == 'second' else 1),
        ('median_joint_rank', median_order, 2 if dataset == 'second' else 1),
    ):
        remaining = [r for r in order if r['image'] not in selected]
        for row in remaining[:count]:
            selected[row['image']] = reason
    return selected


def draw(job, target, reason):
    os.environ.setdefault('MPLCONFIGDIR', str(target.parent / '.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    dataset, name, _, _ = job
    objects = read_objects(job)
    groups = groups_for(objects)
    gt = load_label(dataset, name)
    t1, t2 = (load_rgb(dataset, name, view) for view in ('t1', 't2'))
    fig, grid = plt.subplots(2, 4, figsize=(18, 10))
    axes = grid.ravel()
    gt_overlay = t2.copy()
    gt_overlay[metrics.boundary(gt > 0)] = (255, 0, 0)
    for ax, im, title in zip(axes[:4], (t1, t2, gt, gt_overlay), ('t1', 't2', 'GT', 'GT contour on t2')):
        ax.imshow(im, cmap='gray' if title == 'GT' else None)
        ax.set_title(title)
    labels = dict(none='no merge (raw candidates)', direct_pairs='direct pairs (no chaining)',
                  transitive='transitive (union-find)', nms_area='NMS (keep largest, no union)')
    details = {}
    for ax, method in zip(axes[4:], METHODS):
        overlay = t2.copy()
        details[method] = []
        for k, ids in enumerate(groups[method]):
            obj = union_object(objects, ids)
            edge = metrics.boundary(full_mask(obj, gt.shape))
            color = np.asarray(colorsys.hsv_to_rgb((k*.61803398875) % 1, .85, 1))*255
            overlay[edge] = color.astype(np.uint8)
            details[method].append({key: value for key, value in obj.items() if key != 'mask'})
        ax.imshow(overlay)
        ax.set_title(f'{labels.get(method, method)}: {len(groups[method])} objects')
    for ax in axes:
        ax.axis('off')
    fig.suptitle(f'{dataset} {name} ({reason}); merging only across sources; one colour per object', y=0.985)
    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.92, wspace=0.03, hspace=0.12)
    fig.savefig(target, dpi=150)
    plt.close(fig)
    write_json(target.with_suffix('.json'), dict(image=name, reason=reason, objects=details))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--datasets', nargs='+', choices=tuple(SOURCES), default=list(SOURCES))
    args = p.parse_args()
    if args.workers < 1:
        p.error('--workers must be positive')
    started = time.monotonic()
    for dataset in args.datasets:
        path = OUT / ('exp5' if dataset == 'second' else 'exp4') / dataset / 'candidates.jsonl'
        images = names(dataset)
        if not args.smoke and len(images) != {'second': 1694, 'sysu': 4000}[dataset]:
            raise ValueError(f'Incomplete {dataset} test split: {len(images)} images')
        if (args.out / dataset / 'summary.json').exists():
            raise FileExistsError(f'{args.out / dataset}: completed output exists; use a new --out')
        if args.smoke:
            fixed = ['test_759.png', 'test_518.png', 'test_900.png'] if dataset == 'second' else []
            images = sorted(set(images[:4] + fixed))
        index = index_jsonl(path, set(images))
        if set(index) != set(images):
            raise ValueError(f'{path}: candidate manifest does not cover requested test images')
        dest = args.out / dataset
        dest.mkdir(parents=True, exist_ok=True)
        jobs = {name: (dataset, name, path, index[name]) for name in images}
        rows = []
        for row in parallel_map(evaluate, jobs.values(), args.workers):
            rows.append(row)
            if len(rows) % 100 == 0:
                print(dataset, len(rows), '/', len(images), flush=True)
        baseline = metrics.aggregate([r['baseline'] for r in rows])
        summaries = {}
        for method in METHODS:
            entries = [r['methods'][method] for r in rows]
            aggregate = metrics.aggregate([e['metrics'] for e in entries])
            count = sum(e['objects'] for e in entries)
            total_area = sum(e['total_object_area'] for e in entries)
            residual_area = sum(e['residual_area'] for e in entries)
            summaries[method] = dict(
                images=len(rows), object_count=distribution(e['objects'] for e in entries),
                max_object_area=max(e['max_area'] for e in entries),
                per_image_max_object_area=distribution(e['max_area'] for e in entries),
                per_image_max_area_ratio=distribution(e['max_area_ratio'] for e in entries if e['max_area_ratio'] is not None),
                object_inflation_mean=sum(e['inflation_sum'] for e in entries)/count if count else None,
                object_inflation_max=max((e['inflation']['max'] for e in entries if e['objects']), default=None),
                multi_gt_object_fraction=sum(e['multi_gt_objects'] for e in entries)/count if count else None,
                area_weighted_impurity=sum(e['mixed_area'] for e in entries)/total_area if total_area else None,
                residual_gt_fraction=sum(e['residual_gt'] for e in entries)/residual_area if residual_area else None,
                oracle_delta_f1_pp=100*(aggregate['micro']['pixel_f1']-baseline['micro']['pixel_f1']),
                oracle_bf2=aggregate['micro']['bf2_f1'],
                oracle_delta_bf2_pp=100*(aggregate['micro']['bf2_f1']-baseline['micro']['bf2_f1']),
                evaluation=aggregate)
            write_json(dest / f'stats_{method}.json', dict(smoke=args.smoke, sources=SOURCES[dataset],
                                                         rules=RULES, baseline=baseline, **summaries[method]))
        selected = select_examples(rows, dataset)
        for name, reason in selected.items():
            draw(jobs[name], dest / f'{Path(name).stem}.png', reason)
        write_json(dest / 'per_image.json', rows)
        write_json(dest / 'summary.json', dict(smoke=args.smoke, scope='subset' if args.smoke else 'full_test',
                   sources=SOURCES[dataset], images=images, rules=RULES, selected=selected,
                   baseline=baseline, methods=summaries, seconds=time.monotonic()-started))


if __name__ == '__main__':
    main()
