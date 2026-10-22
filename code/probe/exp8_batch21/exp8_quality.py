"""SYSU step-2 quality, training candidates and validation-only cleaning selection."""
from __future__ import annotations

import argparse
import json
import time
from contextlib import ExitStack
from pathlib import Path

from exp8_batch21_common import (FLAGS, LABELS, MAIN, RULES, arguments, batch20_module,
                                 checked_units, configure, data, names, oracle, provenance,
                                 read_pool, rt, score, selected_names, source, write_fixed)

quality20 = batch20_module('exp8_quality')
unit_flags, keys, pct = quality20.unit_flags, quality20.keys, quality20.pct


def quality_config(args, dataset):
    return dict(revision=1, dataset=dataset, partial=bool(args.limit), flags=list(FLAGS),
                fragment='max pairwise intersection / own area > 0.5', keys=keys(dataset),
                rules=RULES, pool_root=str(args.batch19), provenance=provenance(args))


def check_definition(args):
    row = rt.read_json(args.out / 'cleaning/candidates.json')
    if (row['rules'] != RULES or row['partial'] != bool(args.limit)
            or row['training_images'] != names('pool_train')[:args.limit or None]
            or row['main_unit'] != MAIN['pool_train'] or row['provenance'] != provenance(args)
            or row['selection_metrics'] != ['pixel_f1', 'pixel_iou']):
        raise ValueError('Define candidates from the matching training run before validation')
    return row


def quality_row(args, dataset, name):
    row = rt.read_json(args.out / 'quality' / dataset / 'images' / f'{Path(name).stem}.json')
    if row['image'] != name or row['config'] != quality_config(args, dataset):
        raise ValueError(f'{dataset}/{name}: incompatible quality row')
    return row


def summary(args, dataset):
    row = rt.read_json(args.out / 'quality' / dataset / 'summary.json')
    if (row['dataset'] != dataset or row['images'] != names(dataset)[:args.limit or None]
            or row['partial'] != bool(args.limit) or row['config'] != quality_config(args, dataset)
            or row['main_unit'] != MAIN[dataset]):
        raise ValueError(f'{dataset}: need complete matching quality summary')
    return row


def analyze(args):
    if args.dataset == 'pool_val':
        check_definition(args)
    images = selected_names(args)
    config = quality_config(args, args.dataset)
    for number, name in enumerate(images, 1):
        path = args.out / 'quality' / args.dataset / 'images' / f'{Path(name).stem}.json'
        if path.exists():
            quality_row(args, args.dataset, name)
            continue
        start = time.perf_counter()
        pool = read_pool(args, name)
        gt = rt.common.load_label(source(args.dataset), name)
        base = rt.prediction(source(args.dataset), name, args.batch19)
        if gt.shape != base.shape:
            raise ValueError(f'{name}: prediction/label shape mismatch')
        result = dict(config=config, image=name, baseline=pool['baseline'], units={})
        for key in keys(args.dataset):
            objects, shape, details = checked_units(args, name, key, pool)
            if shape != gt.shape:
                raise ValueError(f'{name}/{key}: wrong mask shape')
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
                raise ValueError(f'{name}/{key}: oracle differs from batch19 pool')
            groups = {flag: [i for i, f in enumerate(flags) if f[flag]] for flag in FLAGS}
            groups.update({rule: [i for i, f in enumerate(flags) if any(f[tag] for tag in tags)]
                           for rule, tags in RULES.items()})
            unit = dict(total=len(objects), labels={label: sum(d['label'] == label for d in details)
                                                   for label in LABELS},
                        full=pool['units'][key]['metrics'], groups={})
            evaluations = {(): unit['full']}
            for group, removed in groups.items():
                after = oracle(objects, details, base, removed)
                positive_ids = tuple(i for i in removed if details[i]['label'] == 'positive')
                if positive_ids not in evaluations:
                    evaluations[positive_ids] = score(after, gt, base, name)
                removed_labels = {label: sum(details[i]['label'] == label for i in removed) for label in LABELS}
                lost = full & ~after
                record = dict(count=len(removed), labels=removed_labels,
                              kept_labels={label: unit['labels'][label] - removed_labels[label] for label in LABELS},
                              lost_tp_pixels=int((lost & gt).sum()), lost_fp_pixels=int((lost & ~gt).sum()),
                              after=evaluations[positive_ids])
                if group in RULES and args.dataset == 'pool_train':
                    dropped = set(removed)
                    record['kept_supervised_ids'] = [i for i, d in enumerate(details)
                                                     if i not in dropped and d['label'] != 'middle']
                unit['groups'][group] = record
            result['units'][key] = unit
        result['seconds'] = time.perf_counter() - start
        rt.write_json(path, result)
        print(f'quality {args.dataset} {number}/{len(images)} {name}: {result["seconds"]:.2f}s', flush=True)


def aggregate(args):
    if args.dataset == 'pool_val':
        check_definition(args)
    for name in selected_names(args, sharded=False):
        quality_row(args, args.dataset, name)
    # The same aggregation, CSV columns and full metric tables as batch20.
    quality20.aggregate(args)
    path = args.out / 'quality' / args.dataset / 'report.md'
    text = path.read_text(encoding='utf-8')
    text = text.replace('LEVIR/WHU 表是测试池描述与反事实移除敏感性，不生成清洗池，也不选择规则。',
                        'SYSU pool_train 登记规则，pool_val 描述质量并选择规则；测试池保持不变。')
    path.write_text(text + '\n' + data.LIMITATION + '\n', encoding='utf-8')


def export_training_ids(args, train, rules, directory):
    files, counts = {}, {}
    with ExitStack() as stack:
        for rule in rules:
            dest = directory / rule
            dest.mkdir(parents=True, exist_ok=True)
            for key in keys('pool_train'):
                path = dest / f'{key}.jsonl'
                temporary = path.with_suffix('.jsonl.tmp')
                files[rule, key] = (path, temporary, stack.enter_context(temporary.open('w', encoding='utf-8')))
                counts[rule, key] = 0
        for name in train['images']:
            row = quality_row(args, 'pool_train', name)
            for rule in rules:
                for key in keys('pool_train'):
                    ids = row['units'][key]['groups'][rule]['kept_supervised_ids']
                    counts[rule, key] += len(ids)
                    handle = files[rule, key][2]
                    handle.write(json.dumps(dict(image=name, dataset='sysu_val', pool_split='pool_train',
                                                 unit=key, rule=rule, unit_ids=ids,
                                                 archive=str(args.batch19 / 'pool/sysu_val' / key
                                                             / f'{Path(name).stem}.npz'))) + '\n')
    for (rule, key), (path, temporary, _) in files.items():
        if counts[rule, key] != train['units'][key]['groups'][rule]['supervised_count']:
            raise ValueError(f'{key}/{rule}: exported IDs differ from training summary')
        temporary.replace(path)


def define(args):
    if args.dataset != 'pool_train':
        raise ValueError('Define only on SYSU pool_train')
    selected_names(args, sharded=False)
    train = summary(args, 'pool_train')
    directory = args.out / 'cleaning/training_ids'
    export_training_ids(args, train, RULES, directory)
    fields = ('count', 'fraction', 'labels', 'kept_total', 'kept_labels', 'supervised_count',
              'positive_rate', 'positive_rate_all_kept', 'loss_f1_pp', 'loss_iou_pp')
    write_fixed(args.out / 'cleaning/candidates.json',
                dict(rules=RULES, training_images=train['images'], partial=bool(args.limit),
                     main_unit=MAIN['pool_train'], provenance=provenance(args),
                     selection_metrics=['pixel_f1', 'pixel_iou'],
                     tie_rule='R1 then R2 then R3 on exact pixel F1 and IoU tie',
                     origin='prespecified task thresholds; registered on pool_train before cleaning validation',
                     training_candidates={key: {rule: {f: unit['groups'][rule][f] for f in fields}
                                                for rule in RULES} for key, unit in train['units'].items()},
                     training_ids={rule: {key: str(directory / rule / f'{key}.jsonl')
                                          for key in keys('pool_train')} for rule in RULES}))


def check_selection(args):
    row = rt.read_json(args.out / 'cleaning/selected.json')
    if (row['partial'] or row['rule'] not in RULES or row['flags'] != RULES[row['rule']]
            or row['selection_metrics'] != ['pixel_f1', 'pixel_iou'] or row['test_cleaned']
            or row['training_images'] != names('pool_train') or row['validation_images'] != names('pool_val')
            or row['provenance'] != provenance(args) or row['main_unit'] != MAIN['pool_val']):
        raise ValueError('Formal audit requires matching complete SYSU cleaning selection')
    return row


def select(args):
    if args.dataset != 'pool_val' or args.limit or args.shard != (0, 1):
        raise ValueError('Selection requires all 800 pool_val images, never smoke or test')
    definition = check_definition(args)
    train, val = summary(args, 'pool_train'), summary(args, 'pool_val')
    main = MAIN['pool_val']
    groups = val['units'][main]['groups']
    chosen = min(RULES, key=lambda k: (-groups[k]['after']['micro']['pixel_f1'],
                                      -groups[k]['after']['micro']['pixel_iou'], list(RULES).index(k)))
    export_training_ids(args, train, [chosen], args.out / 'cleaning/selected_training_ids')
    row = dict(rule=chosen, flags=RULES[chosen], partial=False, main_unit=main,
               provenance=provenance(args), selection_metrics=definition['selection_metrics'],
               validation_images=val['images'], training_images=train['images'],
               validation_scores={k: {m: groups[k]['after']['micro'][m] for m in definition['selection_metrics']}
                                  for k in RULES},
               validation_losses={key: {k: {m: unit['groups'][k][m] for m in ('loss_f1_pp', 'loss_iou_pp')}
                                        for k in RULES} for key, unit in val['units'].items()},
               training_after={key: {f: unit['groups'][chosen][f] for f in
                                    ('kept_total', 'kept_labels', 'supervised_count', 'positive_rate', 'positive_rate_all_kept')}
                               for key, unit in train['units'].items()},
               training_ids={key: str(args.out / 'cleaning/selected_training_ids' / chosen / f'{key}.jsonl')
                             for key in keys('pool_train')},
               tie_rule=definition['tie_rule'], test_cleaned=False)
    report = ['# SYSU 清洗选择', '', f'主单元：{main}；规则：{chosen}；移除：{", ".join(RULES[chosen])}。',
              '按 pool_val 的 oracle 像素 F1 降序、IoU 降序，仍并列按 R1/R2/R3 顺序。未设损失容限。',
              '完整池只作参照。损失 = 完整池 − 移除后的指标，单位为百分点；负损失表示移除后改善。',
              '训练监督正例率分母为保留的正+负单元；所有候选移除中间标签。', '',
              '| 候选 | 验证 F1 % | IoU % | F1 损失 pp | IoU 损失 pp | 训练监督数 | 正例率 % |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for rule in RULES:
        g, t = groups[rule], train['units'][main]['groups'][rule]
        report.append(f'| {rule} | {pct(g["after"]["micro"]["pixel_f1"])} | {pct(g["after"]["micro"]["pixel_iou"])}'
                      + f' | {g["loss_f1_pp"]:.4f} | {g["loss_iou_pp"]:.4f}'
                      + f' | {t["supervised_count"]} | {pct(t["positive_rate"])} |')
    report += ['', 'U0、直接对合并对象使用同一选中规则，详见 selected.json 及各质量报告。测试集不清洗。',
               data.LIMITATION, '']
    (args.out / 'cleaning/report.md').write_text('\n'.join(report), encoding='utf-8')
    write_fixed(args.out / 'cleaning/selected.json', row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('analyze', 'aggregate', 'define', 'select'))
    arguments(parser)
    args = parser.parse_args()
    configure(args)
    {'analyze': analyze, 'aggregate': aggregate, 'define': define, 'select': select}[args.action](args)


if __name__ == '__main__':
    main()
