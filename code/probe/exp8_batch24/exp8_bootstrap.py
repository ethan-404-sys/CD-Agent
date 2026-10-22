"""Install SYSU adapters before importing the shared batch22 entry points."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from exp8_reuse import STAGE, load
import exp8_config as cfg

sys.path.insert(0, str(STAGE))
np, rt = cfg.rt.np, cfg.rt


def cleaned_selection(pool, args):
    selected_path = args.batch21 / 'cleaning/selected.json'
    record = rt.read_json(selected_path)
    unit = cfg.unit(args)
    adapter = argparse.Namespace(batch19=args.batch19.resolve(), freeze=cfg.frozen_pool(args))
    if (record['partial'] or record['test_cleaned'] or record['main_unit'] != unit
            or record['rule'] not in cfg.b21.RULES
            or record['flags'] != cfg.b21.RULES[record['rule']]
            or record['training_images'] != cfg.names('train')
            or record['validation_images'] != cfg.names('val')
            or record['selection_metrics'] != ['pixel_f1', 'pixel_iou']
            or record['provenance'] != cfg.b21.provenance(adapter)):
        raise ValueError('Cleaning selection differs from complete SYSU training/validation pools')
    path = args.batch21 / 'cleaning/selected_training_ids' / record['rule'] / (unit + '.jsonl')
    if Path(record['training_ids'][unit]).resolve() != path.resolve():
        raise ValueError('Selected cleaning path differs from selected rule')
    selected, seen = np.zeros(len(pool), bool), set()
    with path.open() as handle:
        for row in map(json.loads, handle):
            name = row['image']
            archive = args.batch19 / 'pool/sysu_val' / unit / (Path(name).stem + '.npz')
            if (name in seen or row['dataset'] != 'sysu_val' or row['pool_split'] != 'pool_train'
                    or row['rule'] != record['rule'] or row['unit'] != unit
                    or Path(row['archive']).resolve() != archive.resolve()):
                raise ValueError(f'{name}: invalid cleaning row')
            seen.add(name)
            if name not in pool.slices:
                continue
            sl = pool.slices[name]
            ids = np.asarray(row['unit_ids'], np.int64)
            if len(np.unique(ids)) != len(ids) or np.any(ids < 0) or np.any(ids >= sl.stop - sl.start):
                raise ValueError(f'{name}: invalid retained IDs')
            selected[sl.start + ids] = True
    if seen != set(cfg.names('train')) or np.any(pool.label[selected] < 0):
        raise ValueError('Cleaning must cover all training images and keep only supervised units')
    return selected, dict(selection_file=str(selected_path), ids_file=str(path), selected=record)


def bootstrap(kind):
    cfg.configure_mode(kind)
    features = load('exp8_features')
    features.cleaned_selection = cleaned_selection
    if kind == 'u0':
        import exp8_u0
        exp8_u0.bind(features)
    return features


def mode_from_cli(training=False):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--pool', choices=('u5', 'u0'))
    parser.add_argument('--variant', choices=('full', 'clean', 'u0'))
    args, _ = parser.parse_known_args()
    kind = ('u0' if args.variant == 'u0' else 'u5') if training else (args.pool or 'u5')
    if args.pool is not None and args.pool != kind:
        parser.error('--pool conflicts with --variant')
    return kind


def evaluation_module():
    module = load('exp8_evaluate', '_batch24_evaluate')
    original_report = module.write_report

    def write_report(root, summary, frozen):
        original_report(root, summary, frozen)
        path = root / 'report.md'
        text = path.read_text(encoding='utf-8')
        text += '\n## 解释限制\n\n' + '\n'.join('- ' + s for s in cfg.LIMITATIONS) + '\n'
        text += '\n级联接受部分的 ΔIoU（百分点）：\n\n| 方案 | 分位数组合 | ΔIoU pp |\n|---|---|---:|\n'
        for key, scorer in summary['scorers'].items():
            for tag, row in scorer['cascades'].items():
                text += f'| {key} | {tag} | {module.fmt(row["accepted"]["delta_iou_pp"])} |\n'
        path.write_text(text, encoding='utf-8')

    module.write_report = write_report
    return module
