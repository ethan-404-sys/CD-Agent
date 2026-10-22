"""Prepare SYSU manifest, official predictions, validation-only U5 scale and U4."""
from __future__ import annotations

import argparse
from pathlib import Path

import exp8_runtime as rt
import exp8_data as data


def write_manifest(args):
    if args.limit or args.shard != (0, 1) or args.dataset != 'sysu_val':
        raise ValueError('Manifest always contains the complete frozen SYSU val split')
    row = data.manifest()
    spec = rt.common.DATASETS['sysu_val']
    for name in row['all_images']:
        for folder in spec['dirs']:
            path = spec['root'] / folder / name
            if not path.is_file():
                raise FileNotFoundError(path)
    dest = args.out / 'exp6/sysu_val'
    path = dest / 'manifest.json'
    if path.exists() and rt.read_json(path) != row:
        raise ValueError(f'{path}: existing manifest differs')
    rt.write_json(path, row)
    (dest / 'all_train.txt').write_text('\n'.join(row['all_images']) + '\n')
    for kind in ('pool_train', 'pool_val', 'verifier_train', 'verifier_val'):
        (dest / f'{kind}.txt').write_text('\n'.join(row[f'{kind}_images']) + '\n')
    print(path, flush=True)


def predict(args):
    if args.dataset != 'sysu_val':
        raise ValueError('Only sysu_val predictions may be generated')
    from exp8_compare import require_comparison
    require_comparison(args.out)
    import predict_schanger as original
    from PIL import Image
    pending = []
    dest = rt.prediction_dir(args.dataset, args.out)
    for name in rt.subset(data.image_names(scope=args.scope), args):
        prob = dest / 'prob' / (Path(name).stem + '.npy')
        mask = dest / 'mask' / name
        if prob.is_file() and mask.is_file():
            rt.np.load(prob, mmap_mode='r', allow_pickle=False)
            with Image.open(mask) as im:
                im.verify()
        else:
            pending.append(name)
    if pending:
        original.PRED = args.out / 'preds'
        original.names = lambda dataset: pending
        original.write_json = lambda path, row: rt.write_json(
            dest / f'report_{args.scope}_{args.shard[0]}of{args.shard[1]}.json',
            dict(**row, limitation=data.LIMITATION, report_scope='newly computed images'))
        original.run('sysu_val', 'small')
    print(f'Prediction shard complete; newly computed {len(pending)}', flush=True)


def geometry(args):
    if args.dataset != 'sysu_val':
        raise ValueError('Geometry must be estimated on sysu_val')
    scope = 'pool_val'
    images = data.image_names(scope=scope)
    key = 'sysu_val'
    config = dict(dataset='sysu_val', kind='u5', scope=scope,
                  source=f'sysu_val/{scope}', split_source=str(data.SPLIT),
                  method='no_signal_missed_gt', detector='schanger_small',
                  min_area=rt.e4.MIN_TARGET, prediction_coverage_below=0.1,
                  probability_directory=str(rt.prediction_dir('sysu_val', args.out) / 'prob'),
                  test_gt_used=False, limitation=data.LIMITATION)
    dest = args.out / 'geometry' / key
    if args.merge:
        wanted = images[:args.limit or None]
        values = []
        for name in wanted:
            row = rt.read_json(dest / (Path(name).stem + '.json'))
            if row['config'] != config or row['image'] != name:
                raise ValueError(f'{name}: geometry settings changed')
            values.extend(row['areas'])
        if not values:
            raise ValueError('No no-signal targets; cannot estimate scale')
        rt.write_json(args.out / 'geometry' / f'{key}.json',
                      dict(**config, images=wanted, partial=bool(args.limit),
                           median_area=float(rt.np.median(values)), area=rt.metrics.distribution(values)))
        return
    for name in rt.subset(images, args):
        path = dest / (Path(name).stem + '.json')
        if path.is_file():
            row = rt.read_json(path)
            if row['config'] != config or row['image'] != name:
                raise ValueError(f'{path}: incompatible geometry checkpoint')
            continue
        gt = rt.common.load_label('sysu_val', name)
        _, areas, _ = rt.targets('sysu_val', name, gt, rt.probability('sysu_val', name, args.out) > .5)
        rt.write_json(path, dict(config=config, image=name, areas=areas[1:].tolist()))


def check_automatic(args):
    images = rt.subset(rt.image_names(args.dataset), args)
    for name in images:
        path = rt.automatic_path(args.dataset, name, args.out)
        with rt.np.load(path, allow_pickle=False) as z:
            if z['labels'].shape != (256, 256) or not rt.np.isfinite(float(z['gpu_seconds'])):
                raise ValueError(f'{path}: incomplete SYSU automatic cache')
    print(f'{args.dataset}: {len(images)} automatic caches readable', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('manifest', 'predict', 'geometry', 'automatic', 'estimate',
                                          'check-automatic'))
    rt.add_arguments(parser)
    parser.add_argument('--merge', action='store_true')
    parser.add_argument('--scope', choices=('all', 'pool_train', 'pool_val'), default='all')
    args = parser.parse_args()
    if args.limit < 0 or args.merge and args.action != 'geometry':
        parser.error('Require nonnegative --limit; --merge is only for geometry')
    if args.action == 'estimate':
        images = rt.subset(rt.image_names(args.dataset), args)
        count = sum(not rt.automatic_path(args.dataset, n, args.out).is_file() for n in images)
        print(dict(dataset=args.dataset, images=len(images), missing_automatic=count,
                   sysu_test_seconds_per_pair=2.70167663320899,
                   estimated_gpu_hours=count*2.70167663320899/3600), flush=True)
    elif args.action == 'automatic':
        original = rt.batch18_module('exp8_prepare')
        if args.action == 'automatic' and args.dataset == 'sysu':
            raise ValueError('SYSU test U4 is read-only; wait for batch18 jobs')
        if args.dataset == 'sysu_val':
            original.image_names = lambda dataset: data.image_names(scope=args.scope)
        getattr(original, args.action)(args)
    else:
        {'manifest': write_manifest, 'predict': predict, 'geometry': geometry,
         'check-automatic': check_automatic}[args.action](args)


if __name__ == '__main__':
    main()
