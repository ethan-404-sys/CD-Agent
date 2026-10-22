"""Real-data acceptance checks for saved pools; run under Slurm after aggregation."""
from __future__ import annotations

import argparse
from pathlib import Path

import exp8_runtime as rt
import exp8_data as data

from exp8_runtime import (METHODS, OUT, add_arguments, common, full_masks, image_names, load_units,
                         metrics, oracle, prediction, read_json, subset, write_json)


same_counts = rt.batch18_module('exp8_verify').same_counts


def verify_pieces(out, name, config):
    from exp8_pieces import recall_units
    import exp6_pool_io as io
    base = out / 'exp6/pool/sysu_val'
    row = read_json(base / 'images' / (Path(name).stem + '.json'))
    gt = common.load_label('sysu_val', name)
    io.POOL = base.parent
    masks = io.load_piece_masks('sysu_val', name)
    if len(masks) != len(row['records']):
        raise ValueError(f'{name}: 6a record/mask count mismatch')
    for record in row['records']:
        box, mask = masks[record['piece_id']]
        x0, y0, x1, y1 = box
        fraction = float(gt[y0:y1, x0:x1][mask].mean())
        label = 1 if fraction >= .5 else 0 if fraction < .1 else None
        if (record['area'] != int(mask.sum()) or record['bbox'] != box
                or record['gt_frac'] != fraction or record['label'] != label):
            raise ValueError(f'{name}: 6a saved mask/label mismatch')
    old = recall_units(name, base, config['slic_geometry'])
    new, shape = load_units(out / 'pool/sysu_val/U0' / (Path(name).stem + '.npz'))
    if shape != gt.shape or len(old) != len(new):
        raise ValueError(f'{name}: 6a/U0 count mismatch')
    for a, b in zip(old, new):
        if a['bbox'] != b['bbox'] or not rt.np.array_equal(a['mask'], b['mask']):
            raise ValueError(f'{name}: 6a/U0 masks differ')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    images = subset(image_names(args.dataset), args)
    base = args.out / 'pool' / args.dataset
    checks = []
    split_checks = []
    if args.dataset == 'sysu_val':
        split = data.split()
        if not set(images) <= set(data.image_names()):
            raise ValueError('Images outside the official split')
        if set(split['train_blocks']) & set(split['val_blocks']):
            raise ValueError('Original-image block crosses the split')
        split_checks = ['all images in split.json', '3200/800 disjoint images',
                        '128/32 disjoint complete original-image blocks']
        from exp8_compare import require_comparison
        require_comparison(args.out)
    config = None
    for name in images:
        stem = Path(name).stem
        row = read_json(base / 'images' / f'{stem}.json')
        if row['image'] != name or row['config']['partial'] != bool(args.limit):
            raise ValueError(f'{name}: wrong image or partial flag')
        if config is None:
            config = row['config']
        if row['config'] != config:
            raise ValueError('Mixed image configurations')
        if set(row['units']) != set(config['units']):
            raise ValueError(f'{name}: saved variants differ from configuration')
        if args.dataset == 'sysu_val':
            expected_scope = 'pool_train' if name in split['train_images'] else 'pool_val'
            if row['pool_split'] != expected_scope:
                raise ValueError(f'{name}: incorrect pool split')
            verify_pieces(args.out, name, config)
        gt = common.load_label(args.dataset, name)
        pred = prediction(args.dataset, name, args.out)
        for key, unit in row['units'].items():
            objects, shape = load_units(base / key / f'{stem}.npz')
            if (shape != gt.shape or len(objects) != unit['unit_count']
                    or len(objects) != len(unit['details'])):
                raise ValueError(f'{name}/{key}: saved mask geometry/count mismatch')
            if [o['area'] for o in objects] != unit['areas']:
                raise ValueError(f'{name}/{key}: mask areas differ from metadata')
            counts = dict(positive=0, negative=0, middle=0)
            for obj, detail in zip(objects, unit['details']):
                x0, y0, x1, y1 = obj['bbox']
                frac = float(gt[y0:y1, x0:x1][obj['mask']].mean())
                label = 'positive' if frac >= .5 else 'negative' if frac < .1 else 'middle'
                if label != detail['label'] or frac != detail['gt_fraction']:
                    raise ValueError(f'{name}/{key}: wrong unit truth fraction/label')
                counts[label] += 1
            if counts != unit['label_counts']:
                raise ValueError(f'{name}/{key}: label totals differ')
            masks = full_masks(objects, shape)
            same_counts(metrics.pixel_counts(oracle(masks, gt, pred), gt), unit['metrics']['counts'],
                        f'{name}/{key} saved-mask oracle')
        checks.append(name)
    historical = []
    if args.shard == (0, 1) and not args.limit:
        summary = read_json(base / 'summary.json')
        if (summary['partial'] or summary['images'] != image_names(args.dataset)
                or summary['config'] != config):
            raise ValueError('Acceptance requires a complete summary')
        if args.dataset == 'sysu_val':
            parts = {scope: read_json(base / f'summary_{scope}.json')
                     for scope in ('pool_train', 'pool_val')}
            for scope, part in parts.items():
                if (part['images'] != data.image_names(scope=scope) or part['scope'] != scope
                        or part['partial'] or part['config'] != config):
                    raise ValueError(f'{scope}: incomplete or incompatible summary')
            for key in config['units']:
                combined = {k: sum(part['rows'][key]['evaluation']['counts'][k]
                                   for part in parts.values()) for k in ('tp', 'fp', 'fn')}
                same_counts(combined, summary['rows'][key]['evaluation']['counts'], f'{key}: split totals')
        if args.dataset == 'sysu':
            for method in METHODS:
                reference = read_json(OUT / 'exp8_batch17/dedup' / args.dataset / f'stats_{method}.json')
                same_counts(summary['rows'][f'objects_{method}']['evaluation']['counts'],
                            reference['evaluation']['counts'], f'batch17 {method}')
                historical.append(f'batch17 {method} exact TP/FP/FN')
    if config is None:
        raise ValueError('Empty verification shard')
    if config['slic_geometry'] != rt.load_scale(args.out, 'slic'):
        raise ValueError('U0 must retain historical SYSU scale 972')
    if config['geometry'] != rt.load_scale(args.out, 'u5', allow_partial=bool(args.limit)):
        raise ValueError('U5 geometry must come only from pool_val')
    if args.dataset == 'sysu':
        from exp8_pool import variants
        args.frozen = args.out / 'frozen/sysu_val.json'
        expected_keys, _, status = variants(args, config['geometry'])
        if config['units'] != expected_keys or config['status'] != status:
            raise ValueError('Test must contain only the frozen U5 multiplier')
    write_json(base / f'verification_{args.shard[0]}of{args.shard[1]}.json',
               dict(images=checks, config=config, partial=bool(args.limit), historical_checks=historical,
                    split_checks=split_checks, test_gt_used_for_selection=False,
                    test_gt_read_for_final_evaluation=args.dataset == 'sysu',
                    checks=['saved NPZ geometry/count/area', 'GT fraction and label', 'saved-mask oracle TP/FP/FN'],
                    status='passed'))
    print(f'{args.dataset}: {len(checks)} real images verified; {len(historical)} historical checks', flush=True)


if __name__ == '__main__':
    main()
