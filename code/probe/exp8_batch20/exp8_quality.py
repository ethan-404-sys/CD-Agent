"""Step-2 quality tables, training-defined cleaning candidates and validation selection."""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from exp8_batch20_common import (FLAGS, LABELS, MAIN, RULES, arguments, checked_units,
                                names, oracle, ratio, read_pool, rt, score,
                                selected_names, source, write_fixed)


def unit_flags(objects, details):
    # Pairwise overlap / this unit's area; a union of several neighbors is not used.
    maximum = rt.np.zeros(len(objects), dtype=float)
    boxes = rt.np.asarray([o['bbox'] for o in objects]).reshape(-1, 4)
    for i, obj in enumerate(objects):
        x0, y0, x1, y1 = obj['bbox']
        hits = rt.np.flatnonzero((boxes[:, 0] < x1) & (boxes[:, 2] > x0)
                               & (boxes[:, 1] < y1) & (boxes[:, 3] > y0))
        for j in hits[hits > i]:
            other = objects[j]
            a0, b0, a1, b1 = other['bbox']
            left, top, right, bottom = max(x0, a0), max(y0, b0), min(x1, a1), min(y1, b1)
            overlap = int((obj['mask'][top-y0:bottom-y0, left-x0:right-x0]
                           & other['mask'][top-b0:bottom-b0, left-a0:right-a0]).sum())
            maximum[i] = max(maximum[i], overlap / obj['area'])
            maximum[j] = max(maximum[j], overlap / other['area'])
    result = []
    for obj, detail, overlap in zip(objects, details, maximum):
        x0, y0, x1, y1 = obj['bbox']
        area = obj['area']
        result.append(dict(tiny50=area < 50, tiny100=area < 100,
                           thin=min(x1-x0, y1-y0) <= 3 or area / ((x1-x0)*(y1-y0)) < .2,
                           fragment=bool(overlap > .5), middle=detail['label'] == 'middle'))
    return result


def keys(dataset):
    return list(dict.fromkeys((MAIN[dataset], 'U0', 'objects_direct_pairs')))


def definition_path(args):
    return args.out / 'cleaning/candidates.json'


def check_definition(args):
    definition = rt.read_json(definition_path(args))
    if (definition['rules'] != RULES or definition['training_images'] != names('second_cv_train')[:args.limit or None]
            or definition['partial'] != bool(args.limit)):
        raise ValueError('Cleaning candidates must be defined from this training run before validation')
    return definition


def analyze(args):
    if args.dataset == 'second_cv':
        check_definition(args)
    images = selected_names(args)
    dest = args.out / 'quality' / args.dataset / 'images'
    config = dict(revision=1, dataset=args.dataset, partial=bool(args.limit),
                  flags=list(FLAGS), fragment='max pairwise intersection / own area > 0.5',
                  keys=keys(args.dataset), rules=RULES if args.dataset.startswith('second_cv') else {},
                  pool_root=str((args.out if args.dataset == 'second_cv_train' else args.batch18).resolve()))
    for number, name in enumerate(images, 1):
        path = dest / f'{Path(name).stem}.json'
        if path.exists():
            saved = rt.read_json(path)
            if saved['config'] != config or saved['image'] != name:
                raise ValueError(f'{path}: incompatible quality checkpoint')
            continue
        start = time.perf_counter()
        pool = read_pool(args, name)
        gt = rt.common.load_label(source(args.dataset), name)
        base = rt.prediction(source(args.dataset), name, args.batch18)
        result = dict(config=config, image=name, baseline=pool['baseline'], units={})
        for key in keys(args.dataset):
            objects, shape, details = checked_units(args, name, key, pool)
            if shape != gt.shape:
                raise ValueError(f'{name}/{key}: wrong mask shape')
            # Recompute the labels from actual saved masks before quality analysis.
            for obj, detail in zip(objects, details):
                x0, y0, x1, y1 = obj['bbox']
                frac = float(gt[y0:y1, x0:x1][obj['mask']].mean())
                label = 'positive' if frac >= .5 else 'negative' if frac < .1 else 'middle'
                if frac != detail['gt_fraction'] or label != detail['label']:
                    raise ValueError(f'{name}/{key}: mask-derived label differs')
            flags = unit_flags(objects, details)
            full = oracle(objects, details, base)
            counts = rt.metrics.pixel_counts(full, gt)
            if any(counts[k] != pool['units'][key]['metrics']['counts'][k] for k in ('tp', 'fp', 'fn')):
                raise ValueError(f'{name}/{key}: original oracle differs from batch18 pool')
            groups = {flag: [i for i, f in enumerate(flags) if f[flag]] for flag in FLAGS}
            groups.update({rule: [i for i, f in enumerate(flags) if any(f[tag] for tag in tags)]
                           for rule, tags in config['rules'].items()})
            unit = dict(total=len(objects), labels={label: sum(d['label'] == label for d in details)
                                                   for label in LABELS}, full=pool['units'][key]['metrics'],
                        groups={})
            evaluations = {(): unit['full']}
            for group, removed in groups.items():
                after = oracle(objects, details, base, removed)
                positive_ids = tuple(i for i in removed if details[i]['label'] == 'positive')
                if positive_ids not in evaluations:
                    evaluations[positive_ids] = score(after, gt, base, name)
                removed_labels = {label: sum(details[i]['label'] == label for i in removed) for label in LABELS}
                lost = full & ~after
                kept = {label: unit['labels'][label] - removed_labels[label] for label in LABELS}
                record = dict(count=len(removed), labels=removed_labels, kept_labels=kept,
                              lost_tp_pixels=int((lost & gt).sum()), lost_fp_pixels=int((lost & ~gt).sum()),
                              after=evaluations[positive_ids])
                # IDs are enough to use the cleaned training set without rewriting any masks.
                if group in RULES:
                    dropped = set(removed)
                    record['kept_supervised_ids'] = [i for i, d in enumerate(details)
                                                     if i not in dropped and d['label'] != 'middle']
                unit['groups'][group] = record
            result['units'][key] = unit
        result['seconds'] = time.perf_counter() - start
        rt.write_json(path, result)
        print(f'quality {args.dataset} {number}/{len(images)} {name}: {result["seconds"]:.2f}s', flush=True)


def aggregate(args):
    images = selected_names(args, sharded=False)
    dest = args.out / 'quality' / args.dataset
    baseline, accum, config = [], {}, None
    for name in images:
        row = rt.read_json(dest / 'images' / f'{Path(name).stem}.json')
        if row['image'] != name or row['config']['partial'] != bool(args.limit):
            raise ValueError(f'{name}: mixed full/smoke quality rows')
        if config is None:
            config = row['config']
        if row['config'] != config:
            raise ValueError('Mixed quality configurations')
        baseline.append(row['baseline'])
        for key, unit in row['units'].items():
            a = accum.setdefault(key, dict(total=0, labels={label: 0 for label in LABELS}, full=[], groups={}))
            a['total'] += unit['total']
            a['full'].append(unit['full'])
            for label in LABELS:
                a['labels'][label] += unit['labels'][label]
            for group, record in unit['groups'].items():
                b = a['groups'].setdefault(group, dict(count=0, labels={label: 0 for label in LABELS},
                                                       lost_tp_pixels=0, lost_fp_pixels=0, after=[]))
                for field in ('count', 'lost_tp_pixels', 'lost_fp_pixels'):
                    b[field] += record[field]
                for label in LABELS:
                    b['labels'][label] += record['labels'][label]
                b['after'].append(record['after'])
    summary = dict(dataset=args.dataset, images=images, partial=bool(args.limit), config=config,
                   main_unit=MAIN[args.dataset], baseline=rt.metrics.aggregate(baseline), units={})
    for key, a in accum.items():
        a['full'] = rt.metrics.aggregate(a['full'])
        for b in a['groups'].values():
            b['after'] = rt.metrics.aggregate(b['after'])
            b['fraction'] = ratio(b['count'], a['total'])
            b['label_fractions'] = {label: ratio(b['labels'][label], b['count']) for label in LABELS}
            kept = {label: a['labels'][label] - b['labels'][label] for label in LABELS}
            b['kept_total'] = sum(kept.values())
            b['kept_labels'] = kept
            b['supervised_count'] = kept['positive'] + kept['negative']
            b['positive_rate'] = ratio(kept['positive'], b['supervised_count'])
            b['positive_rate_all_kept'] = ratio(kept['positive'], b['kept_total'])
            b['loss_f1_pp'] = 100 * (a['full']['micro']['pixel_f1'] - b['after']['micro']['pixel_f1'])
            b['loss_iou_pp'] = 100 * (a['full']['micro']['pixel_iou'] - b['after']['micro']['pixel_iou'])
        summary['units'][key] = a
    rt.write_json(dest / 'summary.json', summary)
    write_report(dest, summary)


def pct(value):
    return '—' if value is None else f'{100*value:.3f}'


def write_report(dest, summary):
    lines = [f'# {summary["dataset"]} 数据质量', '',
             f'图像数 {len(summary["images"])}；主单元 {summary["main_unit"]}；冒烟 {summary["partial"]}。', '',
             '正例 ≥50%，负例 <10%，中间标签 [10%,50%)。低质量类别可重叠，不可相加。',
             '碎片是最大两两交集/本单元面积 >50% 的几何代理，不推断其一定来自同一个物体。',
             'oracle 贡献 = 完整池指标 − 移除此类后指标；负损失表示移除后改善。'
             '重叠正单元仍能覆盖的像素不重复计入损失。全部比例为百分数，损失为百分点。', '',
             '| 单元 | 类别/规则 | 数量 | 占池 % | 正/负/中间 | oracle F1 损失 | IoU 损失 | 独有 TP/FP 像素 | 保留总数/监督数 | 监督正例率 % |',
             '|---|---|---:|---:|---|---:|---:|---|---|---:|']
    csv_rows = []
    for key, unit in summary['units'].items():
        full = unit['full']['micro']
        lines.append(f'| {key} | 完整池 | {unit["total"]} | 100 | '
                     + '/'.join(str(unit['labels'][k]) for k in LABELS)
                     + f' | 0 | 0 | 0/0 | {unit["total"]}/{unit["labels"]["positive"]+unit["labels"]["negative"]} | '
                     + pct(ratio(unit['labels']['positive'], unit['labels']['positive']+unit['labels']['negative'])) + ' |')
        for group, row in unit['groups'].items():
            lines.append(f'| {key} | {group} | {row["count"]} | {pct(row["fraction"])} | '
                         + '/'.join(str(row['labels'][k]) for k in LABELS)
                         + f' | {row["loss_f1_pp"]:.4f} | {row["loss_iou_pp"]:.4f}'
                         + f' | {row["lost_tp_pixels"]}/{row["lost_fp_pixels"]}'
                         + f' | {row["kept_total"]}/{row["supervised_count"]} | {pct(row["positive_rate"])} |')
            csv_rows.append(dict(unit=key, group=group, count=row['count'], fraction=row['fraction'],
                                 **row['labels'], **{f'{k}_fraction': row['label_fractions'][k] for k in LABELS},
                                 full_f1=full['pixel_f1'], full_iou=full['pixel_iou'],
                                 after_f1=row['after']['micro']['pixel_f1'], after_iou=row['after']['micro']['pixel_iou'],
                                 **{k: row[k] for k in ('loss_f1_pp', 'loss_iou_pp', 'lost_tp_pixels',
                                                       'lost_fp_pixels', 'kept_total', 'supervised_count', 'positive_rate')}))
    lines += ['', '## 附表：绝对指标与边界、目标指标（不参与选择）', '',
              '| 单元/移除类别 | F1 % | IoU % | BF2 % | BF5 % | Boundary IoU % | 目标 F1 % | 新增误检目标/图 | 逐图下降 % | 粗糙度 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    evaluations = {'detector': summary['baseline']}
    for key, unit in summary['units'].items():
        evaluations[f'{key}/full'] = unit['full']
        evaluations.update({f'{key}/{group}': row['after'] for group, row in unit['groups'].items()})
    for key, evaluation in evaluations.items():
        m = evaluation['micro']
        lines.append(f'| {key} | ' + ' | '.join(pct(m[k]) for k in
                     ('pixel_f1', 'pixel_iou', 'bf2_f1', 'bf5_f1', 'biou_diag', 'object_f1'))
                     + f' | {evaluation["counts"]["new_false_objects"]/evaluation["images"]:.3f}'
                     + f' | {pct(evaluation["delta_f1"]["decline_fraction"])} | {m["pred_roughness"]} |')
    lines += ['', '完整指标（含各边界容差、目标大小分桶、有害编辑率）见 summary.json。',
              'LEVIR/WHU 表是测试池描述与反事实移除敏感性，不生成清洗池，也不选择规则。', '']
    (dest / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    with (dest / 'quality.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)


def define(args):
    if args.dataset != 'second_cv_train':
        raise ValueError('Define candidates only from SECOND verifier_train')
    images = selected_names(args, sharded=False)
    summary = rt.read_json(args.out / 'quality/second_cv_train/summary.json')
    if summary['images'] != images or summary['partial'] != bool(args.limit):
        raise ValueError('Need complete training quality summary for this run')
    write_fixed(definition_path(args), dict(rules=RULES, training_images=images, partial=bool(args.limit),
                main_unit='U5_x4', selection_metrics=['pixel_f1', 'pixel_iou'],
                tie_rule='R1 then R2 then R3 on exact pixel F1 and IoU tie',
                origin='prespecified task thresholds; defined before validation; no test statistics',
                training_candidates={k: {field: value for field, value in v.items() if field != 'after'}
                                     for k, v in summary['units']['U5_x4']['groups'].items() if k in RULES}))


def select(args):
    if args.dataset != 'second_cv' or args.limit or args.shard != (0, 1):
        raise ValueError('Selection requires all 297 validation images, never smoke or test')
    definition = check_definition(args)
    train = rt.read_json(args.out / 'quality/second_cv_train/summary.json')
    val = rt.read_json(args.out / 'quality/second_cv/summary.json')
    if (train['partial'] or train['images'] != names('second_cv_train')
            or val['partial'] or val['images'] != names('second_cv')):
        raise ValueError('Selection requires complete 2671/297 train/validation summaries')
    groups = val['units']['U5_x4']['groups']
    chosen = min(RULES, key=lambda k: (-groups[k]['after']['micro']['pixel_f1'],
                                      -groups[k]['after']['micro']['pixel_iou'], list(RULES).index(k)))
    row = dict(rule=chosen, flags=RULES[chosen], partial=False, selection_metrics=definition['selection_metrics'],
               validation_images=val['images'], training_images=train['images'],
               validation_scores={k: {m: groups[k]['after']['micro'][m] for m in definition['selection_metrics']}
                                  for k in RULES},
               validation_losses={k: {m: groups[k][m] for m in ('loss_f1_pp', 'loss_iou_pp')} for k in RULES},
               training_after={key: {field: unit['groups'][chosen][field] for field in
                                    ('kept_total', 'kept_labels', 'supervised_count', 'positive_rate', 'positive_rate_all_kept')}
                               for key, unit in train['units'].items()},
               tie_rule=definition['tie_rule'], test_cleaned=False)
    write_fixed(args.out / 'cleaning/selected.json', row)
    dest = args.out / 'cleaning/training_ids'
    dest.mkdir(parents=True, exist_ok=True)
    # Keep references to immutable pool archives, no changed masks or validation/test exports.
    for key in keys('second_cv_train'):
        with (dest / f'{key}.jsonl').open('w', encoding='utf-8') as handle:
            import json
            for name in train['images']:
                record = rt.read_json(args.out / 'quality/second_cv_train/images' / f'{Path(name).stem}.json')
                ids = record['units'][key]['groups'][chosen]['kept_supervised_ids']
                handle.write(json.dumps(dict(image=name, unit=key, rule=chosen, unit_ids=ids,
                                             archive=str(args.out / 'pool/second_cv_train' / key / f'{Path(name).stem}.npz')))
                             + '\n')
    report = ['# SECOND 清洗选择', '', f'规则：{chosen}；移除：{", ".join(RULES[chosen])}。',
              '仅比较验证集像素 F1，精确并列时比较 IoU，再并列按 R1/R2/R3 顺序。未设损失容限。',
              '完整池是参照，不作为清洗候选。所有候选都移除中间标签；监督正例率分母仅含正、负例。', '',
              '| 候选 | 验证 F1 % | IoU % | F1 损失 pp | IoU 损失 pp | 训练监督数 | 正例率 % |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for key in RULES:
        g, t = groups[key], train['units']['U5_x4']['groups'][key]
        report.append(f'| {key} | {pct(g["after"]["micro"]["pixel_f1"])} | {pct(g["after"]["micro"]["pixel_iou"])}'
                      + f' | {g["loss_f1_pp"]:.4f} | {g["loss_iou_pp"]:.4f}'
                      + f' | {t["supervised_count"]} | {pct(t["positive_rate"])} |')
    report += ['', 'U0、直接对合并对象的同规则结果见 selected.json 和各质量报告。测试集保持原池。', '']
    (args.out / 'cleaning/report.md').write_text('\n'.join(report), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('analyze', 'aggregate', 'define', 'select'))
    arguments(parser)
    args = parser.parse_args()
    {'analyze': analyze, 'aggregate': aggregate, 'define': define, 'select': select}[args.action](args)


if __name__ == '__main__':
    main()
