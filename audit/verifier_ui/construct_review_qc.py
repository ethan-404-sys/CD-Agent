"""Offline QC for complete manifests. Run batch image work through Slurm."""
from __future__ import annotations

import importlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from task_data_import import read_image
import construct_review_store as store


def issue(code, severity, message):
    return dict(code=code, severity=severity, message=message)


def assets_for(task):
    meta = task.get('meta', {})
    assets = {k: task.get(v) for k, v in [('t1', 'image_t1'), ('t2', 'image_t2'), ('gt', 'gt_mask')]}
    for key in ('reference_mask', 'candidate_mask', 'ignore_mask'):
        if meta.get(key):
            assets[key] = meta[key]
    return assets


def source_paths(task):
    meta = task.get('meta', {})
    return ([s.get('path') for s in meta.get('source_annotations', []) if isinstance(s, dict)]
            + list(meta.get('source_images', {}).values()))


def load_mask(path):
    array = np.asarray(read_image(path))
    if array.ndim != 2 or not set(np.unique(array)).issubset({0, 1, 255}):
        raise ValueError('掩码必须为单通道二值 0/1 或 0/255')
    return array > 0


def task_qc(task):
    findings = []
    add = lambda code, severity, message: findings.append(issue(code, severity, message))
    meta = task.get('meta', {})
    assets = assets_for(task)
    stats = {}
    for key in ('id', 'task_type', 'instruction', 'image_t1', 'image_t2', 'gt_mask'):
        if not task.get(key):
            add('missing_' + key, 'hard_error', '缺少必需字段 ' + key)
    for key in ('review_id', 'data_version', 'dataset', 'split', 'scene_id', 'source_annotations'):
        if not meta.get(key):
            add('missing_' + key, 'unverified', '缺少来源字段 ' + key)
    for source in meta.get('source_annotations', []):
        if not isinstance(source, dict) or not all(source.get(k) for k in ('path', 'version')) or 'feature_ids' not in source:
            add('annotation_provenance', 'unverified', '原标注需 path、version、feature_ids（栅格可为空列表）')
    for name, path in list(assets.items()) + [('annotation', p) for p in source_paths(task)]:
        if not path or not Path(path).is_file():
            add('missing_file', 'hard_error', f'{name} 文件不存在：{path}')
    arrays = {}
    try:
        sizes = {}
        for key in ('t1', 't2'):
            with Image.open(assets[key]) as im:
                sizes[key] = im.size
        for key, path in assets.items():
            if key not in ('t1', 't2'):
                arrays[key] = load_mask(path)
                sizes[key] = arrays[key].shape[::-1]
        if len(set(sizes.values())) != 1:
            add('shape_mismatch', 'hard_error', f'网格尺寸不一致：{sizes}')
        gt = arrays['gt']
        stats.update(gt_pixels=int(gt.sum()), positive_task=bool(gt.any()))
        if not gt.any() and not meta.get('empty_gt_reason'):
            add('unexplained_empty_gt', 'unverified', '空GT需有真实负例依据 empty_gt_reason')
        if 'ignore_mask' in arrays and arrays['ignore_mask'].shape == gt.shape and np.any(gt & arrays['ignore_mask']):
            add('gt_ignore_overlap', 'hard_error', 'GT 与忽略区重叠')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        add('image_read', 'hard_error', f'图像/掩码无法核查：{exc}')
    if task.get('image_t1') == task.get('image_t2'):
        add('same_temporal_path', 'hard_error', '两时相引用同一文件')
    # A reviewer can approve the task while unresolved evidence still prevents formal export.
    add('registration', 'manual_review', '已核对两时相视觉配准、影像时序及GT目标可辨认性')
    kind = str(task.get('task_type', ''))
    module_name = None
    if kind.startswith('T4.3'):
        module_name = 'make_t43_tasks'
        if meta.get('event_type') != 'flood' or meta.get('target_object') not in ('road', 'building') or meta.get('impact_state') != 'flooded':
            add('flood_task_definition', 'hard_error', '事件/目标/影响状态不符合洪水道路或建筑定义')
        for key in ('aoi', 'gt_rules'):
            if not meta.get(key):
                add('missing_' + key, 'unverified', '缺少 ' + key)
    elif kind.startswith('T4.4'):
        module_name = 'make_t44_review_tasks'
        for key in ('relation_type', 'relation_params', 'reference_mask', 'candidate_mask', 'target_component_ids', 'candidate_count', 'filtered_count'):
            if key not in meta:
                add('missing_' + key, 'unverified', '缺少空间关系字段 ' + key)
        if not arrays.get('reference_mask', np.zeros(0, bool)).any():
            add('empty_reference', 'hard_error', '参照物掩码为空或不可读')
        add('reference_visual', 'manual_review', '参照物在影像中真实可识别，指令指代清楚')
        add('candidate_definition', 'manual_review', '已审定本条空间关系定义、参数及目标筛选作用')
        if meta.get('filtered_count', 0) <= 0:
            add('no_relation_selection', 'unverified', '当前关系未移除任何候选目标')
    else:
        add('task_rule', 'unverified', '该任务类型的GT规则尚未接入重算')
    if module_name:
        try:
            analysis = Path(__file__).resolve().parents[2] / 'code/agent_baseline/analysis'
            if str(analysis) not in sys.path:
                sys.path.insert(0, str(analysis))
            module = importlib.import_module(module_name)
            findings.extend(module.verify_task(task))
            if kind.startswith('T4.4') and all(k in arrays for k in ('candidate_mask', 'reference_mask', 'gt')):
                rebuilt = module.relation_truth(arrays['candidate_mask'], arrays['reference_mask'], meta['relation_type'], meta['relation_params'])
                if not np.array_equal(rebuilt['gt_mask'], arrays['gt']):
                    add('relation_gt_mismatch', 'hard_error', '关系重算GT不一致')
                for key in ('target_component_ids', 'candidate_count', 'filtered_count'):
                    if rebuilt[key] != meta.get(key):
                        add('relation_' + key, 'hard_error', '关系重算 ' + key + ' 不一致')
                stats.update(candidate_count=rebuilt['candidate_count'], retained_count=len(rebuilt['target_component_ids']), filtered_count=rebuilt['filtered_count'])
        except (ImportError, AttributeError) as exc:
            add('rule_verifier_unavailable', 'unverified', str(exc))
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            add('rule_recompute', 'hard_error', f'规则重算失败：{exc}')
    findings = [f for f in findings if not (f['code'] == 't43_visual_registration' and f['severity'] == 'unverified')]
    for finding in findings:
        if finding['code'] == 't43_candidate_definition' and finding['severity'] == 'unverified':
            finding['severity'] = 'manual_review'
    return dict(manual_checks=sorted({f['code'] for f in findings if f['severity'] == 'manual_review'}), passed=not any(f['severity'] in ('hard_error', 'unverified') for f in findings), findings=findings, stats=stats)


def batch_qc(tasks):
    reports = [task_qc(task) for task in tasks]
    scenes = defaultdict(list)
    duplicates = defaultdict(list)
    for i, task in enumerate(tasks):
        meta = task.get('meta', {})
        scenes[(meta.get('dataset'), meta.get('scene_id'))].append(i)
        try:
            mask = load_mask(task['gt_mask'])
            signature = (mask.shape, np.packbits(mask).tobytes())
        except (OSError, ValueError, KeyError):
            signature = task.get('gt_mask')
        duplicates[(task.get('image_t1'), task.get('image_t2'), task.get('instruction'), signature)].append(i)
    for ids in scenes.values():
        if len({tasks[i].get('meta', {}).get('split') for i in ids}) > 1:
            for i in ids:
                reports[i]['findings'].append(issue('scene_split_leak', 'hard_error', '同数据集/场景跨 split 重复'))
    for ids in duplicates.values():
        if len(ids) > 1:
            for i in ids:
                reports[i]['findings'].append(issue('duplicate_task', 'hard_error', '相同影像、指令、GT重复任务'))
    for report in reports:
        report['passed'] = not any(f['severity'] in ('hard_error', 'unverified') for f in report['findings'])
    summary = dict(total=len(tasks), passed=sum(r['passed'] for r in reports),
                   positive=sum(r['stats'].get('positive_task', False) for r in reports),
                   negative=sum(r['stats'].get('positive_task') is False for r in reports),
                   unknown_polarity=sum('positive_task' not in r['stats'] for r in reports),
                   candidate_total=sum(r['stats'].get('candidate_count', 0) for r in reports),
                   retained_total=sum(r['stats'].get('retained_count', 0) for r in reports),
                   filtered_total=sum(r['stats'].get('filtered_count', 0) for r in reports),
                   by_dataset=dict(Counter(t.get('meta', {}).get('dataset', '') for t in tasks)),
                   by_split=dict(Counter(t.get('meta', {}).get('split', '') for t in tasks)),
                   by_task=dict(Counter(t.get('task_type') for t in tasks)),
                   by_target=dict(Counter(str(t.get('meta', {}).get('target_object', t.get('target', ''))) for t in tasks)),
                   by_relation=dict(Counter(t.get('meta', {}).get('relation_type', '') for t in tasks)))
    return reports, summary
