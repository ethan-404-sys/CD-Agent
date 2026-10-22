"""CPU validation sweep, exclusive frozen selection, and one test per final scheme."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

from common import load_label, load_prob, load_rgb, load_classes
from exp6_pool_io import DETECTOR
from exp7_common import (ROOT, MERGES, PROMPTS, CONSERVATIVE, Confirmations, cache_config, cache_dir,
                         compose, diagnostic_summary, disk, exclusive_json, image_names, read_json,
                         sam_diagnostics, save_raw, selected_raw, verify_regions, write_json)
from metrics_cd import aggregate, distribution, image_metrics


def config_id(config):
    return '_'.join(config[k] for k in ('model', 'prompt', 'view', 'merge'))


def controls():
    prompts = ['P0', 'soft', 'morph']
    if hasattr(cv2, 'ximgproc') and hasattr(cv2.ximgproc, 'guidedFilter'):
        prompts.append('guided')
    return [dict(model='none', prompt=p, view='t2', merge='none') for p in prompts]


def control_mask(prompt, base, accepted, prob, rgb):
    if prompt == 'P0':
        return base | accepted
    if prompt == 'soft':
        return base | (accepted & (prob > 0.05))
    if prompt == 'morph':
        return ndimage.binary_closing(ndimage.binary_opening(base | accepted, disk(2)), disk(2))
    if prompt == 'guided':
        filtered = cv2.ximgproc.guidedFilter(rgb.astype(np.float32)/255,
                                            accepted.astype(np.float32), 4, 0.01)
        # Radius/epsilon/threshold fixed before validation; restrict to original
        # confirmation plus 5 pixels, the same locality convention as clip5.
        from exp7_common import expand
        return base | ((filtered > 0.5) & expand(accepted, 5))
    raise ValueError(prompt)


def check_caches(root, context, configs):
    for config in configs:
        if config['model'] == 'none':
            if config['prompt'] == 'guided' and config not in controls():
                raise RuntimeError('Frozen guided-filter configuration requires cv2.ximgproc')
            continue
        views = ('t1', 't2') if config['view'] == 'best' else (config['view'],)
        for view in views:
            directory = cache_dir(root, context.dataset, context.setting, context.combo,
                                  config['model'], config['prompt'], view)
            complete = read_json(directory / 'complete.json')
            expected = dict(**cache_config(context, config['model'], config['prompt'], view),
                            images=context.images)
            if complete['config'] != expected or complete['images'] != context.images:
                raise ValueError(f'{directory}: cache does not cover this complete split/configuration')


def evaluate(args, context, configs, destination):
    check_caches(args.root, context, configs)
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / 'summary.json').exists():
        raise FileExistsError(destination / 'summary.json')
    results = {config_id(c): [] for c in configs}
    diagnostics = {}
    start = time.monotonic()
    images = context.images
    example_names = (['test_987.png'] + [n for n in images if n != 'test_987.png'][:2]
                     if context.dataset == 'second' else images[:3])
    with (destination / 'per_image.jsonl').open('w') as per_image, (destination / 'sam_regions.jsonl').open('w') as region_file:
        import json
        for number, name in enumerate(images, 1):
            loaded = time.perf_counter()
            base, accepted = context.accepted(name)
            prob = load_prob(context.dataset, DETECTOR[context.dataset], name)
            rgb = load_rgb(context.dataset, name, 't2')
            input_seconds = time.perf_counter() - loaded
            gt = load_label(context.dataset, name)
            classes = load_classes(context.dataset, name)
            raw, diag = {}, {}
            example = dict(gt=gt, before=base, t2=rgb, accepted=accepted)
            for config in configs:
                key = config_id(config)
                elapsed, gpu_seconds = time.perf_counter(), 0.0
                if config['model'] == 'none':
                    final = control_mask(config['prompt'], base, accepted, prob, rgb)
                else:
                    raw_key = (config['model'], config['prompt'], config['view'])
                    if raw_key not in raw:
                        raw[raw_key] = list(selected_raw(args.root, context.dataset, context.setting,
                                                        context.combo, *raw_key, name))
                        verify_regions(accepted, raw[raw_key])
                    # Timing covers only composition. Disk loading and GT diagnostics
                    # are excluded for every merge, so cache hit order cannot bias it.
                    elapsed = time.perf_counter()
                    final = base | compose(base.shape, raw[raw_key], config['merge'])
                    gpu_seconds = sum(item['seconds'] for item in raw[raw_key])
                seconds = time.perf_counter() - elapsed + gpu_seconds + input_seconds
                if config['model'] != 'none' and raw_key not in diag:
                    diag[raw_key] = sam_diagnostics(gt, raw[raw_key], classes)
                    dkey = '/'.join(raw_key)
                    diagnostics.setdefault(dkey, []).extend(diag[raw_key])
                    for row in diag[raw_key]:
                        region_file.write(json.dumps(dict(image=name, configuration=dkey, **row), ensure_ascii=False) + '\n')
                result = image_metrics(final, gt, before=base)
                result.update(image=name, configuration=key, inference_seconds=seconds,
                              sam_seconds=gpu_seconds, input_seconds=input_seconds)
                results[key].append(result)
                per_image.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
                if name in example_names:
                    example[f'mask_{key}'] = final
            if name in example_names:
                save_raw(destination / (Path(name).stem + '.npz'), example)
            if number % 20 == 0:
                print(f'{number}/{len(images)} {time.monotonic()-start:.1f}s', flush=True)
    summary = dict(dataset=context.dataset, setting=context.setting, combo=context.combo,
                   images=images, configs=configs, conservative=CONSERVATIVE,
                   guided_filter='available' if any(c['prompt'] == 'guided' for c in configs)
                   else '未做：本次配置没有导向滤波；验证阶段若缺cv2.ximgproc则跳过',
                   seconds=time.monotonic()-start, examples=example_names, rows={})
    for config in configs:
        key = config_id(config)
        summary['rows'][key] = dict(**aggregate(results[key]),
                                   inference_seconds=distribution(r['inference_seconds'] for r in results[key]),
                                   sam_seconds=distribution(r['sam_seconds'] for r in results[key]))
    summary['sam'] = {key: diagnostic_summary(rows) for key, rows in diagnostics.items()}
    write_json(destination / 'summary.json', summary)
    return summary


def freeze(summary, path, root):
    p0 = summary['rows'][config_id(controls()[0])]['micro']
    rank, flags = [], {}
    for config in summary['configs']:
        if config['model'] == 'none':
            continue
        key = config_id(config)
        m = summary['rows'][key]['micro']
        eligible = m['pixel_f1'] >= p0['pixel_f1'] - 0.003
        flags[key] = dict(f1_eligible=eligible, boundary_improved=m['bf2_f1'] > p0['bf2_f1'],
                          roughness_improved=(m['pred_roughness'] is not None and p0['pred_roughness'] is not None
                                              and m['pred_roughness'] < p0['pred_roughness']))
        if eligible:
            rank.append(config)
    rank.sort(key=lambda c: (-summary['rows'][config_id(c)]['micro']['bf2_f1'],
                             summary['rows'][config_id(c)]['micro']['pred_roughness']
                             if summary['rows'][config_id(c)]['micro']['pred_roughness'] is not None else float('inf'),
                             -summary['rows'][config_id(c)]['micro']['pixel_f1'], config_id(c)))
    # One final merge/date choice per model/prompt family; all other settings
    # remain validation-only. Figure top3 is also fixed here.
    families, selected = set(), []
    for config in rank:
        family = config['model'], config['prompt']
        if family not in families:
            selected.append(config)
            families.add(family)
    result = dict(version=2, setting=summary['setting'], combo=summary['combo'],
                  source_dataset='second_cv', validation_images=summary['images'],
                  configs=[c for c in summary['configs'] if c['model'] == 'none'] + selected,
                  top3=selected[:3], validation_flags=flags, raw_root=str(Path(root).resolve()),
                  selection_rule=CONSERVATIVE[9], conservative=CONSERVATIVE)
    exclusive_json(path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('validate', 'test'), required=True)
    parser.add_argument('--dataset', choices=('second_cv', 'second', 'sysu', 'levir', 'whu'), required=True)
    parser.add_argument('--setting', choices=('O', 'V'), default='O')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--models', nargs='+', choices=('sam3', 'sam_vith'), default=['sam3', 'sam_vith'])
    parser.add_argument('--prompts', nargs='+', choices=PROMPTS, default=['P1', 'P2'])
    parser.add_argument('--views', nargs='+', choices=('t1', 't2', 'best'), default=['t2'])
    parser.add_argument('--merges', nargs='+', choices=MERGES, default=list(MERGES))
    args = parser.parse_args()
    if args.mode == 'validate':
        if args.dataset != 'second_cv':
            parser.error('Selection is restricted to second_cv validation')
        if args.selection.exists():
            raise FileExistsError(f'Frozen selection exists: {args.selection}')
        configs = controls() + [dict(model=m, prompt=p, view=v, merge=r)
                                for m in args.models for p in args.prompts for v in args.views for r in args.merges]
    else:
        if args.dataset == 'second_cv':
            parser.error('Test requires a test dataset')
        selection = read_json(args.selection)
        if (selection['setting'] != args.setting or selection['source_dataset'] != 'second_cv'
                or selection['validation_images'] != image_names('second_cv')
                or selection['raw_root'] != str(args.root.resolve())):
            raise ValueError('Frozen selection metadata differs')
        configs = selection['configs']
    context = Confirmations(args.dataset, args.setting)
    if len({config_id(c) for c in configs}) != len(configs):
        parser.error('Duplicate configurations')
    if args.mode == 'test':
        check_caches(args.root, context, configs)
        # Lock per final scheme under the raw experiment root, independent of
        # --out and selection filename. A failed test is not silently retried.
        registry = args.root / 'test_once' / args.dataset / args.setting
        for config in configs:
            if (registry / (config_id(config) + '.json')).exists():
                raise FileExistsError(f'Test already claimed: {config_id(config)}')
        for config in configs:
            exclusive_json(registry / (config_id(config) + '.json'),
                           dict(selection=str(args.selection), output=str(args.out), configuration=config))
    summary = evaluate(args, context, configs, args.out)
    if args.mode == 'validate':
        freeze(summary, args.selection, args.root)
    else:
        write_json(args.out / 'selection_used.json', selection)


if __name__ == '__main__':
    main()
