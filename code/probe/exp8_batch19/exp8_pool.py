"""Build and evaluate step-1 object/U0/U4/U5 pools, one resumable image at a time."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import exp8_runtime as rt
import exp8_data as data

from exp8_runtime import (DEDUP_RULES, METHODS, REVISION, SOURCES, add_arguments, automatic_path,
                         common, coverage, full_masks, groups_for,
                         image_names, load_geometry, metrics, np,
                         oracle, prediction, probability,
                         raw_objects, read_json, save_units, split_objects, subset,
                         targets, union_object, write_json)


def variants(args, geom):
    keys = [f'objects_{method}' for method in METHODS] + ['U0', 'U4']
    if args.dataset == 'sysu':
        if args.frozen is None:
            raise ValueError('SYSU test requires --frozen from complete validation')
        frozen = read_json(args.frozen)
        expected = 'sysu_val'
        if (frozen['validation_dataset'] != expected or frozen['partial']
                or frozen['validation_images'] != data.image_names(scope='pool_val')
                or frozen['validation_scope'] != 'pool_val'
                or frozen['split_source'] != str(data.SPLIT)
                or frozen['threshold'] != frozen['multiplier']*geom['median_area']
                or frozen['slic_geometry'] != rt.load_scale(args.out, 'slic')
                or frozen['geometry'] != geom or frozen['selection_metrics'] != ['pixel_f1', 'pixel_iou']):
            raise ValueError('Frozen selection does not match this dataset/geometry')
        multipliers = [frozen['multiplier']]
        status = 'frozen_validation'
    else:
        if args.frozen:
            raise ValueError('--frozen is only for SYSU test')
        multipliers = [4, 8, 16]
        status = 'validation_candidates'
    if any(m not in (4, 8, 16) for m in multipliers):
        raise ValueError('Only the prespecified multipliers 4/8/16 are supported')
    return keys + [f'U5_x{m}' for m in multipliers], multipliers, status


def u0_units(dataset, name, shape, objects, index, geom):
    if dataset == 'sysu_val':
        from exp8_pieces import recall_units
        return recall_units(name, index, geom), 'batch19_6a_historical_sysu_972'
    return original_pool.u0_units(dataset, name, shape, objects, index, geom)


original_pool = rt.batch18_module('exp8_pool')
u4_units = original_pool.u4_units
describe_units = original_pool.describe_units


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument('--frozen', type=Path)
    args = parser.parse_args()
    geom = load_geometry(args.dataset, args.out, allow_partial=bool(args.limit))
    keys, multipliers, status = variants(args, geom)
    slic_geom = rt.load_scale(args.out, 'slic', allow_partial=bool(args.limit))
    images = subset(image_names(args.dataset), args)
    if not images:
        raise ValueError('Empty image shard')
    config = dict(revision=REVISION, dataset=args.dataset, geometry=geom, slic_geometry=slic_geom,
                  limitation=data.LIMITATION,
                  sources=SOURCES[args.dataset], units=keys, status=status,
                  partial=bool(args.limit), dedup_rules=DEDUP_RULES,
                  u4='exact_7b_partition_then_disjoint',
                  u5='direct_pairs; split large only; preserve overlap and complete small objects',
                  b_definition='both classes > 0 and different')
    # Normalize tuples once for exact JSON checkpoint comparisons.
    import json
    config = json.loads(json.dumps(config))
    index = (args.out / 'exp6/pool/sysu_val' if args.dataset == 'sysu_val'
             else rt.pool_index('sysu', images))
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
        u0, u0_origin = u0_units(args.dataset, name, base.shape, original, index, slic_geom)
        baseline = metrics.image_metrics(base, gt, before=base)
        baseline['image'] = name
        variants_iter = [(f'objects_{method}', objects[method]) for method in METHODS]
        variants_iter += [('U0', u0), ('U4', u4_units(original, labels, base.shape))]
        # U5 retains each small direct-pair object in full. It does not apply U4's
        # cross-candidate subtraction to those objects; only large parents are cut.
        variants_iter += [(f'U5_x{m}', split_objects(objects['direct_pairs'], labels,
                                                   base.shape, m*geom['median_area'])) for m in multipliers]
        result = dict(config=config, image=name,
                      pool_split=('pool_train' if name in data.split()['train_images'] else 'pool_val')
                      if args.dataset == 'sysu_val' else 'test', baseline=baseline, input_objects=len(raw),
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
