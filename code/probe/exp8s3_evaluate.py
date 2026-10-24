"""Exact score fields, cumulative curves, validation freezes and frozen-point image shards."""
from exp8s3_common import *


def score_field(objects, ids, values, shape):
    field = np.full(shape, -np.inf, np.float64)
    for index, value in zip(ids, values):
        obj = objects[int(index)]
        x0, y0, x1, y1 = obj['bbox']
        view = field[y0:y1, x0:x1]
        view[obj['mask']] = np.maximum(view[obj['mask']], value)
    return field


def targets2(dataset, split, name, gt, base, cell):
    # One parent GT target per cell-2 component, deduplicated by target ID (batch39 convention).
    gl, _, _ = gt_label(raw_dataset(dataset, split), name, gt)
    ids = np.unique(gl[cell == 2])
    if np.any(ids == 0):
        raise ValueError('Cell 2 must belong to GT objects')
    slices = ndimage.find_objects(gl)
    targets = []
    for ident in ids:
        sl = slices[ident-1]
        mask = gl[sl] == ident
        targets.append((sl, mask, int(mask.sum()), int((mask & base[sl]).sum())))
    return targets


def histogram(field, gt, base, cell, targets):
    take = np.isfinite(field) & ~base
    values, inv = np.unique(field[take], return_inverse=True)
    truth, cells = gt[take], cell[take]
    rows = [np.bincount(inv, weights=w, minlength=len(values))
            for w in (truth, ~truth, cells == 2, cells == 1, cells == 3, cells == 5)]
    hist = np.column_stack([values, *rows, np.zeros(len(values))])
    events, baseline_hits = [], 0
    for sl, mask, area, inside in targets:
        needed = (area+1)//2-inside
        if needed <= 0:
            baseline_hits += 1
            continue
        scores = field[sl][mask & ~base[sl]]
        scores = scores[np.isfinite(scores)]
        if len(scores) >= needed:
            # The largest accepting threshold: the needed-th highest newly covered GT score.
            value = np.partition(scores, len(scores)-needed)[len(scores)-needed]
            events.append([value, 0, 0, 0, 0, 0, 0, 1])
    if events:
        hist = np.concatenate([hist, np.asarray(events)])
    return hist, baseline_hits


def curves(args):
    methods = manifest(args)['methods']
    for split in ('val', 'test') if args.split == 'all' else (args.split,):
        data = assemble(args, split)
        scores = {m: load_scores(args, m, split, data) for m in methods}
        lookup = {name: i for i, name in enumerate(names(args, split))}
        offsets = np.r_[0, np.cumsum(data['counts'])]
        for name in names(args, split, True):
            dest = args.out / args.dataset / 'histograms' / split / (Path(name).stem + '.npz')
            if dest.exists():
                raise FileExistsError(dest)
            part = archive(unit_path(args, split, name))
            objects, shape = pool_objects(args.dataset, split, name)
            gt, base, _, cell, baseline = pixels(args.dataset, split, name)
            targets = targets2(args.dataset, split, name, gt, base, cell)
            image_id = lookup[name]; sl = slice(offsets[image_id], offsets[image_id+1])
            arrays = dict(image=name, baseline=baseline, cell_totals=part['cell_totals'], objects2=len(targets))
            for method in methods:
                field = score_field(objects, part['id'], scores[method]['score'][sl], shape)
                hist, basehits = histogram(field, gt, base, cell, targets)
                arrays['hist__' + method] = hist
                arrays['basehits'] = basehits
            full = score_field(objects, part['id'], np.ones(len(part['id'])), shape)
            pos = part['label'] == 1
            positive = score_field(objects, part['id'][pos], np.ones(int(pos.sum())), shape)
            arrays['cell1_coverage'] = int((np.isfinite(full) & (cell == 1)).sum())
            arrays['cell2_coverage'] = int((np.isfinite(full) & (cell == 2)).sum())
            arrays['positive_cell2_coverage'] = int((np.isfinite(positive) & (cell == 2)).sum())
            save_npz(dest, arrays)
            print(f'curves {split}/{name}', flush=True)


def fraction(num, den):
    return np.divide(np.asarray(num, float), den, out=np.full_like(np.asarray(num, float), np.nan), where=den != 0)


def cumulative(hist, baseline, cell_totals, object_total, basehits):
    if len(hist):
        values, inv = np.unique(hist[:, 0], return_inverse=True)
        counts = np.column_stack([np.bincount(inv, weights=hist[:, k], minlength=len(values)) for k in range(1, 8)])
        thresholds = np.r_[np.inf, values[::-1]]
        counts = np.vstack([np.zeros(7), np.cumsum(counts[::-1], axis=0)])
    else:
        thresholds, counts = np.array([np.inf]), np.zeros((1, 7))
    tp0, fp0, fn0 = baseline
    tp, fp, fn = tp0+counts[:, 0], fp0+counts[:, 1], fn0-counts[:, 0]
    f0, i0 = 2*tp0/max(1, 2*tp0+fp0+fn0), tp0/max(1, tp0+fp0+fn0)
    return dict(threshold=thresholds, delta_f1=100*(fraction(2*tp, 2*tp+fp+fn)-f0),
                delta_iou=100*(fraction(tp, tp+fp+fn)-i0),
                recall2=100*fraction(counts[:, 2], cell_totals[1]),
                recall1=100*fraction(counts[:, 3], cell_totals[0]),
                recall3=100*fraction(counts[:, 4], cell_totals[2]),
                recall5=100*fraction(counts[:, 5], cell_totals[3]),
                object_recall2=100*fraction(counts[:, 6]+basehits, object_total),
                delta_miss_rate=-100*fraction(counts[:, 0], tp0+fn0),
                added_tp=counts[:, 0], added_fp=counts[:, 1], recovered2=counts[:, 2],
                recovered1=counts[:, 3], object_hits2=counts[:, 6]+basehits,
                added_precision=100*fraction(counts[:, 0], counts[:, 0]+counts[:, 1]))


def scalar(x):
    return float(x) if np.isfinite(x) else None


def point(curve, index):
    value = {k: scalar(v[index]) for k, v in curve.items()}
    value['accept_none'] = bool(np.isposinf(curve['threshold'][index]))
    value['f1_cost_per_recall2_pp'] = (-value['delta_f1']/value['recall2']
                                     if value['recall2'] and value['delta_f1'] is not None else None)
    return value


def at_threshold(curve, threshold):
    if threshold is None:
        return 0
    return int(np.searchsorted(-curve['threshold'], -threshold, side='right')-1)


def merge(args):
    methods = manifest(args)['methods']
    dest = args.out / args.dataset / 'merged'
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / 'frozen.json').exists():
        raise FileExistsError('Freeze already exists; do not reselect/re-evaluate this run')
    frozen, summary = {}, {}
    for split in ('val', 'test'):
        parts = [archive(args.out / args.dataset / 'histograms' / split / (Path(n).stem + '.npz')) for n in names(args, split)]
        if [str(p['image']) for p in parts] != names(args, split):
            raise ValueError('Histogram inventory mismatch')
        baseline = np.sum([p['baseline'] for p in parts], axis=0)
        totals = np.sum([p['cell_totals'] for p in parts], axis=0)
        if not args.limit:
            reference = read_json(b40.base_dir(raw_dataset(args.dataset, split)) / 'manifest.json')['baseline']
            if baseline.tolist() != [reference[k] for k in ('tp', 'fp', 'fn')]:
                raise ValueError('Aggregated baseline differs from batch30')
        obj_total = sum(int(p['objects2']) for p in parts)
        basehits = sum(int(p['basehits']) for p in parts)
        info = dict(images=len(parts), baseline=baseline.tolist(), cell_totals=totals.tolist(), objects2=obj_total,
                    baseline_check='selected per-image batch30 counts' if args.limit else 'exact batch30 manifest match',
                    baseline_f1=100*2*int(baseline[0])/max(1, int(2*baseline[0]+baseline[1]+baseline[2])),
                    cell1_coverage=sum(int(p['cell1_coverage']) for p in parts),
                    cell2_coverage=sum(int(p['cell2_coverage']) for p in parts),
                    positive_cell2_coverage=sum(int(p['positive_cell2_coverage']) for p in parts), methods={})
        info['cell1_upper_percent'] = scalar(100*fraction(info['cell1_coverage'], totals[0]))
        info['positive_cell2_upper_percent'] = scalar(100*fraction(info['positive_cell2_coverage'], totals[1]))
        info['all_cell2_upper_percent'] = scalar(100*fraction(info['cell2_coverage'], totals[1]))
        for method in methods:
            hist = np.concatenate([p['hist__' + method] for p in parts])
            curve = cumulative(hist, baseline, totals, obj_total, basehits)
            save_npz(dest / split / (method + '.npz'), curve)
            if split == 'val':
                frozen[method] = {}
                for budget in BUDGETS:
                    feasible = np.flatnonzero(curve['delta_f1'] >= -budget-1e-10)
                    recall = np.nan_to_num(curve['recall2'][feasible], nan=0.)
                    index = feasible[np.argmax(recall)]  # Descending thresholds: ties choose highest threshold.
                    frozen[method][str(budget)] = point(curve, index)
                info['methods'][method] = frozen[method]
            else:
                info['methods'][method] = {key: point(curve, at_threshold(curve, row['threshold']))
                                           for key, row in frozen[method].items()}
                # Keep the actual frozen threshold, even when the test score grid has no equal value.
                for key, row in frozen[method].items():
                    info['methods'][method][key].update(threshold=row['threshold'], accept_none=row['accept_none'])
        summary[split] = info
        if split == 'val':
            write_json(dest / 'frozen.json', dict(dataset=args.dataset, rule=manifest(args)['rule'],
                       selection='validation: max cell2 recall with delta F1 >= -budget; ties highest threshold', methods=frozen))
    write_json(dest / 'summary.json', summary)


def frozen_points(args):
    frozen = read_json(args.out / args.dataset / 'merged/frozen.json')['methods']
    for split in ('val', 'test') if args.split == 'all' else (args.split,):
        data = assemble(args, split)
        scores = {m: load_scores(args, m, split, data)['score'] for m in frozen}
        offsets = np.r_[0, np.cumsum(data['counts'])]
        lookup = {n: i for i, n in enumerate(names(args, split))}
        for name in names(args, split, True):
            dest = args.out / args.dataset / 'points' / split / (Path(name).stem + '.json')
            if dest.exists():
                raise FileExistsError(f'{dest}: frozen points already evaluated')
            part = archive(unit_path(args, split, name))
            objects, shape = pool_objects(args.dataset, split, name)
            gt, base, _, cell, _ = pixels(args.dataset, split, name)
            targets = targets2(args.dataset, split, name, gt, base, cell)
            i = lookup[name]; sl = slice(offsets[i], offsets[i+1])
            results = {}
            for method, budgets in frozen.items():
                field = score_field(objects, part['id'], scores[method][sl], shape)
                unique = {}
                results[method] = {}
                for key, frozen_row in budgets.items():
                    threshold = frozen_row['threshold']
                    if threshold not in unique:
                        add = np.zeros(shape, bool) if threshold is None else ((field >= threshold) & ~base & np.isfinite(field))
                        fp = add & ~gt
                        _, count = ndimage.label(fp, EIGHT)
                        hits = sum(int(((base[box] | add[box]) & mask).sum())*2 >= area for box, mask, area, _ in targets)
                        unique[threshold] = dict(added_fp=int(fp.sum()), added_fp_components=int(count),
                             added_tp=int((add & gt).sum()), recovered2=int((add & (cell == 2)).sum()),
                             recovered1=int((add & (cell == 1)).sum()), object_hits2=int(hits))
                    results[method][key] = dict(unique[threshold], threshold=threshold)
            write_json(dest, dict(image=name, methods=results))
            print(f'points {split}/{name}', flush=True)


def main():
    p = parser(__doc__)
    p.add_argument('stage', choices=('curves', 'merge', 'points'))
    p.add_argument('--split', choices=('val', 'test', 'all'), default='all')
    shard_arguments(p)
    args = setup(p.parse_args()); check_shard(args)
    if args.stage == 'merge' and (args.shards != 1 or args.shard != 0 or args.split != 'all'):
        p.error('merge consumes complete val/test inventories')
    start = time.monotonic()
    {'curves': curves, 'merge': merge, 'points': frozen_points}[args.stage](args)
    timing(args, args.stage, start)

if __name__ == '__main__':
    main()
