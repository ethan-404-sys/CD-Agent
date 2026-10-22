"""Aggregate complete shards, freeze U5 by validation pixel scores, and plot test_759."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from exp8_runtime import (DEFAULT_OUT, DATASETS, WORKSPACE, common, full_masks, image_names,
                         legacy, load_units, metrics, np, pool_index, read_json, write_json)


def percentage(value):
    return '—' if value is None else f'{100*value:.2f}'


def number(value):
    return '—' if value is None else f'{value:.2f}'


def markdown(summary):
    status = {'validation_candidates': '验证候选，未冻结', 'frozen_validation': '验证集已冻结',
              'sensitivity_unfrozen': '敏感性，未冻结'}[summary['config']['status']]
    geometry = summary['config']['geometry']
    lines = [f'# {summary["dataset"]} 第 1 步', '',
             f'图像数：{len(summary["images"])}；状态：{status}；'
             f'冒烟子集：{summary["partial"]}。', '',
             f'U5 面积中位数：{geometry["median_area"]:g} 像素；来源：{geometry["source"]}，'
             f'{len(geometry["images"])} 张，口径：{geometry["method"]}。',
             f'来源限制：{geometry["limitation"] or "无额外口径替代"}。', '',
             'F1、IoU、覆盖率和中间占比用百分数；ΔF1 用百分点。面积单位为像素。', '',
             '| 单元 | 每图均值 / P50 / P90 | 面积 P10 / P50 / P90 / 最大 | 正 / 负 / 中间 | 中间 % | 无信号目标覆盖 % | 无信号像素覆盖 % | F1 | IoU | ΔF1 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    base = summary['baseline']['micro']
    lines.append(f'| detector | — | — | — | — | — | — | {percentage(base["pixel_f1"])} | {percentage(base["pixel_iou"])} | 0.00 |')
    for key, row in summary['rows'].items():
        counts, areas, labels = row['unit_count'], row['unit_area'], row['label_counts']
        cov, pixel = row['no_signal'], row['evaluation']['micro']
        lines.append(f'| {key} | ' + ' / '.join(number(counts[k]) for k in ('mean', 'p50', 'p90'))
                     + ' | ' + ' / '.join(number(areas[k]) for k in ('p10', 'p50', 'p90', 'max'))
                     + f' | {labels["positive"]} / {labels["negative"]} / {labels["middle"]}'
                     + f' | {percentage(row["middle_fraction"])} | {percentage(cov["object_coverage"])}'
                     + f' | {percentage(cov["pixel_coverage"])} | {percentage(pixel["pixel_f1"])}'
                     + f' | {percentage(pixel["pixel_iou"])} | {row["delta_f1_pp"]:+.2f} |')
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
              'U0 是历史切块池口径，保留单元间重叠；U4 使用 7b 的小面积优先像素去重。'
              'U5 保持小对象完整，仅切大对象，保留对象间重叠。',
              'SYSU/WHU 三个倍数仅作敏感性描述，不能根据测试分数冻结。', '']
    return '\n'.join(lines)


def aggregate(args):
    images = image_names(args.dataset)[:args.limit or None]
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
                         delta_f1_pp=100*(ev['micro']['pixel_f1']-base['micro']['pixel_f1']))
    summary = dict(dataset=args.dataset, images=images, partial=bool(args.limit), config=config,
                   baseline=base, rows=rows, cpu_seconds_per_image=metrics.distribution(timing))
    write_json(dest / 'summary.json', summary)
    (dest / 'report.md').write_text(markdown(summary), encoding='utf-8')
    print(dest / 'report.md', flush=True)


def freeze(args):
    if args.dataset not in ('second_cv', 'levir_val') or args.limit:
        raise ValueError('Freeze requires complete SECOND/LEVIR validation; test/smoke cannot select')
    summary = read_json(args.out / 'pool' / args.dataset / 'summary.json')
    if (summary['partial'] or summary['images'] != image_names(args.dataset)
            or summary['config']['status'] != 'validation_candidates'
            or summary['config']['geometry']['partial']):
        raise ValueError('Incomplete or incompatible validation summary')
    scores = {m: summary['rows'][f'U5_x{m}']['evaluation']['micro'] for m in (4, 8, 16)}
    # Only pixel F1/IoU select; deterministic exact ties use the prespecified order.
    selected = min(scores, key=lambda m: (-scores[m]['pixel_f1'], -scores[m]['pixel_iou'], m))
    row = dict(validation_dataset=args.dataset, partial=False, multiplier=selected,
               threshold=selected*summary['config']['geometry']['median_area'],
               geometry=summary['config']['geometry'], selection_metrics=['pixel_f1', 'pixel_iou'],
               tie_rule='smaller multiplier only on exact F1 and IoU tie',
               validation_scores={m: {k: v[k] for k in ('pixel_f1', 'pixel_iou')} for m, v in scores.items()})
    path = args.out / 'frozen' / f'{args.dataset}.json'
    if path.is_file() and read_json(path) != json.loads(json.dumps(row)):
        raise ValueError(f'{path}: a different freeze already exists; do not overwrite it')
    write_json(path, row)
    print(path, flush=True)


def plot(args):
    if args.dataset != 'second' or args.limit:
        raise ValueError('test_759 plot requires the frozen SECOND test pool')
    name = 'test_759.png'
    dest = args.out / 'figures/test_759'
    dest.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(dest / '.matplotlib')
    import importlib.util
    source = WORKSPACE / 'wyh/exp6_staging/all_pieces/all_pieces.py'
    spec = importlib.util.spec_from_file_location('exp8_existing_panel', source)
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    row = read_json(args.out / 'pool/second/images/test_759.json')
    if row['config']['status'] != 'frozen_validation' or row['config']['partial']:
        raise ValueError('Plot needs the frozen, full-run configuration')
    path, spans = pool_index('second', [name])
    old = list(legacy.read_spans(path, spans[name]))
    old_candidates = len({(renderer.facet(r), r['cand_id']) for r in old})
    if len(old) != 1302 or old_candidates != 221:
        raise ValueError('test_759 legacy pool differs from the documented 1302/221 reference')
    target = next(r for r in old if r['piece_id'] == 619)
    t2, gt = common.load_rgb('second', name, 't2'), common.load_label('second', name)
    key = next(k for k in row['units'] if k.startswith('U5_'))
    for unit, filename in (('objects_direct_pairs', 'new_objects.png'), (key, 'new_pool.png')):
        objects, shape = load_units(args.out / 'pool/second' / unit / 'test_759.npz')
        renderer.panel(t2, full_masks(objects, shape), f'{name}  {unit}: {len(objects)} units',
                       dest / filename, gt=gt, target=target, rng=np.random.default_rng(0),
                       legend=[renderer.Patch(facecolor='#444444', ec='white', lw=2, label='GT contour'),
                               renderer.Patch(fill=False, ec='red', lw=3, label='audit piece 619')])
    counts = dict(legacy_all_pieces=len(old), legacy_all_candidates=old_candidates,
                  current_raw_candidates=row['input_objects'],
                  units={k: v['unit_count'] for k, v in row['units'].items()},
                  scope_note='Old reference has B/G/C1/C2/H1/pred; new pools use B/G0.05/H1 only.')
    write_json(dest / 'counts.json', counts)
    lines = ['# test_759 新旧池对照', '', '| 池 | 单元数 |', '|---|---:|',
             f'| 旧池（全部六来源）切块 | {len(old)} |', f'| 旧池（全部六来源）候选 | {old_candidates} |',
             f'| 本批原始 B+G0.05+H1 候选 | {row["input_objects"]} |']
    lines += [f'| {k} | {v["unit_count"]} |' for k, v in row['units'].items()]
    lines += ['', 'new_pool.png 是验证集冻结 U5；new_objects.png 是默认直接对合并对象。',
              '复用旧图绘制函数：t2、随机色半透明填充、黑轮廓、白色真值轮廓、红色 piece 619 框。',
              '旧池的六来源与本批三来源不同；U0 行提供相同来源的切块对照。', '']
    (dest / 'README.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('aggregate', 'freeze', 'plot'))
    parser.add_argument('--dataset', choices=DATASETS, required=True)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    {'aggregate': aggregate, 'freeze': freeze, 'plot': plot}[args.action](args)


if __name__ == '__main__':
    main()
