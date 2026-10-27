"""Cluster CIs, exact top-k unions and validation-frozen operating points."""
from exp8s7_common import *
from exp8s3_evaluate import histogram, cumulative, point
import csv
import time

SEEDS = (1020, 1021, 1022)


def bootstrap(z, score, take, replicates):
    y, values, ids = z['label'][take], score[take], z['image_id'][take]
    result = dict(positive=int((y == 1).sum()), negative=int((y == 0).sum()), **rank_metrics(y, values),
                  auc_ci95=None, ap_ci95=None, bootstrap_seed=20261026, bootstrap_replicates=replicates,
                  bootstrap_valid=0)
    if result['auc'] is None:
        return dict(result, status='single_class')
    rank = c.b40.Ranking(y, values)
    rng = np.random.default_rng(20261026)
    samples = []
    n = len(z['images'])
    for _ in range(replicates):
        weights = np.bincount(rng.integers(n, size=n), minlength=n)
        pair = rank.evaluate(weights[ids])
        if np.isfinite(pair).all():
            samples.append(pair)
    if samples:
        ci = np.percentile(samples, [2.5, 97.5], axis=0)
        result.update(auc_ci95=ci[:, 0].tolist(), ap_ci95=ci[:, 1].tolist())
    return dict(result, bootstrap_valid=len(samples), status='ok')


def ranking(z, scores, replicates):
    neg = z['label'] == 0
    # Rank negatives by D within the evaluated split; deterministic ties use image and unit IDs.
    ordered = np.flatnonzero(neg)
    ordered = ordered[np.lexsort((z['id'][ordered], z['image_id'][ordered], -z['dscore'][ordered]))]
    hard = np.zeros(len(neg), bool)
    hard[ordered[:int(np.ceil(len(ordered)*.2))]] = True
    two = (z['label'] == 1) & (z['majority'] == 2)
    subsets = dict(cell2=two | neg, all=z['label'] >= 0, hard=two | hard)
    return {m: {k: bootstrap(z, score, take, replicates) for k, take in subsets.items()}
            for m, score in scores.items()}


def targets(d):
    result = []
    slices = ndi.find_objects(d['gl'])
    for ident in sorted(s6.targets2(d)):
        box = slices[ident-1]
        result.append((box, d['gl'][box] == ident, int(d['ga'][ident]), int(d['gb'][ident])))
    return result


def field_for(units, values, shape):
    field = np.full(shape, -np.inf, np.float64)
    for u, v in zip(units, values):
        x0, y0, x1, y1 = u['bbox']
        view = field[y0:y1, x0:x1]
        view[u['mask']] = np.maximum(view[u['mask']], v)
    return field


def spatial(a, split, z, scores, frozen=None):
    hists = {m: [] for m in scores}
    budget = {m: {str(k): dict(hits2=0, objects2=0, selected=0) for k in KS} for m in scores}
    points = {m: {str(b): dict(added_tp=0, added_fp=0, added_fp_components=0, hits2=0)
                  for b in (.5, 1., 2.)} for m in scores}
    baseline = np.zeros(3, np.int64); totals = np.zeros(4, np.int64)
    object_total, basehits = 0, 0
    fixed_k = 10 if a.dataset == 'second' else 3
    per_image = dict(images=z['images'].tolist(), objects2=[],
                     methods={m: dict(budget_hits2=[], f1_hits2=[]) for m in scores})
    for i, name in enumerate(z['images']):
        d = s5.geometry(a.dataset, split, str(name))
        rows = image_rows(z, i)
        units = [unpack(z, j) for j in rows]
        gt, base = d['gt'], d['base']
        baseline += [(gt & base).sum(), (~gt & base).sum(), (gt & ~base).sum()]
        totals += [(d['cell'] == k).sum() for k in CELLS]
        ts = targets(d); object_total += len(ts)
        per_image['objects2'].append(len(ts))
        basehits += sum(2*inside >= area for _, _, area, inside in ts)
        for method, values in scores.items():
            v = values[rows]
            order = np.lexsort((z['id'][rows], -v))
            union = np.zeros_like(gt); last = 0
            for k in KS:
                stop = len(rows) if k == 'all' else min(k, len(rows))
                for j in order[last:stop]:
                    s5.add_mask(union, units[int(j)])
                stats = s5.coverage(d, union)
                b = budget[method][str(k)]
                b['hits2'] += stats['hits2']; b['objects2'] += stats['objects2']; b['selected'] += stop
                if k == fixed_k:
                    per_image['methods'][method]['budget_hits2'].append(int(stats['hits2']))
                last = stop
            field = field_for(units, v, gt.shape)
            if frozen is None:
                hist, _ = histogram(field, gt, base, d['cell'], ts)
                hists[method].append(hist)
            else:
                for key, row in frozen[method].items():
                    threshold = row['threshold']
                    added = np.zeros_like(gt) if threshold is None else field >= threshold
                    fp = added & ~gt
                    count = ndi.label(fp, s5.EIGHT)[1]
                    p = points[method][key]
                    p['added_tp'] += int((added & gt).sum()); p['added_fp'] += int(fp.sum())
                    p['added_fp_components'] += count
                    hits = int(s5.coverage(d, added)['hits2'])
                    p['hits2'] += hits
                    if key == '1.0':
                        per_image['methods'][method]['f1_hits2'].append(hits)
        print(f'eval {split}/{name}', flush=True)
    nimages = len(z['images'])
    for by_k in budget.values():
        for b in by_k.values():
            b.update(object_recall2=s5.fraction(b['hits2'], b['objects2']),
                     selected_per_image=b['selected']/nimages)
    curves = {}
    tp0, fp0, fn0 = map(int, baseline)
    basef = 2*tp0/max(1, 2*tp0+fp0+fn0)
    if frozen is None:
        for method in scores:
            curves[method] = cumulative(np.concatenate(hists[method]), baseline, totals, object_total, basehits)
    else:
        for method, by_b in points.items():
            for key, row in by_b.items():
                tp, fp, fn = tp0+row['added_tp'], fp0+row['added_fp'], fn0-row['added_tp']
                row.update(delta_f1=100*(2*tp/max(1, 2*tp+fp+fn)-basef),
                           object_recall2=s5.fraction(row['hits2'], object_total),
                           added_fp_components_per_image=row['added_fp_components']/nimages,
                           threshold=frozen[method][key]['threshold'])
    return dict(budget=budget, points=points, curves=curves, images=nimages,
                baseline=baseline.tolist(), objects2=object_total, per_image=per_image)


def choose(curves):
    result = {}
    for method, curve in curves.items():
        result[method] = {}
        for b in (.5, 1., 2.):
            feasible = np.flatnonzero(curve['delta_f1'] >= -b-1e-10)
            # Descending threshold order means equal recalls choose the highest threshold.
            at = feasible[np.argmax(np.nan_to_num(curve['object_recall2'][feasible], nan=0.))]
            result[method][str(b)] = point(curve, at)
    return result


def load_methods(a, z, split):
    methods = dict(D=z['dscore'], **{'M1-ign3': z['m1score']}, oracle=z['precision'])
    paths = {}
    for folder in a.scores:
        folder = Path(folder).resolve()
        if folder.is_relative_to(HERE / 'consult'):
            raise ValueError('consult/ is outside this task')
        name = folder.name
        if name in methods:
            raise ValueError(f'Duplicate method {name}')
        methods[name] = align_scores(folder / f'{split}.npz', z)
        paths[name] = str(folder)
    for prefix in a.paired_ref:
        for seed in SEEDS:
            name = f'{prefix}-s{seed}'
            if name not in methods:
                folder = a.out / a.dataset / 'scores' / name
                methods[name] = align_scores(folder / f'{split}.npz', z)
                paths[name] = str(folder.resolve())
    return methods, paths


def seed_groups(scores, require_complete=False):
    prefixes = sorted({name.rsplit('-s', 1)[0] for name in scores
                       if any(name.endswith(f'-s{seed}') for seed in SEEDS)})
    groups = {}
    for prefix in prefixes:
        names = [f'{prefix}-s{seed}' for seed in SEEDS]
        if all(name in scores for name in names):
            groups[prefix] = names
        elif require_complete:
            raise ValueError(f'{prefix}: paired evaluation requires all three seeds {SEEDS}')
    return groups


def metric_mean(values):
    return float(np.mean(values)) if all(v is not None for v in values) else None


def seed_means(result, groups):
    means = {}
    for prefix, names in groups.items():
        ranks = {subset: {metric: metric_mean([result['ranking'][m][subset][metric] for m in names])
                          for metric in ('auc', 'ap')} for subset in ('cell2', 'all', 'hard')}
        budgets = {k: {metric: metric_mean([result['budget'][m][k][metric] for m in names])
                       for metric in ('object_recall2', 'selected_per_image', 'hits2')}
                   for k in map(str, KS)}
        points = {b: {metric: metric_mean([result['points'][m][b][metric] for m in names])
                      for metric in ('object_recall2', 'delta_f1', 'added_fp', 'added_fp_components_per_image')}
                  for b in ('0.5', '1.0', '2.0')}
        means[prefix] = dict(seeds=list(SEEDS), ranking=ranks, budget=budgets, points=points,
                             aggregation='arithmetic mean of metrics, not score ensemble')
    return means


def paired_comparisons(a, z, scores, result, groups):
    if not a.paired_ref:
        return {}
    if not any(prefix not in a.paired_ref for prefix in groups):
        raise ValueError('--paired-ref requires at least one complete new-method seed group')
    take = cell2_selection(z)
    ids, y = z['image_id'][take], z['label'][take]
    names = [name for members in groups.values() for name in members]
    ranks = {name: c.b40.Ranking(y, scores[name][take]) for name in names} if len(np.unique(y)) == 2 else {}
    per_image = result['per_image']
    objects = np.asarray(per_image['objects2'])
    n = len(z['images'])
    hits = {name: np.asarray([per_image['methods'][name]['budget_hits2'],
                              per_image['methods'][name]['f1_hits2']]) for name in names}
    # All methods share the very same image draws, including empty candidate images.
    samples = {name: np.full((a.bootstrap, 3), np.nan) for name in names}
    rng = np.random.default_rng(20261026)
    for iteration in range(a.bootstrap):
        weights = np.bincount(rng.integers(n, size=n), minlength=n)
        total_objects = weights @ objects
        unit_weights = weights[ids]
        valid_ap = ranks and unit_weights[y == 1].sum() > 0 and unit_weights[y == 0].sum() > 0
        for name in names:
            if valid_ap:
                samples[name][iteration, 0] = ranks[name].evaluate(unit_weights)[1]
            if total_objects:
                samples[name][iteration, 1:] = hits[name] @ weights / total_objects
    k = str(10 if a.dataset == 'second' else 3)
    metric_keys = ('cell2_ap', 'budget_object_recall2', 'f1_object_recall2')
    def original(name):
        return [result['ranking'][name]['cell2']['ap'], result['budget'][name][k]['object_recall2'],
                result['points'][name]['1.0']['object_recall2']]
    comparisons = {}
    for prefix, members in groups.items():
        for ref in a.paired_ref:
            if prefix == ref:
                continue
            references = groups[ref]
            bootstrap_delta = np.mean([samples[m]-samples[r] for m, r in zip(members, references)], axis=0)
            metric_rows = {}
            for column, metric in enumerate(metric_keys):
                deltas = [None if original(m)[column] is None or original(r)[column] is None
                          else original(m)[column]-original(r)[column] for m, r in zip(members, references)]
                valid = bootstrap_delta[:, column]
                valid = valid[np.isfinite(valid)]
                metric_rows[metric] = dict(delta=metric_mean(deltas),
                    ci95=np.percentile(valid, [2.5, 97.5]).tolist() if len(valid) else None,
                    bootstrap_valid=len(valid), positive_gain_seeds=sum(d is not None and d > 0 for d in deltas),
                    seed_deltas={str(seed): delta for seed, delta in zip(SEEDS, deltas)})
            hit_changes = {}
            for m, r, seed in zip(members, references, SEEDS):
                difference = hits[m][0]-hits[r][0]
                hit_changes[str(seed)] = dict(net_hits=int(difference.sum()),
                    improved_images=int((difference > 0).sum()), worsened_images=int((difference < 0).sum()),
                    per_image_net_hits=difference.tolist())
            comparisons[f'{prefix} - {ref}'] = dict(method=prefix, reference=ref, k=int(k),
                metrics=metric_rows, budget_hit_changes=hit_changes, bootstrap_seed=20261026,
                bootstrap_replicates=a.bootstrap,
                f1_rule='validation-selected delta F1 >= -1 point; threshold fixed during resampling',
                uncertainty='image sampling conditional on the three trained models and frozen thresholds')
    return comparisons


def sample_metrics(a, split, z, scores):
    if a.sample_list is None:
        return dict(status='pending_exp6j_sample_list', inventory='sample_inventory.csv')
    with a.sample_list.open(newline='') as f:
        selected = {(r['image'], int(r['id'])) for r in csv.DictReader(f)
                    if r['dataset'] == a.dataset and r['split'] == split}
    keys = [(str(z['images'][i]), int(j)) for i, j in zip(z['image_id'], z['id'])]
    take = np.array([key in selected for key in keys])
    matched = {key for key in keys if key in selected}
    return dict(status='matched', requested=len(selected), matched=len(matched),
                absent_from_filtered_pool=len(selected-matched),
                methods={m: {group: bootstrap(z, v, take & mask, a.bootstrap)
                             for group, mask in dict(all=z['label'] >= 0, cell2=cell2_selection(z)).items()}
                         for m, v in scores.items()})


def table(result):
    lines = [f"# {result['dataset']} / {result['split']} 图像判断器评估", '',
             '单元排序采用按图整群自助法；预算召回为比例，ΔF1 为百分点。', '',
             '| 方法 | 子集 | AUC（95% CI） | AP（95% CI） |', '|---|---|---:|---:|']
    def display(row, key):
        value, ci = row[key], row[key+'_ci95']
        if value is None:
            return '无有效双类别样本'
        return f'{value:.4f}' + (f' [{ci[0]:.4f}, {ci[1]:.4f}]' if ci else '')
    for method, groups in result['ranking'].items():
        for group, row in groups.items():
            label = {'cell2': '② 正例 vs 补漏负例', 'all': '全部正例 vs 负例', 'hard': '② 正例 vs D 最高20%负例'}[group]
            lines.append(f"| {method} | {label} | {display(row, 'auc')} | {display(row, 'ap')} |")
    def number(value):
        return 'NA' if value is None else f'{value:.4f}'
    for method, summary in result['seed_means'].items():
        for group, row in summary['ranking'].items():
            lines.append(f"| {method} 三种子平均 | {group} | {number(row['auc'])} | {number(row['ap'])} |")
    lines.extend(['', '| 方法 | k | ② 对象召回 | 实际单元/图 |', '|---|---:|---:|---:|'])
    for method, budgets in result['budget'].items():
        for k, row in budgets.items():
            recall = row['object_recall2']
            recall_text = f'{recall:.4f}' if recall is not None else 'NA'
            lines.append(f"| {method} | {k} | {recall_text} | {row['selected_per_image']:.3f} |")
    for method, summary in result['seed_means'].items():
        for k, row in summary['budget'].items():
            lines.append(f"| {method} 三种子平均 | {k} | {number(row['object_recall2'])} | {number(row['selected_per_image'])} |")
    lines.extend(['', '| 方法 | 验证F1预算 | 阈值 | ΔF1 | ② 对象召回 | 新增误检连通域/图 |', '|---|---:|---:|---:|---:|---:|'])
    for method, budgets in result['points'].items():
        for k, row in budgets.items():
            recall = row['object_recall2']
            recall_text = f'{recall:.4f}' if recall is not None else 'NA'
            threshold_text = f"{row['threshold']:.6g}" if row['threshold'] is not None else '不接收'
            lines.append(f"| {method} | {k} | {threshold_text} | {row['delta_f1']:.4f} | {recall_text} | {row['added_fp_components_per_image']:.4f} |")
    for method, summary in result['seed_means'].items():
        for k, row in summary['points'].items():
            lines.append(f"| {method} 三种子平均 | {k} | 各种子独立冻结 | {number(row['delta_f1'])} | "
                         f"{number(row['object_recall2'])} | {number(row['added_fp_components_per_image'])} |")
    if result['paired']:
        lines.extend(['', '三种子平均为指标的算术平均。配对区间共用按图重采样；F1 约束点使用验证冻结阈值，重采样中不重新选阈值。', '',
                      '| 新方法 − 参考 | 指标 | 平均差（95% CI） | 正增益种子数/3 | 有效重采样 |',
                      '|---|---|---:|---:|---:|'])
        for label, comparison in result['paired'].items():
            for metric, row in comparison['metrics'].items():
                title = {'cell2_ap': '② AP', 'budget_object_recall2': f"k={comparison['k']} ②对象召回",
                         'f1_object_recall2': 'ΔF1≥−1 验证冻结点②对象召回'}[metric]
                ci = row['ci95']
                interval = f" [{ci[0]:.4f}, {ci[1]:.4f}]" if ci else ''
                lines.append(f"| {label} | {title} | {number(row['delta'])}{interval} | "
                             f"{row['positive_gain_seeds']} | {row['bootstrap_valid']} |")
        lines.extend(['', '低预算净增命中对象数及图像分布（逐图净变化见 JSON）：', '',
                      '| 新方法 − 参考 | 种子 | 净增命中对象 | 命中增加图数 | 命中减少图数 |', '|---|---:|---:|---:|---:|'])
        for label, comparison in result['paired'].items():
            for seed, row in comparison['budget_hit_changes'].items():
                lines.append(f"| {label} | {seed} | {row['net_hits']} | {row['improved_images']} | {row['worsened_images']} |")
    lines.extend(['', f"exp6j 同样本评估状态：{result['exp6j']['status']}。", ''])
    return '\n'.join(lines)


def main():
    p = parser(__doc__)
    p.add_argument('--scores', type=Path, nargs='*', default=[])
    p.add_argument('--report', default='comparison')
    p.add_argument('--bootstrap', type=int, default=2000)
    p.add_argument('--sample-list', type=Path)
    p.add_argument('--paired-ref', default='', help='Comma-separated three-seed prefixes, e.g. img,meta-m1,e1')
    p.add_argument('--final', action='store_true')
    a = setup(p)
    a.paired_ref = list(dict.fromkeys(name.strip() for name in a.paired_ref.split(',') if name.strip()))
    if any(Path(name).name != name for name in a.paired_ref):
        p.error('--paired-ref accepts method names, not paths')
    if a.bootstrap < 1 or (not a.limit and a.bootstrap != 2000):
        p.error('Full evaluation requires 2000 replicates')
    if a.final and a.limit:
        p.error('Smoke only evaluates validation')
    dest = local(a.out / a.dataset / 'evaluation' / a.report)
    split = 'test' if a.final else 'val'
    if (dest / f'{split}.json').exists():
        raise FileExistsError(f'{split} already evaluated in {dest}; preserve this report')
    started = time.monotonic()
    frozen_doc = c.read_json(dest / 'frozen.json') if a.final else None
    if frozen_doc and frozen_doc['limit'] != a.limit:
        raise ValueError('Frozen validation scope differs')
    z = load_units(a, split)
    scores, paths = load_methods(a, z, split)
    groups = seed_groups(scores, require_complete=bool(a.paired_ref))
    if frozen_doc and paths != frozen_doc['score_directories']:
        raise ValueError('Final methods must match the validation freeze')
    ranks = ranking(z, scores, a.bootstrap)
    if a.final:
        stats = spatial(a, split, z, scores, frozen_doc['methods'])
    else:
        stats = spatial(a, split, z, scores)
        curves = stats.pop('curves')
        for method, curve in curves.items():
            save(dest / 'curves' / 'val' / f'{method}.npz', **curve)
        frozen = choose(curves)
        write(dest / 'frozen.json', dict(dataset=a.dataset, limit=a.limit, methods=frozen, score_directories=paths,
              recall_unit='percent (batch43 curve convention)',
              selection='validation maximum cell2 object recall under delta F1 >= -budget; highest threshold tie'))
        frozen_stats = spatial(a, split, z, scores, frozen)
        stats.update(points=frozen_stats['points'], per_image=frozen_stats['per_image'])
    stats.pop('curves', None)
    result = dict(dataset=a.dataset, split=split, partial=bool(a.limit), ranking=ranks, **stats,
                  exp6j=sample_metrics(a, split, z, scores), seconds=time.monotonic()-started,
                  hard_negative_rule='top ceil(20% of negatives) per evaluated split by D; ties image_id/id',
                  score_directories=paths, object_recall_unit='fraction')
    result['seed_means'] = seed_means(result, groups)
    result['paired'] = paired_comparisons(a, z, scores, result, groups)
    result['seconds'] = time.monotonic()-started
    write(dest / f'{split}.json', result)
    (dest / f'{split}.md').write_text(table(result))
    with (dest / ('sample_inventory.csv' if split == 'val' else 'sample_inventory_test.csv')).open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['dataset', 'split', 'image', 'id', 'label', 'majority', *scores])
        for j in range(len(z['id'])):
            writer.writerow([a.dataset, split, z['images'][z['image_id'][j]], z['id'][j], z['label'][j],
                             z['majority'][j], *[v[j] for v in scores.values()]])
    print(dest / f'{split}.md', flush=True)


if __name__ == '__main__':
    main()
