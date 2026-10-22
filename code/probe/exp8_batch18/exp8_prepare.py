"""Prepare validation geometry, missing LEVIR predictions, and 7b U4 caches (exp5)."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from exp8_runtime import (DETECTOR, add_arguments, automatic_path, common,
                         e4, geometry_key, image_names, metrics, np, prediction_dir,
                         probability, read_json, save_npz, subset, targets,
                         temporal_partition, period_partition, write_json)
from objects import gt_label


def estimate(args):
    images = subset(image_names(args.dataset), args)
    # Prior: SECOND 7b measured ~3.14 s/pair at 512²; 20 s/pair is its planning bound.
    count = sum(not automatic_path(args.dataset, n, args.out).is_file() for n in images)
    print(dict(dataset=args.dataset, images=len(images), missing_automatic=count,
               measured_second_seconds_per_pair=3.14, planning_seconds_per_pair=20,
               planning_gpu_hours=count*20/3600, exceeds_12_gpu_hours=count*20/3600 > 12), flush=True)


def automatic(args):
    images = subset(image_names(args.dataset), args)
    missing = [n for n in images if not automatic_path(args.dataset, n, args.out).is_file()]
    if args.dataset.startswith('second') and missing:
        raise FileNotFoundError(f'7b cache missing for {len(missing)} images; SECOND must not be regenerated')
    if missing:
        import torch
        from torchange.models.segment_any_change.segment_anything import SamAutomaticMaskGenerator, sam_model_registry
        from exp7_refine import SAM_CKPT
        model = sam_model_registry['vit_h'](checkpoint=SAM_CKPT).to('cuda').eval()
        generator = SamAutomaticMaskGenerator(model)
        for index, name in enumerate(missing, 1):
            rgb = [common.load_rgb(args.dataset, name, v) for v in ('t1', 't2')]
            torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                parts = [period_partition(generator.generate(im), im.shape[:2]) for im in rgb]
            torch.cuda.synchronize()
            seconds = time.perf_counter() - start
            labels = temporal_partition(*parts)
            save_npz(automatic_path(args.dataset, name, args.out), labels=labels,
                     gpu_seconds=np.asarray(seconds))
            print(f'U4 {index}/{len(missing)} {name}: {seconds:.2f} s/pair', flush=True)
    times = []
    for name in images:
        with np.load(automatic_path(args.dataset, name, args.out), allow_pickle=False) as z:
            times.append(float(z['gpu_seconds']))
    write_json(args.out / 'timing' / f'{args.dataset}_automatic_{args.shard[0]}of{args.shard[1]}.json',
               dict(images=images, newly_computed=len(missing), seconds=metrics.distribution(times),
                    historical_gpu_seconds=sum(times), reused_seconds=sum(times) if not missing else None))


def predict(args):
    if args.dataset != 'levir_val':
        raise ValueError('Only missing LEVIR official validation predictions are generated here')
    from PIL import Image
    import predict_schanger as original
    images = subset(image_names(args.dataset), args)
    directory = prediction_dir(args.dataset, args.out)
    pending = []
    for name in images:
        p = directory / 'prob' / (Path(name).stem + '.npy')
        m = directory / 'mask' / name
        if p.is_file() and m.is_file():
            # Header/image integrity only; an interrupted file must not count as complete.
            np.load(p, mmap_mode='r', allow_pickle=False)
            with Image.open(m) as image:
                image.verify()
        else:
            pending.append(name)
    if not pending:
        print('LEVIR val predictions complete', flush=True)
        return
    original.PRED = args.out / 'preds'
    original.names = lambda dataset: pending
    original.write_json = lambda path, row: write_json(
        directory / f'report_{args.shard[0]}of{args.shard[1]}.json', row)
    original.run('levir_val', 'base')


def geometry_plan(args):
    key = geometry_key(args.dataset)
    if args.dataset != key:
        raise ValueError(f'Geometry must be requested as --dataset {key}, never from test images')
    source = {'sysu': 'sysu_train', 'whu': 'whu_train'}.get(key, key)
    images = image_names(source)
    prob_dir = prediction_dir(source, args.out) / 'prob'
    # A smoke run can use just its first N validation predictions; full runs
    # still require the entire validation set before estimating the median.
    available = [(prob_dir / (Path(n).stem + '.npy')).is_file() for n in images[:args.limit or None]]
    if all(available):
        method = 'validation_missed_gt' if key in ('second_cv', 'levir_val') else 'train_missed_gt'
    elif key in ('sysu', 'whu') and not any(available):
        method = 'train_all_gt'
    else:
        raise FileNotFoundError(f'{source}: incomplete detector probabilities; complete them before geometry')
    return key, source, images, method


def geometry_run(args):
    key, source, all_images, method = geometry_plan(args)
    images = subset(all_images, args)
    config = dict(dataset=key, source=source, method=method, min_area=e4.MIN_TARGET,
                  detector=DETECTOR[source], probability_directory=str(prediction_dir(source, args.out) / 'prob'),
                  test_gt_used=False, split='validation' if source in ('second_cv', 'levir_val') else 'train')
    dest = args.out / 'geometry' / key
    if args.merge:
        wanted = all_images[:args.limit or None]
        values = []
        for name in wanted:
            row = read_json(dest / (Path(name).stem + '.json'))
            if row['config'] != config or row['image'] != name:
                raise ValueError(f'{name}: incompatible geometry checkpoint')
            values.extend(row['areas'])
        if not values:
            raise ValueError('No eligible geometry targets; cannot define U5 threshold')
        write_json(args.out / 'geometry' / f'{key}.json',
                   dict(**config, partial=bool(args.limit), images=wanted,
                        area=metrics.distribution(values), median_area=float(np.median(values)),
                        multipliers=[4, 8, 16],
                        limitation='No train predictions; all GT objects replace no-signal misses.'
                        if method == 'train_all_gt' else None))
        return
    for index, name in enumerate(images, 1):
        path = dest / (Path(name).stem + '.json')
        if path.is_file():
            row = read_json(path)
            if row['config'] != config:
                raise ValueError(f'{path}: changed geometry settings; use a new output directory')
            continue
        gt = common.load_label(source, name)
        if method == 'train_all_gt':
            labels, _, _ = gt_label(source, name, gt)
            areas = np.bincount(labels.ravel())[1:]
            areas = areas[areas >= e4.MIN_TARGET]
        else:
            _, areas, _ = targets(source, name, gt, probability(source, name, args.out) > .5)
            areas = areas[1:]
        write_json(path, dict(config=config, image=name, areas=areas.tolist()))
        if index % 20 == 0:
            print(f'geometry {source}: {index}/{len(images)}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('estimate', 'automatic', 'predict', 'geometry'))
    add_arguments(parser)
    parser.add_argument('--merge', action='store_true', help='aggregate all geometry image checkpoints')
    args = parser.parse_args()
    if args.merge and args.action != 'geometry':
        parser.error('--merge is only supported for geometry')
    {'estimate': estimate, 'automatic': automatic,
     'predict': predict, 'geometry': geometry_run}[args.action](args)


if __name__ == '__main__':
    main()
