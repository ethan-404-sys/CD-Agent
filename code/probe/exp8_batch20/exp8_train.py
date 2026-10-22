"""SECOND verifier_train: new ViT-H cache, seven pools, real-mask verification."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from exp8_batch20_common import (arguments, checked_units, frozen, oracle, pool18,
                                read_pool, rt, score, selected_names)


def automatic(args):
    import torch
    from torchange.models.segment_any_change.segment_anything import SamAutomaticMaskGenerator, sam_model_registry
    from exp7_refine import SAM_CKPT

    frozen(args)
    images = selected_names(args)
    dest = args.out / 'automatic/second_cv_train'
    pending = [n for n in images if not (dest / f'{Path(n).stem}.npz').is_file()]
    if pending:
        model = sam_model_registry['vit_h'](checkpoint=SAM_CKPT).to('cuda').eval()
        generator = SamAutomaticMaskGenerator(model)
        for number, name in enumerate(pending, 1):
            rgb = [rt.common.load_rgb('second_cv', name, view) for view in ('t1', 't2')]
            torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                parts = [rt.period_partition(generator.generate(im), im.shape[:2]) for im in rgb]
            torch.cuda.synchronize()
            seconds = time.perf_counter() - start
            rt.save_npz(dest / f'{Path(name).stem}.npz', labels=rt.temporal_partition(*parts),
                        gpu_seconds=rt.np.asarray(seconds))
            print(f'automatic {number}/{len(pending)} {name}: {seconds:.2f}s', flush=True)
    times = []
    for name in images:
        with rt.np.load(dest / f'{Path(name).stem}.npz', allow_pickle=False) as z:
            if z['labels'].shape != rt.common.load_label('second_cv', name).shape:
                raise ValueError(f'{name}: wrong automatic partition shape')
            times.append(float(z['gpu_seconds']))
    rt.write_json(args.out / 'timing' / f'train_auto_{args.shard[0]}of{args.shard[1]}.json',
                  dict(images=images, partial=bool(args.limit), newly_computed=len(pending),
                       seconds=rt.metrics.distribution(times), total_seconds=sum(times)))


def build(args):
    freeze = frozen(args)
    geom = freeze['geometry']
    images = selected_names(args)
    index = rt.pool_index('second_cv', images)
    if index is None:
        raise FileNotFoundError('SECOND legacy U0 pool is required')
    keys = [f'objects_{m}' for m in rt.METHODS] + ['U0', 'U4', 'U5_x4']
    config = dict(revision=1, dataset='second_cv_train', geometry=geom, units=keys,
                  partial=bool(args.limit), status='frozen_validation', threshold=freeze['threshold'],
                  sources=list(rt.SOURCES['second_cv']), dedup_rules=rt.DEDUP_RULES,
                  automatic='batch20/automatic/second_cv_train',
                  u5='batch18 direct pairs, split only above frozen threshold')
    # JSON normalization makes resumable configuration comparisons exact.
    import json
    config = json.loads(json.dumps(config))
    dest = args.out / 'pool/second_cv_train'
    for number, name in enumerate(images, 1):
        stem = Path(name).stem
        done = dest / 'images' / f'{stem}.json'
        if done.exists():
            row = read_pool(args, name)
            if row['config'] != config:
                raise ValueError(f'{done}: incompatible pool configuration')
            for key in keys:
                if not (dest / key / f'{stem}.npz').is_file():
                    raise FileNotFoundError(f'{name}/{key}: missing archive')
            continue
        start = time.perf_counter()
        prob = rt.probability('second_cv', name, args.batch18)
        base = rt.prediction('second_cv', name, args.batch18)
        gt = rt.common.load_label('second_cv', name)
        if gt.shape != base.shape or prob.shape != base.shape:
            raise ValueError(f'{name}: prediction / label shape mismatch')
        raw = rt.raw_objects('second_cv', name, prob, args.batch18)
        groups = rt.groups_for(raw)
        objects = {m: [rt.union_object(raw, ids) for ids in groups[m]] for m in rt.METHODS}
        with rt.np.load(args.out / 'automatic/second_cv_train' / f'{stem}.npz', allow_pickle=False) as z:
            labels, seconds = z['labels'], float(z['gpu_seconds'])
        if labels.shape != gt.shape:
            raise ValueError(f'{name}: automatic partition shape mismatch')
        variants = [(f'objects_{m}', objects[m]) for m in rt.METHODS]
        variants += [('U0', pool18.u0_units('second_cv', name, gt.shape, objects['none'], index, geom)[0]),
                     ('U4', pool18.u4_units(objects['none'], labels, gt.shape)),
                     ('U5_x4', rt.split_objects(objects['direct_pairs'], labels, gt.shape, freeze['threshold']))]
        row = dict(config=config, image=name, baseline=score(base, gt, base, name),
                   input_objects=len(raw), gpu_seconds_from_cache=seconds, units={})
        for key, units in variants:
            details = pool18.describe_units(units, gt, base)
            final = oracle(units, details, base)
            rt.save_units(dest / key / f'{stem}.npz', units, gt.shape)
            row['units'][key] = dict(unit_count=len(units), areas=[o['area'] for o in units],
                                    details=details, metrics=score(final, gt, base, name),
                                    label_counts={label: sum(d['label'] == label for d in details)
                                                  for label in ('positive', 'negative', 'middle')})
        row['seconds'] = time.perf_counter() - start
        rt.write_json(done, row)
        print(f'pool {number}/{len(images)} {name}: {row["seconds"]:.2f}s', flush=True)


def verify(args):
    checks = []
    for name in selected_names(args):
        row = read_pool(args, name)
        gt = rt.common.load_label('second_cv', name)
        base = rt.prediction('second_cv', name, args.batch18)
        for key, saved in row['units'].items():
            objects, shape, details = checked_units(args, name, key, row)
            if shape != gt.shape:
                raise ValueError(f'{name}/{key}: wrong shape')
            actual = pool18.describe_units(objects, gt, base)
            for a, b in zip(actual, details):
                if any(a[k] != b[k] for k in ('area', 'gt_fraction', 'label', 'residual_gt', 'residual_area')):
                    raise ValueError(f'{name}/{key}: labels differ on mask readback')
            # Independent batch18/7b oracle, compared with the saved batch20 counts.
            final = rt.oracle(rt.full_masks(objects, shape), gt, base)
            counts = rt.metrics.pixel_counts(final, gt)
            if any(counts[k] != saved['metrics']['counts'][k] for k in ('tp', 'fp', 'fn')):
                raise ValueError(f'{name}/{key}: oracle pixel counts differ')
        checks.append(name)
    rt.write_json(args.out / 'pool/second_cv_train' / f'verification_{args.shard[0]}of{args.shard[1]}.json',
                  dict(images=checks, partial=bool(args.limit), status='passed',
                       checks=['seven saved pools', 'areas/labels/residuals', 'batch18 oracle TP/FP/FN']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('automatic', 'build', 'verify'))
    arguments(parser, ('second_cv_train',))
    args = parser.parse_args()
    {'automatic': automatic, 'build': build, 'verify': verify}[args.action](args)


if __name__ == '__main__':
    main()
