"""Real-data acceptance checks for saved pools; run under Slurm after aggregation."""
from __future__ import annotations

import argparse
from pathlib import Path

from exp8_runtime import (METHODS, OUT, add_arguments, common, full_masks, image_names, load_units,
                         metrics, oracle, prediction, read_json, subset, write_json)


def same_counts(actual, expected, description):
    for key in ('tp', 'fp', 'fn'):
        if actual[key] != expected[key]:
            raise ValueError(f'{description}: {key} differs: {actual[key]} vs {expected[key]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    images = subset(image_names(args.dataset), args)
    base = args.out / 'pool' / args.dataset
    checks = []
    for name in images:
        stem = Path(name).stem
        row = read_json(base / 'images' / f'{stem}.json')
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
        if summary['partial'] or summary['images'] != image_names(args.dataset):
            raise ValueError('Acceptance requires a complete summary')
        if args.dataset.startswith('second'):
            reference = read_json(OUT / 'exp7_batch16' / f'units_{args.dataset}' / 'summary.json')
            same_counts(summary['rows']['U0']['evaluation']['counts'], reference['rows']['U0_pool']['counts'],
                        '7b U0_pool')
            reference = read_json(OUT / 'exp7_batch16' / f'units_{args.dataset}_U4' / 'summary.json')
            same_counts(summary['rows']['U4']['evaluation']['counts'], reference['rows']['U4']['counts'], '7b U4')
            historical += ['7b U0_pool exact TP/FP/FN', '7b U4 exact TP/FP/FN']
        if args.dataset in ('second', 'sysu'):
            for method in METHODS:
                reference = read_json(OUT / 'exp8_batch17/dedup' / args.dataset / f'stats_{method}.json')
                same_counts(summary['rows'][f'objects_{method}']['evaluation']['counts'],
                            reference['evaluation']['counts'], f'batch17 {method}')
                historical.append(f'batch17 {method} exact TP/FP/FN')
    write_json(base / f'verification_{args.shard[0]}of{args.shard[1]}.json',
               dict(images=checks, partial=bool(args.limit), historical_checks=historical,
                    checks=['saved NPZ geometry/count/area', 'GT fraction and label', 'saved-mask oracle TP/FP/FN'],
                    status='passed'))
    print(f'{args.dataset}: {len(checks)} real images verified; {len(historical)} historical checks', flush=True)


if __name__ == '__main__':
    main()
