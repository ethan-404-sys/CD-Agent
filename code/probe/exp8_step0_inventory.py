"""Read-only CPU inventory of local data, SChanger provenance, and SYSU filenames."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import csv
import os
from pathlib import Path
import re
import sys
import time

sys.dont_write_bytecode = True
from exp8_common import DATASETS, PROBE, WORKSPACE, parallel_map, read_json, write_json
from PIL import Image

VOJ = WORKSPACE / 'Vlm_vs_OneJev'
SCHANGER = VOJ / 'vendor/SChanger'
FOLDERS = {'sysu': 'SYSU-CD', 'levir': 'LEVIR-CD', 'whu': 'WHU-CD', 'second': 'SECOND'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}
LAYOUTS = (('img1', 'img2', 'label_unch0chi255'), ('A', 'B', 'label'),
           ('time1', 'time2', 'label'), ('T1', 'T2', 'label'))


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_values(item)


def discover_roots(extra):
    """Use live common registrations plus paths actually present in local config/code."""
    roots = {ds: {} for ds in FOLDERS}

    def add(ds, path, evidence):
        path = Path(path)
        roots[ds].setdefault(str(path), []).append(evidence)

    for ds, folder in FOLDERS.items():
        registered = DATASETS[ds]['root']
        root = registered.parent if ds in ('levir', 'whu') else registered
        add(ds, root, f'{PROBE}/common.py: DATASETS[{ds}]')
        for path in (WORKSPACE / 'datasets' / folder, WORKSPACE / 'datasets/lim1' / folder,
                     SCHANGER / 'data' / folder):
            if path.exists():
                add(ds, path, 'Local sibling of registered dataset / SChanger evaluation data layout')
    source_files = [PROBE / 'common.py', VOJ / 'tools/prepare_resources.py', SCHANGER / 'evaluation/evaluate.py']
    for folder in (VOJ / 'configs', VOJ / 'reports'):
        if folder.exists():
            source_files.extend(sorted(folder.glob('*.json')))
    for path in source_files:
        if not path.is_file():
            continue
        if path.suffix == '.json':
            values = string_values(read_json(path))
        else:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            values = (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        for value in values:
            if not value.startswith('/') or '\n' in value:
                continue
            parts = Path(value).parts
            for ds, folder in FOLDERS.items():
                if folder in parts:
                    add(ds, Path(*parts[:parts.index(folder)+1]), str(path))
    for spec in extra:
        ds, separator, path = spec.partition('=')
        if not separator or ds not in roots:
            raise ValueError('--data-root requires sysu|levir|whu|second=/absolute/path')
        add(ds, Path(path), 'Explicit --data-root')
    return roots


def images_in(folder):
    if not folder.is_dir():
        return {}
    return {p.name: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES}


def split_of(name):
    prefix = name.split('_', 1)[0].lower()
    return {'validation': 'val', 'valid': 'val'}.get(prefix, prefix) if prefix in (
        'train', 'val', 'test', 'validation', 'valid') else 'unassigned'


def layouts(root):
    for dirs in LAYOUTS:
        if (root / dirs[0]).is_dir():
            yield root, dirs, None
    # Keep original images and derived test tiles as separate representations.
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            first = child.name.lower().split('_', 1)[0]
            if first not in ('train', 'val', 'test', 'validation', 'valid'):
                continue
            split = {'validation': 'val', 'valid': 'val'}.get(first, first)
            for dirs in LAYOUTS:
                if (child / dirs[0]).is_dir():
                    yield child, dirs, split


def header_job(paths):
    result = {}
    for role, path in paths.items():
        with Image.open(path) as im:
            result[role] = dict(size=list(im.size), mode=im.mode)
    return result


def inventory_layout(ds, root, dirs, explicit_split, workers, smoke):
    files = {role: images_in(root / directory) for role, directory in zip(('t1', 't2', 'gt'), dirs)}
    split_names = {explicit_split} if explicit_split else {split_of(n) for role in files.values() for n in role}
    results, val_names = [], []
    for split in sorted(split_names):
        selected = {role: {n: path for n, path in entries.items() if explicit_split or split_of(n) == split}
                    for role, entries in files.items()}
        all_names = sorted(set().union(*(set(v) for v in selected.values())))
        complete = set.intersection(*(set(v) for v in selected.values()))
        inspect_names = all_names[:3] if smoke else all_names
        sizes, modes = {r: Counter() for r in files}, {r: Counter() for r in files}
        mismatch = 0
        jobs = ({role: entries[name] for role, entries in selected.items() if name in entries} for name in inspect_names)
        for row in parallel_map(header_job, jobs, workers):
            mismatch += int(len({tuple(v['size']) for v in row.values()}) > 1)
            for role, data in row.items():
                sizes[role][f'{data["size"][0]}x{data["size"][1]}'] += 1
                modes[role][data['mode']] += 1
        results.append(dict(dataset=ds, path=str(root), resolved=str(root.resolve()), dirs=list(dirs), split=split,
                            files={role: len(v) for role, v in selected.items()}, complete_pairs=len(complete),
                            missing={role: len(set(all_names)-set(v)) for role, v in selected.items()},
                            dimensions={role: dict(v) for role, v in sizes.items()},
                            modes={role: dict(v) for role, v in modes.items()}, inspected_pairs=len(inspect_names),
                            size_mismatches=mismatch, dimension_scope='first_3_names' if smoke else 'all_headers',
                            filename_examples=all_names[:8]))
        if ds == 'sysu' and split == 'val':
            val_names.extend(sorted(complete))
    return results, val_names


def index_evidence(root):
    path = root / 'dataset_index.json'
    if not path.is_file():
        return None
    value = read_json(path)
    if isinstance(value, list):
        nulls = sum(v is None for v in value)
        examples = [v for v in value if v is not None][:2]
        explicit_group = any(isinstance(v, dict) and any(k in v for k in ('scene_id', 'original_pair', 'source_pair')) for v in value)
        return dict(path=str(path), rows=len(value), null_rows=nulls,
                    row_types=dict(Counter(type(v).__name__ for v in value)), examples=examples,
                    explicit_group_fields=explicit_group,
                    note='Null rows do not establish the existence or split membership of missing images.')
    return dict(path=str(path), structure=type(value).__name__, keys=list(value)[:20] if isinstance(value, dict) else [])


def filename_groups(val_names, mapping):
    names = sorted(set(val_names))
    result = dict(val_pairs=len(names), examples=names[:12], original_expected_pairs=800, tiles_per_pair=25,
                  recoverable=False, status='no_val_images' if not names else 'unverified',
                  conclusion='No complete SYSU val pairs found in the inspected local representations.' if not names else
                  'No verified original-airframe mapping. Do not derive groups with numeric filename // 25.')
    if not names:
        return result
    simple = sum(re.fullmatch(r'(?:val|valid|validation)_\d+\.[^.]+', n, re.IGNORECASE) is not None for n in names)
    result['split_serial_names'] = simple
    if simple == len(names):
        result.update(status='serial_only', conclusion='All val filenames contain only split and serial number; '
                      'the original 800-pair identities cannot be recovered from these names alone.')
    else:
        result['conclusion'] = 'Names may retain additional tokens, but no verified scene/tile convention was found; '
        result['conclusion'] += 'keep grouping unresolved until a source mapping or crop-generation record is available.'
    if mapping is not None:
        with mapping.open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        if not rows or not {'filename', 'original_pair', 'tile_id'} <= set(rows[0]):
            raise ValueError('--sysu-group-map CSV needs filename,original_pair,tile_id columns')
        records = {}
        identities = set()
        for row in rows:
            filename = row['filename']
            identity = row['original_pair'], row['tile_id']
            if filename in records or identity in identities or not all(identity):
                raise ValueError('Duplicate filename/tile identity or empty original_pair/tile_id in group map')
            records[filename] = row
            identities.add(identity)
        missing = sorted(set(names)-set(records))
        group_sizes = Counter(r['original_pair'] for r in rows)
        result.update(mapping=str(mapping), missing_val_names=missing[:20], missing_val_count=len(missing),
                      mapping_pairs=len(rows), original_groups=len(group_sizes),
                      tiles_per_original=dict(Counter(group_sizes.values())))
        if len(group_sizes) == 800 and set(group_sizes.values()) == {25} and not missing:
            result.update(recoverable=True, status='provided_complete_mapping',
                          conclusion='Provided mapping covers val and has 800 original pairs x 25 distinct tiles. '
                                     'Mapping provenance is supplied by the caller; filenames alone were insufficient.')
            result['val_groups'] = {n: records[n]['original_pair'] for n in names}
        else:
            result['conclusion'] = 'Provided mapping does not establish complete 800 x 25 grouping with full val coverage.'
    return result


def schanger_evidence(extra_roots):
    checkpoints = []
    for root in (VOJ / 'checkpoints', SCHANGER / 'weights', WORKSPACE / 'models/SChanger'):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*schanger*.pth')):
            checkpoints.append(dict(path=str(path), bytes=path.stat().st_size))
    config_path = SCHANGER / 'training_configs.json'
    training = read_json(config_path)
    configs = {key: {k: v for k, v in value.items() if k in (
        'epochs', 'eval_interval', 'save_interval', 'batch_size', 'image_size', 'seed')}
        for key, value in training['datasets'].items() if key in ('sysu-cd', 'levir-cd', 'whu-cd')}
    # Record evidence locations and classifications, not arbitrary log contents.
    source_files = [SCHANGER / 'train.py', SCHANGER / 'README.md', SCHANGER / 'evaluation/README.md',
                    SCHANGER / 'cd/SChanger.py', VOJ / 'tools/prepare_resources.py',
                    PROBE / 'predict_schanger.py', config_path]
    roots = [SCHANGER / 'logs', SCHANGER / 'outputs', VOJ / 'outputs', VOJ / 'diagnostics', *extra_roots]
    log_files = []
    for root in roots:
        if not root.is_dir():
            continue
        for folder, directories, files in os.walk(root):
            directories[:] = [d for d in directories if d not in ('masks', 'probabilities', 'images', 'preds', '.git', '__pycache__')]
            for name in files:
                path = Path(folder) / name
                related = 'schanger' in str(path).lower() or root in extra_roots
                if related and path.suffix in ('.log', '.out', '.txt', '.json', '.jsonl'):
                    log_files.append(path)
    evidence = []
    for path in sorted(set(source_files + log_files)):
        if not path.is_file():
            continue
        hits = []
        with path.open(encoding='utf-8', errors='replace') as handle:
            for line_number, line in enumerate(handle, 1):
                low = line.lower()
                tags = []
                if 'validation_metrics' in low and 'best_f1' in low:
                    tags.append('validation_f1_compared_or_assigned_to_best_f1')
                if 'save_checkpoint' in low and 'best.pth' in low:
                    tags.append('best_checkpoint_saved')
                if ('val-split' in low or 'val_split' in low) and ('val' in low):
                    tags.append('validation_split_configured')
                if 'schanger_small_sysu.pth' in low and ('resolve/main' in low or 'download' in low):
                    tags.append('published_sysu_weight_download_reference')
                if ('val' in low and ('best' in low or 'checkpoint' in low)) and path in log_files:
                    tags.append('log_mentions_validation_and_selection_requires_provenance_review')
                if tags:
                    hits.append(dict(line=line_number, tags=tags))
        evidence.append(dict(path=str(path), kind='log' if path in log_files else 'local_source_or_config', findings=hits))
    tags = {tag for e in evidence for hit in e['findings'] for tag in hit['tags']}
    return dict(checkpoints=checkpoints, config_path=str(config_path), training_config=configs,
                evidence=evidence, log_search_roots=[str(p) for p in roots], logs_inspected=len(log_files),
                local_training_selects_on_val={'validation_f1_compared_or_assigned_to_best_f1', 'best_checkpoint_saved'} <= tags,
                official_checkpoint_val_selection='unknown', assume_official_val_used=True,
                conclusion='Local training code can establish its validation selection rule; it does not establish '
                           'the training history of the downloaded published checkpoint. No linked original training '
                           'run is certified by this inventory. Treat official val as used for weight selection.',
                checkpoint_inspection='Filesystem presence/size and local download/config evidence only; no tensor loading or inference.')


def markdown(report):
    lines = ['# exp8 第 0 步：本地数据与权重盘点', '',
             '冒烟模式：文件名计数完整，尺寸只读取各划分前三个文件名。' if report['smoke'] else
             '只读取目录、图像头、配置和日志；尺寸按全部图像头统计，不解码整幅像素。', '',
             '| 数据集 | 路径 | 划分 | t1/t2/GT | 完整配对 | 尺寸（宽×高） |',
             '| --- | --- | --- | --- | ---: | --- |']
    for row in report['representations']:
        counts = '/'.join(str(row['files'][k]) for k in ('t1', 't2', 'gt'))
        sizes = '; '.join(f'{role}: '+', '.join(f'{k} ({v})' for k, v in values.items()) for role, values in row['dimensions'].items())
        lines.append(f'| {row["dataset"]} | {row["path"]} | {row["split"]} | {counts} | {row["complete_pairs"]} | {sizes} |')
    lines += ['', '同一数据的原图、裁块和软链接分别登记；软链接解析到同一目录时只扫描一次，不跨表示相加。', '',
              '| 数据集 | 未发现完整配对的划分 |', '| --- | --- |']
    for ds, splits in report['missing_splits'].items():
        lines.append(f'| {ds} | {", ".join(splits) or "无"} |')
    lines += ['', '## SYSU val 原始航片分组', '', report['sysu_grouping']['conclusion'], '',
              '不能用重命名后的编号整除 25 推断原始航片。dataset_index 的空条目也不能证明 val 文件仍存在。', '',
              '## SChanger', '',
              f'本地训练代码显示按 val 选择最优权重：{report["schanger"]["local_training_selects_on_val"]}。',
              '官方发布权重是否用 val 选过：未知，按“用过”处理。本地训练脚本不能证明官方权重的训练历史。',
              f'检查相关日志 {report["schanger"]["logs_inspected"]} 个；证据文件与行号见 inventory.json。', '',
              '| 权重路径 | 文件字节数 |', '| --- | ---: |']
    for row in report['schanger']['checkpoints']:
        lines.append(f'| {row["path"]} | {row["bytes"]} |')
    lines += ['', '## 训练池可用性', '', report['training_pool_decision'], '',
              '此次为本地盘点；未联网核实地面分辨率、未下载数据、未训练检测器、未生成数据划分。', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--data-root', action='append', default=[], help='Additional dataset=/absolute/root')
    p.add_argument('--evidence-root', type=Path, action='append', default=[], help='Additional SChanger training log directory')
    p.add_argument('--sysu-group-map', type=Path, help='Known-provenance CSV: filename,original_pair,tile_id')
    args = p.parse_args()
    if args.workers < 1:
        p.error('--workers must be positive')
    started = time.monotonic()
    roots = discover_roots(args.data_root)
    representations, indexes, val_names, aliases, seen = [], [], [], [], set()
    for ds, candidates in roots.items():
        for text, evidence in candidates.items():
            root = Path(text)
            aliases.append(dict(dataset=ds, path=text, resolved=str(root.resolve()), exists=root.is_dir(), evidence=evidence))
            if not root.is_dir():
                continue
            if (ds, str(root.resolve())) not in seen:
                index = index_evidence(root)
                if index:
                    indexes.append(index)
            for location, dirs, split in layouts(root):
                key = ds, str(location.resolve()), dirs, split
                if key in seen:
                    continue
                seen.add(key)
                rows, vals = inventory_layout(ds, location, dirs, split, args.workers, args.smoke)
                representations.extend(rows)
                val_names.extend(vals)
            seen.add((ds, str(root.resolve())))
    missing = {ds: [s for s in ('train', 'val', 'test') if not any(
        r['dataset'] == ds and r['split'] == s and r['complete_pairs'] for r in representations)] for ds in FOLDERS}
    grouping = filename_groups(val_names, args.sysu_group_map)
    schanger = schanger_evidence(args.evidence_root)
    weight_exists = any(Path(r['path']).name == 'schanger_small_sysu.pth' and r['bytes'] > 0 for r in schanger['checkpoints'])
    if not weight_exists:
        decision = '停止后续 SYSU 训练池生成：未发现已有 SChanger-small SYSU 权重。'
    elif 'val' not in missing['sysu']:
        decision = ('SYSU val 与已有权重可用；按提供的原始航片映射分组，仍保留 val 可能用于权重选择的限制。'
                    if grouping['recoverable'] else 'SYSU val 与已有权重可用，但原始航片分组未核实；停止后续训练/验证划分，需找回来源映射。')
    elif 'train' not in missing['sysu']:
        decision = '未发现可用 SYSU val；仅有 train 可作后备，存在检测器过拟合偏差。记录此限制并停在盘点，不自动生成训练池或重训。'
    else:
        decision = '停止后续 SYSU 训练池生成：未发现完整的 train 或 val 配对。'
    report = dict(smoke=args.smoke, aliases=aliases, representations=representations, missing_splits=missing,
                  dataset_indexes=indexes, sysu_grouping=grouping, schanger=schanger,
                  training_pool_decision=decision, seconds=time.monotonic()-started)
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / 'inventory.json', report)
    (args.out / 'inventory.md').write_text(markdown(report), encoding='utf-8')


if __name__ == '__main__':
    main()
