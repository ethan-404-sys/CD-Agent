"""Aggregate SYSU pools and freeze U5 exclusively on the 800 pool_val images."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import exp8_runtime as rt
import exp8_data as data

from exp8_runtime import (DEFAULT_OUT, DATASETS, image_names, metrics, read_json, write_json)


original_report = rt.batch18_module('exp8_report')
percentage = original_report.percentage
number = original_report.number


def markdown(summary):
    status = {'validation_candidates': '验证候选，未冻结',
              'frozen_validation': '验证集已冻结'}[summary['config']['status']]
    geometry = summary['config']['geometry']
    lines = [f'# {summary["dataset"]} 第 1 步', '',
             f'范围：{summary["scope"]}；图像数：{len(summary["images"])}；状态：{status}；'
             f'冒烟子集：{summary["partial"]}。', '',
             f'U5 面积中位数：{geometry["median_area"]:g} 像素；来源：{geometry["source"]}，'
             f'{len(geometry["images"])} 张，口径：{geometry["method"]}。',
             f'来源限制：{geometry["limitation"] or "无额外口径替代"}。', '',
             'F1、IoU、覆盖率和中间占比用百分数；ΔF1、ΔIoU 用百分点。面积单位为像素。', '',
             '| 单元 | 每图均值 / P50 / P90 | 面积 P10 / P50 / P90 / 最大 | 正 / 负 / 中间 | 中间 % | 无信号目标覆盖 % | 无信号像素覆盖 % | F1 | IoU | ΔF1 | ΔIoU |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    base = summary['baseline']['micro']
    lines.append(f'| detector | — | — | — | — | — | — | {percentage(base["pixel_f1"])} | {percentage(base["pixel_iou"])} | 0.00 | 0.00 |')
    for key, row in summary['rows'].items():
        counts, areas, labels = row['unit_count'], row['unit_area'], row['label_counts']
        cov, pixel = row['no_signal'], row['evaluation']['micro']
        lines.append(f'| {key} | ' + ' / '.join(number(counts[k]) for k in ('mean', 'p50', 'p90'))
                     + ' | ' + ' / '.join(number(areas[k]) for k in ('p10', 'p50', 'p90', 'max'))
                     + f' | {labels["positive"]} / {labels["negative"]} / {labels["middle"]}'
                     + f' | {percentage(row["middle_fraction"])} | {percentage(cov["object_coverage"])}'
                     + f' | {percentage(cov["pixel_coverage"])} | {percentage(pixel["pixel_f1"])}'
                     + f' | {percentage(pixel["pixel_iou"])} | {row["delta_f1_pp"]:+.2f} | {row["delta_iou_pp"]:+.2f} |')
    lines += ['', '## 附表：边界与目标指标', '',
              '| 单元 | BF2 | BF5 | Boundary IoU | 目标 F1 | 新增误检目标/图 | 逐图下降 % | 有害编辑 % | 粗糙度 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    evaluations = {'detector': summary['baseline'], **{k: r['evaluation'] for k, r in summary['rows'].items()}}
    for key, row in evaluations.items():
        m = row['micro']
        lines.append(f'| {key} | ' + ' | '.join(percentage(m[k]) for k in ('bf2_f1', 'bf5_f1', 'biou_diag', 'object_f1'))
                     + f' | {row["counts"]["new_false_objects"]/row["images"]:.3f}'
                     + f' | {percentage(row["delta_f1"]["decline_fraction"])}'
                     + f' | {percentage(row["harmful_edit_rate"])} | {number(m["pred_roughness"])} |')
    lines += ['', '## 附表：面积膨胀与纯度', '',
              '| 单元 | 对象膨胀均值 / 最大 | 每图最大面积比均值 / 最大 | 面积加权混杂度 | 去掉原预测后的真值占比 |',
              '|---|---:|---:|---:|---:|']
    for key, row in summary['rows'].items():
        lines.append(f'| {key} | {number(row["inflation"]["mean"])} / {number(row["inflation"]["max"])}'
                     + f' | {number(row["max_area_ratio"]["mean"])} / {number(row["max_area_ratio"]["max"])}'
                     + f' | {percentage(row["area_weighted_impurity"])} | {percentage(row["residual_gt_fraction"])} |')
    lines += ['', '膨胀仅对四种对象去重方式定义；U4/U5 切分后不沿用父对象膨胀值。',
              '无信号目标采用 6a 的 ≥20 像素、预测覆盖 <10% 定义；单个单元覆盖目标 ≥50%，'
              '且单元面积 ≤4×目标外接框面积才计入目标覆盖。像素覆盖统计单元并集。',
              'U0 沿用历史 SYSU SLIC 尺度 972（历史测试统计），本批不重新读取测试标签估计尺度，保留单元间重叠；U4 使用 7b 的小面积优先像素去重。'
              'U5 保持小对象完整，仅切大对象，保留对象间重叠。',
              'U5 仅用官方 val 内冻结的 800 张验证图选择；SYSU test 只评冻结倍数。',
              data.LIMITATION, '']
    return '\n'.join(lines)


def aggregate(args):
    if args.scope != 'all' and args.dataset != 'sysu_val':
        raise ValueError('Selection subset is only defined for sysu_val')
    images = image_names(args.dataset)[:args.limit or None]
    if args.scope != 'all':
        allowed = set(data.image_names(scope=args.scope))
        images = [name for name in images if name in allowed]
    if not images:
        raise ValueError('No images in this scope; enlarge the smoke subset')
    if args.limit < 0:
        raise ValueError('--limit must be nonnegative')
    dest = args.out / 'pool' / args.dataset
    accum, baseline, config, timing = {}, [], None, []
    for name in images:
        row = read_json(dest / 'images' / (Path(name).stem + '.json'))
        if row['image'] != name:
            raise ValueError(f'{name}: wrong image checkpoint')
        if config is None:
            config = row['config']
            from exp8_pool import variants
            args.frozen = args.out / 'frozen/sysu_val.json' if args.dataset == 'sysu' else None
            keys, _, status = variants(args, config['geometry'])
            if config['units'] != keys or config['status'] != status:
                raise ValueError('Pool variants do not match the validation/frozen test protocol')
        if row['config'] != config or config['partial'] != bool(args.limit):
            raise ValueError('Mixed configurations or smoke/full image rows')
        baseline.append(row['baseline'])
        timing.append(row['seconds'])
        for key in config['units']:
            if not (dest / key / (Path(name).stem + '.npz')).is_file():
                raise FileNotFoundError(f'{key}/{name}: missing unit archive')
            u = row['units'][key]
            a = accum.setdefault(key, dict(metrics=[], areas=[], counts=[], labels=dict(positive=0, negative=0, middle=0),
                                          coverage=dict(targets=0, targets_covered=0, pixels=0, pixels_covered=0),
                                          inflation=[], max_area_ratio=[], total_area_ratio=[],
                                          minority_pixels=0, residual_area=0, residual_gt=0))
            a['metrics'].append(u['metrics'])
            a['areas'].extend(u['areas'])
            a['counts'].append(u['unit_count'])
            for label, count in u['label_counts'].items():
                a['labels'][label] += count
            for label, count in u['no_signal'].items():
                a['coverage'][label] += count
            a['max_area_ratio'].append(u['max_area_ratio'])
            a['total_area_ratio'].append(u['total_area_ratio'])
            for detail in u['details']:
                if key.startswith('objects_'):
                    a['inflation'].append(detail['inflation'])
                for field in ('minority_pixels', 'residual_area', 'residual_gt'):
                    a[field] += detail[field]
    base = metrics.aggregate(baseline)
    rows = {}
    for key, a in accum.items():
        ev = metrics.aggregate(a['metrics'])
        cov = a['coverage']
        cov.update(object_coverage=metrics.ratio(cov['targets_covered'], cov['targets'], None),
                   pixel_coverage=metrics.ratio(cov['pixels_covered'], cov['pixels'], None))
        rows[key] = dict(evaluation=ev, no_signal=cov, label_counts=a['labels'],
                         label_fractions={label: metrics.ratio(count, sum(a['counts']), None)
                                          for label, count in a['labels'].items()},
                         middle_fraction=metrics.ratio(a['labels']['middle'], sum(a['counts']), None),
                         unit_count=metrics.distribution(a['counts']), unit_area=metrics.distribution(a['areas']),
                         inflation=metrics.distribution(a['inflation']),
                         max_area_ratio=metrics.distribution(a['max_area_ratio']),
                         total_area_ratio=metrics.distribution(a['total_area_ratio']),
                         area_weighted_impurity=metrics.ratio(a['minority_pixels'], sum(a['areas']), None),
                         residual_gt_fraction=metrics.ratio(a['residual_gt'], a['residual_area'], None),
                         delta_f1_pp=100*(ev['micro']['pixel_f1']-base['micro']['pixel_f1']),
                         delta_iou_pp=100*(ev['micro']['pixel_iou']-base['micro']['pixel_iou']))
    summary = dict(dataset=args.dataset, images=images, scope=args.scope, partial=bool(args.limit), config=config,
                   baseline=base, rows=rows, cpu_seconds_per_image=metrics.distribution(timing))
    suffix = '_' + args.scope if args.scope != 'all' else ''
    write_json(dest / f'summary{suffix}.json', summary)
    (dest / f'report{suffix}.md').write_text(markdown(summary), encoding='utf-8')
    print(dest / f'report{suffix}.md', flush=True)


def freeze(args):
    if args.dataset != 'sysu_val' or args.limit:
        raise ValueError('Freeze requires all 800 SYSU pool_val images')
    summary = read_json(args.out / 'pool/sysu_val/summary_pool_val.json')
    images = data.image_names(scope='pool_val')
    verification = read_json(args.out / 'pool/sysu_val/verification_0of1.json')
    if (summary['partial'] or summary['images'] != images or summary['scope'] != 'pool_val'
            or summary['config']['status'] != 'validation_candidates'
            or summary['config']['geometry'] != rt.load_scale(args.out, 'u5')
            or summary['config']['slic_geometry'] != rt.load_scale(args.out, 'slic')
            or verification['partial'] or verification['status'] != 'passed'
            or verification['images'] != data.image_names()
            or verification['config'] != summary['config']):
        raise ValueError('Incomplete or incompatible validation/acceptance evidence')
    scores = {m: summary['rows'][f'U5_x{m}']['evaluation']['micro'] for m in (4, 8, 16)}
    selected = min(scores, key=lambda m: (-scores[m]['pixel_f1'], -scores[m]['pixel_iou'], m))
    row = dict(validation_dataset='sysu_val', validation_scope='pool_val',
               validation_images=images, split_source=str(data.SPLIT), partial=False,
               multiplier=selected, threshold=selected*summary['config']['geometry']['median_area'],
               geometry=summary['config']['geometry'], slic_geometry=summary['config']['slic_geometry'],
               selection_metrics=['pixel_f1', 'pixel_iou'],
               tie_rule='smaller multiplier only on exact F1 and IoU tie',
               official_val_used_for_checkpoint_selection=True, limitation=data.LIMITATION,
               validation_scores={m: {k: v[k] for k in ('pixel_f1', 'pixel_iou')} for m, v in scores.items()})
    path = args.out / 'frozen/sysu_val.json'
    if path.is_file() and read_json(path) != json.loads(json.dumps(row)):
        raise ValueError(f'{path}: a different freeze already exists; do not overwrite it')
    write_json(path, row)
    print(path, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('aggregate', 'freeze'))
    parser.add_argument('--dataset', choices=DATASETS, required=True)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--scope', choices=('all', 'pool_train', 'pool_val'), default='all')
    args = parser.parse_args()
    {'aggregate': aggregate, 'freeze': freeze}[args.action](args)


if __name__ == '__main__':
    main()
