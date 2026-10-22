"""Build and evaluate step-1 object/U0/U4/U5 pools, one resumable image at a time."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from exp8_runtime import (DEDUP_RULES, METHODS, REVISION, SOURCES, add_arguments, automatic_path,
                         common, coverage, disjoint_units, full_masks, geometry, groups_for,
                         image_names, load_geometry, make_object, metrics, np,
                         old_pool_units, oracle, pool_index, prediction, probability,
                         raw_objects, read_json, save_units, slic, split_objects, subset,
                         targets, union_object, write_json)


def variants(args, geom):
    keys = [f'objects_{method}' for method in METHODS] + ['U0', 'U4']
    if args.dataset in ('second', 'levir'):
        if args.frozen is None:
            raise ValueError('SECOND/LEVIR test requires --frozen from complete validation')
        frozen = read_json(args.frozen)
        expected = 'second_cv' if args.dataset == 'second' else 'levir_val'
        if (frozen['validation_dataset'] != expected or frozen['partial']
                or frozen['geometry'] != geom or frozen['selection_metrics'] != ['pixel_f1', 'pixel_iou']):
            raise ValueError('Frozen selection does not match this dataset/geometry')
        multipliers = [frozen['multiplier']]
        status = 'frozen_validation'
    else:
        if args.frozen:
            raise ValueError('--frozen is only for SECOND/LEVIR test')
        multipliers = [4, 8, 16]
        status = 'validation_candidates' if args.dataset in ('second_cv', 'levir_val') else 'sensitivity_unfrozen'
    if any(m not in (4, 8, 16) for m in multipliers):
        raise ValueError('Only the prespecified multipliers 4/8/16 are supported')
    return keys + [f'U5_x{m}' for m in multipliers], multipliers, status


def u0_units(dataset, name, shape, objects, index, geom):
    # Legacy B for LEVIR/WHU had all-class transitions. Rebuild it from building XOR;
    # regenerate G with the same SLIC map so the new source convention is consistent.
    if dataset in ('second', 'second_cv', 'sysu') and index is not None:
        return old_pool_units(dataset, name, shape, index), 'existing_6a_pool'
    median = geometry(dataset)[0] if dataset in ('levir', 'whu') else geom['median_area']
    if any(o['area'] > 4*median for o in objects):
        rgb = np.concatenate([common.load_rgb(dataset, name, v) for v in ('t1', 't2')], axis=2)
        labels = slic(rgb, n_segments=max(4, int(np.prod(shape)/median)), compactness=10,
                      channel_axis=-1, start_label=1)
    else:
        labels = np.ones(shape, np.int32)
    return split_objects(objects, labels, shape, 4*median), f'6a_slic_rebuilt_area_{median:g}'


def u4_units(objects, labels, shape):
    parts = split_objects(objects, labels, shape)
    masks = full_masks(parts, shape)
    final = disjoint_units(masks)
    # Associate the unchanged 7b output order with the original parent metadata.
    order = iter(sorted(range(len(parts)), key=lambda i: (parts[i]['area'], i)))
    units = []
    for mask in final:
        while True:
            index = next(order)
            if np.all(~mask | masks[index]):
                break
        meta = {k: v for k, v in parts[index].items() if k not in ('bbox', 'mask', 'area')}
        units.append(make_object(mask, **meta))
    return units


def describe_units(units, gt, base):
    rows = []
    for i, obj in enumerate(units):
        x0, y0, x1, y1 = obj['bbox']
        local_gt = gt[y0:y1, x0:x1]
        truth = int((local_gt & obj['mask']).sum())
        fraction = truth / obj['area']
        residual = obj['mask'] & ~base[y0:y1, x0:x1]
        row = {k: v for k, v in obj.items() if k != 'mask'}
        row.update(unit_id=i, gt_fraction=fraction,
                   label='positive' if fraction >= .5 else 'negative' if fraction < .1 else 'middle',
                   minority_pixels=min(truth, obj['area']-truth),
                   residual_area=int(residual.sum()), residual_gt=int((residual & local_gt).sum()))
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument('--frozen', type=Path)
    args = parser.parse_args()
    geom = load_geometry(args.dataset, args.out, allow_partial=bool(args.limit))
    keys, multipliers, status = variants(args, geom)
    images = subset(image_names(args.dataset), args)
    if not images:
        raise ValueError('Empty image shard')
    config = dict(revision=REVISION, dataset=args.dataset, geometry=geom,
                  sources=SOURCES[args.dataset], units=keys, status=status,
                  partial=bool(args.limit), dedup_rules=DEDUP_RULES,
                  u4='exact_7b_partition_then_disjoint',
                  u5='direct_pairs; split large only; preserve overlap and complete small objects',
                  building_b='XOR(cls_t1 == 5, cls_t2 == 5), including unlabelled other date')
    # Normalize tuples once for exact JSON checkpoint comparisons.
    import json
    config = json.loads(json.dumps(config))
    index = pool_index(args.dataset, images) if args.dataset in ('second', 'second_cv', 'sysu') else None
    dest = args.out / 'pool' / args.dataset
    for number, name in enumerate(images, 1):
        stem = Path(name).stem
        done = dest / 'images' / f'{stem}.json'
        if done.is_file():
            row = read_json(done)
            if row['config'] != config or row['image'] != name:
                raise ValueError(f'{done}: incompatible completed image; use a new --out')
            for key in keys:
                if not (dest / key / f'{stem}.npz').is_file():
                    raise FileNotFoundError(f'{name}/{key}: missing saved units')
            continue
        start = time.perf_counter()
        prob = probability(args.dataset, name, args.out)
        base = prediction(args.dataset, name, args.out)
        gt = common.load_label(args.dataset, name)
        if prob.shape != base.shape or gt.shape != base.shape:
            raise ValueError(f'{name}: RGB prediction/GT geometry mismatch')
        raw = raw_objects(args.dataset, name, prob, args.out)
        groups = groups_for(raw)
        objects = {method: [union_object(raw, ids) for ids in groups[method]] for method in METHODS}
        target_data = targets(args.dataset, name, gt, prob > .5)
        with np.load(automatic_path(args.dataset, name, args.out), allow_pickle=False) as z:
            labels, gpu_seconds = z['labels'], float(z['gpu_seconds'])
        if labels.shape != base.shape:
            raise ValueError(f'{name}: automatic partition has the wrong shape')
        original = objects['none']
        u0, u0_origin = u0_units(args.dataset, name, base.shape, original, index, geom)
        baseline = metrics.image_metrics(base, gt, before=base)
        baseline['image'] = name
        variants_iter = [(f'objects_{method}', objects[method]) for method in METHODS]
        variants_iter += [('U0', u0), ('U4', u4_units(original, labels, base.shape))]
        # U5 retains each small direct-pair object in full. It does not apply U4's
        # cross-candidate subtraction to those objects; only large parents are cut.
        variants_iter += [(f'U5_x{m}', split_objects(objects['direct_pairs'], labels,
                                                   base.shape, m*geom['median_area'])) for m in multipliers]
        result = dict(config=config, image=name, baseline=baseline, input_objects=len(raw),
                      raw_objects=[{k: v for k, v in o.items() if k != 'mask'} for o in raw],
                      u0_origin=u0_origin, gpu_seconds_from_cache=gpu_seconds, units={})
        for key, units in variants_iter:
            masks = full_masks(units, base.shape)
            final = oracle(masks, gt, base)
            score = metrics.image_metrics(final, gt, before=base)
            score['image'] = name
            details = describe_units(units, gt, base)
            labels_count = {label: sum(r['label'] == label for r in details)
                            for label in ('positive', 'negative', 'middle')}
            area = [o['area'] for o in units]
            save_units(dest / key / f'{stem}.npz', units, base.shape)
            result['units'][key] = dict(unit_count=len(units), areas=area,
                                       label_counts=labels_count, no_signal=coverage(masks, target_data),
                                       metrics=score, details=details,
                                       max_area_ratio=metrics.ratio(max(area, default=0),
                                                                  max((o['area'] for o in raw), default=0), None),
                                       total_area_ratio=metrics.ratio(sum(area), sum(o['area'] for o in raw), None))
        result['seconds'] = time.perf_counter() - start
        # This is the completion marker, written only after every unit archive succeeds.
        write_json(done, result)
        print(f'{args.dataset} {number}/{len(images)} {name}: {result["seconds"]:.2f}s', flush=True)


if __name__ == '__main__':
    main()
