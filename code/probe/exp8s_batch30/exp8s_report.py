"""仅读取公共底座，输出第 1、2 步全部曲线、来源表和图。"""
from __future__ import annotations

import csv
import itertools
from pathlib import Path

from exp8s_common import (np, DATASETS, LEVELS, SOURCES, DEFAULTS, VERSION, read_json, write_json,
                         names, level_key, load_objects, dedup, combined, put, scores)
from exp8s_metrics import Accumulator, image_stats, target_stats, ratio
from exp8s_prepare import require_reference


def pixels(path):
    with np.load(path, allow_pickle=False) as z:
        shape = tuple(z['shape'])
        return tuple(np.unpackbits(z[key], count=int(np.prod(shape))).reshape(shape).astype(bool)
                     for key in ('gt', 'base'))


def combination_key(selected):
    return '+'.join(s for s in SOURCES if s in selected)


def choose_order(combinations, available):
    """仅在验证集使用：最大单源增益开始，再比较每新增候选的增益。"""
    remaining = [s for s in SOURCES if s in available]
    first = min(remaining, key=lambda s: (-combinations[s]['dedup']['oracle_delta_f1_pp'], SOURCES.index(s)))
    order = [first]
    remaining.remove(first)
    while remaining:
        previous = combinations[combination_key(order)]['dedup']

        def priority(source):
            new = combinations[combination_key(order + [source])]['dedup']
            gain = new['oracle_delta_f1_pp'] - previous['oracle_delta_f1_pp']
            extra = new['candidates_mean'] - previous['candidates_mean']
            # 候选不增且收益增加优先；其余用至少 1/图像数的正预算分母。
            dominant = extra <= 0 and gain > 0
            slope = gain / max(extra, 1/new['images'])
            return -int(dominant), -slope, SOURCES.index(source)

        chosen = min(remaining, key=priority)
        order.append(chosen)
        remaining.remove(chosen)
    return order


def export_csv(path, rows):
    flat = [{k: v for k, v in row.items() if not isinstance(v, (dict, list))} for row in rows]
    fields = list(dict.fromkeys(key for row in flat for key in row))
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)


def evaluate_dataset(args, ds):
    root = args.out / 'interim/exp8s' / ds
    manifest = read_json(root / 'manifest.json')
    config = manifest['config']
    if config['version'] != VERSION or config['images'] != names(ds)[:args.limit or None] or config['partial'] != bool(args.limit):
        raise ValueError(f'{ds}: incompatible cache scope')
    available = [s for s in SOURCES if s != 'H1' or config['h1_available']]
    keys = [level_key(s, level) for s in available for level in LEVELS[s]]
    singles = {key: Accumulator() for key in keys}
    combinations = {combination_key(c): {mode: Accumulator() for mode in ('raw', 'dedup')}
                    for n in range(1, len(available)+1) for c in itertools.combinations(available, n)}
    discarded = {}
    source_totals = {s: dict(raw_candidates=0, candidates=0, unique_objects=0, unique_pixels=0,
                            allocated_after_cross_dedup=0.) for s in available}
    u5 = Accumulator() if ds in ('second_cv', 'second', 'sysu_val', 'sysu') else None
    f1 = scores(manifest['baseline'])[0]
    for index, name in enumerate(config['images'], 1):
        path = root / Path(name).stem
        info = read_json(path / 'image.json')
        if info['config'] != config:
            raise ValueError(f'{path}: mixed configuration')
        gt, base = pixels(path / 'pixels.npz')
        missed, _ = load_objects(path / 'missed')
        ns, _ = load_objects(path / 'no_signal')
        raw = {}
        for key in keys:
            objects, shape = load_objects(path / key)
            if shape != gt.shape:
                raise ValueError(f'{path}/{key}: cache geometry mismatch')
            raw[key] = objects
            singles[key].add(image_stats(dedup(objects, internal=True), gt, base, missed, ns, f1))
        defaults = {s: raw[level_key(s, DEFAULTS[s])] for s in available}
        # 同一图内复用内部去重结果；跨来源每种组合独立调用 D1。
        internal = {s: dedup(defaults[s], internal=True) for s in available}
        for key, modes in combinations.items():
            selected = key.split('+')
            original = [o for s in selected for o in defaults[s]]
            merged = dedup([o for s in selected for o in internal[s]])
            for mode, objects in [('raw', original), ('dedup', merged)]:
                modes[mode].add(image_stats(objects, gt, base, missed, ns, f1))
        unions, hits = {}, {}
        for s in available:
            union = np.zeros(gt.shape, bool)
            for obj in internal[s]:
                put(union, obj)
            unions[s] = union
            hits[s] = set(target_stats(internal[s], missed, union)['hit_ids'])
        all_merged = dedup([o for s in available for o in internal[s]])
        for s in available:
            other_union = np.zeros(gt.shape, bool)
            other_hits = set()
            for other in available:
                if other != s:
                    other_union |= unions[other]
                    other_hits |= hits[other]
            target = source_totals[s]
            target['raw_candidates'] += len(defaults[s])
            target['candidates'] += len(internal[s])
            target['unique_objects'] += len(hits[s] - other_hits)
            target['unique_pixels'] += int((unions[s] & ~other_union & gt & ~base).sum())
            target['allocated_after_cross_dedup'] += sum(1/len(o['sources']) for o in all_merged if s in o['sources'])
        if u5 is not None:
            objects, _ = load_objects(path / 'U5')
            u5.add(image_stats(objects, gt, base, missed, ns, f1))
        for key, row in info['dropped'].items():
            dst = discarded.setdefault(key, {k: 0 for k in row})
            for field in row:
                dst[field] += row[field]
        print(f'evaluate {ds} {index}/{len(config["images"])} {name}', flush=True)
    summary = dict(version=VERSION, dataset=ds, split='val' if ds in ('second_cv', 'sysu_val') else 'test',
                   partial=bool(args.limit), images=config['images'], baseline=manifest['baseline'],
                   available_sources=available, missing_sources={} if config['h1_available'] else {'H1': manifest['h1_status']},
                   missing_h1_files=manifest['h1_missing'], c2=config['c2'], defaults=DEFAULTS,
                   step1={key: accum.finish(manifest['baseline']) for key, accum in singles.items()},
                   combinations={key: {mode: acc.finish(manifest['baseline']) for mode, acc in modes.items()}
                                 for key, modes in combinations.items()}, discarded=discarded,
                   u5=u5.finish(manifest['baseline']) if u5 is not None else None)
    dest = args.out / 'results' / ds
    dest.mkdir(parents=True, exist_ok=True)
    write_json(dest / 'step1.json', {k: summary[k] for k in
               ('dataset', 'split', 'partial', 'images', 'step1', 'u5', 'discarded', 'missing_sources', 'c2')})
    export_csv(dest / 'step1.csv', [dict(source=s, level=level, **summary['step1'][level_key(s, level)],
                                      **discarded[level_key(s, level)]) for s in available for level in LEVELS[s]])
    print(f'step1 complete: {dest / "step1.json"}; now determine step2 order', flush=True)
    if ds in ('second_cv', 'sysu_val'):
        order = choose_order(summary['combinations'], available)
        order_origin = ds + ' validation greedy marginal gain per added candidate'
        write_json(args.out / 'orders' / (ds + '.json'), dict(images=config['images'], partial=bool(args.limit),
                                                            order=order, defaults=DEFAULTS))
    elif ds in ('second', 'sysu'):
        validation = 'second_cv' if ds == 'second' else 'sysu_val'
        frozen = read_json(args.out / 'orders' / (validation + '.json'))
        if frozen['partial'] != bool(args.limit) or frozen['images'] != names(validation)[:args.limit or None]:
            raise ValueError('Validation order scope mismatch')
        order = [s for s in frozen['order'] if s in available]
        # 如 SYSU val 缺 H1、test 有 H1，将未选过的来源按预定顺序追加，不用 test 排序。
        order += [s for s in available if s not in order]
        order_origin = validation + ' validation order; extra available sources appended in B,G,H1,C order'
    else:
        order = available
        order_origin = 'prespecified B,G,H1,C; descriptive test only, no selection'
    summary['order'], summary['order_origin'] = order, order_origin
    summary['step2'] = [dict(sources=order[:n], **summary['combinations'][combination_key(order[:n])])
                        for n in range(1, len(order)+1)]
    all_score = summary['combinations'][combination_key(available)]['dedup']['oracle_delta_f1_pp']
    for s, row in source_totals.items():
        without = combination_key([x for x in available if x != s])
        remaining_score = summary['combinations'][without]['dedup']['oracle_delta_f1_pp'] if without else 0.
        row.update(source=s, candidates_mean=row['candidates']/len(config['images']),
                   internal_duplicate_fraction=ratio(row['raw_candidates']-row['candidates'], row['raw_candidates']),
                   cross_duplicate_fraction=ratio(row['candidates']-row['allocated_after_cross_dedup'], row['candidates']),
                   total_duplicate_fraction=ratio(row['raw_candidates']-row['allocated_after_cross_dedup'], row['raw_candidates']),
                   removal_delta_f1_drop_pp=all_score-remaining_score)
    summary['source_table'] = list(source_totals.values())
    write_json(dest / 'summary.json', summary)
    export_csv(dest / 'step2.csv', [dict(step=i, sources='+'.join(row['sources']), mode=mode, **row[mode])
                                  for i, row in enumerate(summary['step2'], 1) for mode in ('raw', 'dedup')])
    export_csv(dest / 'source_table.csv', summary['source_table'])
    export_csv(dest / 'discarded.csv', [dict(variant=k, **v) for k, v in discarded.items()])
    if summary['u5'] is not None:
        export_csv(dest / 'u5.csv', [dict(unit='U5_x4', **summary['u5'], **discarded['U5'])])
    plot(args.out, summary)
    (dest / 'report.md').write_text(markdown(summary), encoding='utf-8')
    return summary


def plot(out, summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figdir = out / 'figures/exp8s' / summary['dataset']
    figdir.mkdir(parents=True, exist_ok=True)
    title = f'{summary["dataset"]} | {summary["split"]} | ' + ('SMOKE' if summary['partial'] else 'descriptive curves')
    axis_labels = {'candidates_mean': 'Mean candidates per image (D1 objects; U5 units)',
                   'area_fraction': 'Sum of candidate added areas / image pixels (overlap counted)',
                   'R_obj_05': 'Missed-object recall at IoU >= 0.5',
                   'oracle_delta_f1_pp': 'Oracle micro F1 gain (percentage points, F/2 rule)'}
    for x in ('candidates_mean', 'area_fraction'):
        for y in ('R_obj_05', 'oracle_delta_f1_pp'):
            fig, ax = plt.subplots(figsize=(8, 5))
            for s in summary['available_sources']:
                rows = [(level, summary['step1'][level_key(s, level)]) for level in LEVELS[s]]
                rows = [(level, row) for level, row in rows if row[y] is not None]
                rows.sort(key=lambda pair: pair[1][x])
                ax.plot([r[x] for _, r in rows], [r[y] for _, r in rows], marker='o', label=s)
                for level, row in rows:
                    ax.annotate(str(level) if level is not None else 'all', (row[x], row[y]), fontsize=7)
            if summary['u5'] is not None and summary['u5'][y] is not None:
                row = summary['u5']
                ax.axhline(row[y], color='grey', linestyle=':', label='Frozen U5 x4 reference')
                ax.scatter([row[x]], [row[y]], color='black', marker='*', s=100)
            ax.set(title=title, xlabel=axis_labels[x], ylabel=axis_labels[y])
            ax.legend(); ax.grid(alpha=.2)
            fig.tight_layout(); fig.savefig(figdir / f'step1_{x}_{y}.png', dpi=160); plt.close(fig)
            fig, ax = plt.subplots(figsize=(8, 5))
            for mode, label_text in [('raw', 'Raw union (no dedup)'), ('dedup', 'D1 within then across sources')]:
                points = [(r[mode][x], r[mode][y], '+'.join(r['sources'])) for r in summary['step2']
                          if r[mode][y] is not None]
                ax.plot([p[0] for p in points], [p[1] for p in points], marker='o', label=label_text)
                for px, py, sources in points:
                    ax.annotate(sources, (px, py), fontsize=7)
            for row in summary['step2']:
                if row['raw'][y] is not None and row['dedup'][y] is not None:
                    ax.annotate('', xy=(row['dedup'][x], row['dedup'][y]),
                                xytext=(row['raw'][x], row['raw'][y]), arrowprops=dict(arrowstyle='->', color='grey'))
            ax.set(title=title, xlabel=axis_labels[x], ylabel=axis_labels[y])
            ax.legend(); ax.grid(alpha=.2)
            fig.tight_layout(); fig.savefig(figdir / f'step2_{x}_{y}.png', dpi=160); plt.close(fig)


def fmt(value):
    return '—' if value is None else f'{value:.6f}'


def markdown(s):
    lines = [f'# {s["dataset"]} 候选预算第 1、2 步', '',
             f'划分：{s["split"]}；图像：{len(s["images"])}；冒烟：{s["partial"]}。',
             '比例指标为 0–1，F1/IoU 增益为百分点；测试结果只作描述，未选择工作点。',
             f'C2：{s["c2"]["policy"]}；验证来源 {s["c2"]["validation_dataset"]}；切点 {s["c2"]["cut"]}；'
             f'跨数据集迁移：{s["c2"]["transfer"]}。',
             f'阶梯顺序：{" → ".join(s["order"])}；依据：{s["order_origin"]}。',
             'F/2 中 F 为当前划分的检测器 micro F1，固定一次，不在加入候选后更新。',
             '面积横轴累加候选面积，重叠重复计数；另有 union_area_fraction 供核对。',
             '漏检对象为按 exp4 语义目标拆分后的 FN 8 连通域（≥20）；无信号对象保留 exp4 原始 GT 区域。',
             'Frag 以 IoU≥0.5 命中的对象为分母，统计覆盖该对象至少10%面积的候选数。', '']
    if s['missing_sources']:
        lines += [f'缺失来源：{s["missing_sources"]}。相关曲线和组合全部跳过；完整缺失文件见 JSON。', '']
    lines += ['| 来源档位 | 每图候选 | P90 | 面积占比 | R_obj@0.5 | AR_obj | R_pix | ΔF1 | ΔIoU | exp8 ΔF1 | Frag | Pos% |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for key, row in s['step1'].items():
        fields = ('candidates_mean', 'candidates_p90', 'area_fraction', 'R_obj_05', 'AR_obj', 'R_pix',
                  'oracle_delta_f1_pp', 'oracle_delta_iou_pp', 'exp8_delta_f1_pp', 'Frag')
        pos = row['Pos_fraction']
        lines.append(f'| {key} | ' + ' | '.join(fmt(row[k]) for k in fields) + f' | {fmt(None if pos is None else 100*pos)} |')
    if s['u5']:
        lines += ['', f'U5 ×4 对照：每图 {fmt(s["u5"]["candidates_mean"])} 个保留单元，'
                  f'ΔF1 {fmt(s["u5"]["oracle_delta_f1_pp"])}。原单元数、<20丢弃数见 discarded.csv。']
    lines += ['', '| 来源 | 原始候选 | 内部去重候选 | 独有命中对象 | 独有 FN 像素 | 跨源重复率 | 移除后 ΔF1 下降 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for row in s['source_table']:
        lines.append(f'| {row["source"]} | ' + ' | '.join(fmt(row[k]) for k in
                     ('raw_candidates', 'candidates', 'unique_objects', 'unique_pixels',
                      'cross_duplicate_fraction', 'removal_delta_f1_drop_pp')) + ' |')
    first, last = s['step2'][0]['dedup'], s['step2'][-1]
    loss = last['raw']['oracle_delta_f1_pp'] - last['dedup']['oracle_delta_f1_pp']
    lines += ['', '## 本批可回答的问题', '',
              f'默认全集内部及跨源去重使每图候选数从 {fmt(last["raw"]["candidates_mean"])} '
              f'变为 {fmt(last["dedup"]["candidates_mean"])}；oracle ΔF1 损失 {fmt(loss)} 个百分点。',
              f'默认阶梯起点 ΔF1 为 {fmt(first["oracle_delta_f1_pp"])}；终点为 '
              f'{fmt(last["dedup"]["oracle_delta_f1_pp"])}。来源独有贡献见上表，全部档位见 JSON/CSV。',
              '曲线形状、像素/对象召回及碎片差异按图和表描述；不从测试数字宣布选择。',
              'D2、r0 对比、全局预算、80%/90%预算和真实评分器结论属于第3–6步，本批尚未运行。',
              'SECOND 验证/测试检测器不同；SYSU 验证有32个原图组且权重可能用过官方val；WHU切点来自LEVIR。', '']
    return '\n'.join(lines)


def evaluate(args):
    require_reference(args)
    (args.out / 'completed.json').unlink(missing_ok=True)
    summaries = {}
    # 先验证冻结顺序，再描述测试；SYSU test 的 H1 可作为预定的最后一步。
    for ds in ('second_cv', 'sysu_val', 'second', 'sysu', 'levir', 'whu'):
        if ds in getattr(args, 'datasets', DATASETS):
            summaries[ds] = evaluate_dataset(args, ds)
    lines = ['# 实验8补充：候选预算第1、2步', '',
             f'冒烟：{bool(args.limit)}。本报告只在历史一致性核对通过后生成。',
             '各数据集完整指标、无信号子集、丢弃数及缺失来源见同目录子报告和 JSON/CSV。', '']
    for ds, s in summaries.items():
        lines.append(f'- [{ds}]({ds}/report.md)：{len(s["images"])} 张；来源 {", ".join(s["available_sources"])}。')
    lines += ['', '本批仅覆盖第1、2步及后续共用缓存和r1函数。未训练、未调用外部模型、未重算SAM3/AnyChange。', '']
    (args.out / 'results/exp8s_candidate_budget.md').write_text('\n'.join(lines))
    write_json(args.out / 'completed.json', dict(version=VERSION, partial=bool(args.limit), datasets=list(summaries)))
