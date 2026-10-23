"""实验 8 补充第 3、4 步：去重方式对比与不训练的排序规则（只读 batch30 数据底座）。

阶段（每个划分单独运行；stats/global 按图分片，各片只写自己的 jsonl）：
  stats      逐图：四种去重方式 × r1 每图 top-K；D1 上五种排序规则 × 每图 top-K；
             同时写出每个 D1 对象的规则分数与 Pos 标签，供全局 top-N 与 AP 使用。
  thresholds 汇总全部分片的对象分数，按“分数降序、图序号、对象序号”全局排序，
             得到每条规则、每个 N 下每张图应取的对象数（与每图 top-K 走同一代码路径）。
  global     逐图：按上一步的每图取数计算全局 top-N 的指标。
  merge      汇总全部分片，写 summary.json、CSV、图和报告。
指标定义、D1 和 Accumulator 全部复用 batch30；不训练、不调用外部模型。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, '/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch30')
from exp8s_common import (np, DEFAULTS, SOURCES, read_json, write_json, names, level_key,  # noqa: E402
                         load_objects, dedup, combined, put, scores, overlap)
from exp8s_metrics import Accumulator, image_stats  # noqa: E402
from exp8s_rank import r1_key  # noqa: E402

OUT = Path('/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe')
BASE = {'second_cv': OUT / 'exp8s_batch30', 'second': OUT / 'exp8s_batch30',
        'sysu_val': OUT / 'exp8s_batch30_sysu', 'sysu': OUT / 'exp8s_batch30_sysu'}
VALIDATION = {'second_cv': 'second_cv', 'second': 'second_cv', 'sysu_val': 'sysu_val', 'sysu': 'sysu_val'}
KS = (1, 2, 3, 5, 8, 12, 20, 30, 50, 80, None)
METHODS = ('none', 'D1', 'D2', 'D3')
RULES = ('r0', 'r0b', 'r1', 'r2', 'r3', 'r4')


def image_list(ds):
    images = read_json(BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')['config']['images']
    if images != names(ds):
        raise ValueError(f'{ds}: data base image list differs from the split')
    return images


def k_label(k):
    return 'all' if k is None else str(k)


def pixels(path):
    with np.load(path, allow_pickle=False) as z:
        shape = tuple(int(v) for v in z['shape'])
        gt, base = (np.unpackbits(z[k], count=int(np.prod(shape))).reshape(shape).astype(bool)
                    for k in ('gt', 'base'))
        return gt, base, z['probability'].astype(np.float32)


# ---------------------------------------------------------------- 去重
def direct_groups(objects, iou_thr, internal):
    """与 batch17 groups_for 的 direct_pairs 相同（候选对按 IoU 降序、每个候选最多合并一次、不传递），
    仅 IoU 阈值可调；包含条件仍为交集/小者 ≥ 0.8。internal=True 时同来源内比较。"""
    edges = []
    for i, a in enumerate(objects):
        for j in range(i + 1, len(objects)):
            b = objects[j]
            if not internal and a['source'] == b['source']:
                continue
            inter = overlap(a, b)
            if not inter:
                continue
            iou = inter / (a['area'] + b['area'] - inter)
            if iou >= iou_thr or inter / min(a['area'], b['area']) >= .8:
                edges.append((-iou, i, j))
    edges.sort()
    used, groups = set(), []
    for _, i, j in edges:
        if i not in used and j not in used:
            groups.append([i, j])
            used.update((i, j))
    groups.extend([i] for i in range(len(objects)) if i not in used)
    return sorted(groups)


def merge_group(objects, ids, keep_smallest):
    if keep_smallest:
        keep = min(ids, key=lambda i: (objects[i]['area'], i))
        base = objects[keep]
        obj = dict(bbox=list(base['bbox']), mask=base['mask'], area=base['area'])
    else:
        boxes = [objects[i]['bbox'] for i in ids]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        mask = np.zeros((y1 - y0, x1 - x0), bool)
        for i in ids:
            a, b, c, d = objects[i]['bbox']
            mask[b - y0:d - y0, a - x0:c - x0] |= objects[i]['mask']
        obj = dict(bbox=[x0, y0, x1, y1], mask=mask, area=int(mask.sum()))
    obj['sources'] = sorted({s for i in ids for s in objects[i]['sources']})
    src_scores = {}
    for i in ids:
        for s, v in objects[i]['src_scores'].items():
            src_scores[s] = max(src_scores.get(s, -np.inf), v)
    obj['src_scores'] = src_scores
    obj['source'] = '+'.join(obj['sources'])
    return obj


def apply_method(by_source, method):
    """by_source：各来源默认档原候选（每个已带 sources、src_scores）。"""
    pooled = [o for s in SOURCES if s in by_source for o in by_source[s]]
    if method == 'none':
        return list(pooled)
    iou_thr, keep_smallest = {'D1': (.5, False), 'D2': (.5, True), 'D3': (.3, False)}[method]
    internal = []
    for s in SOURCES:
        if s in by_source:
            objs = by_source[s]
            internal += [merge_group(objs, g, keep_smallest) if len(g) > 1 else objs[g[0]]
                         for g in direct_groups(objs, iou_thr, internal=True)]
    return [merge_group(internal, g, keep_smallest) if len(g) > 1 else internal[g[0]]
            for g in direct_groups(internal, iou_thr, internal=False)]


# ---------------------------------------------------------------- 排序
def annotate(objects, gt, prob):
    for o in objects:
        x0, y0, x1, y1 = o['bbox']
        o['a'] = int((gt[y0:y1, x0:x1] & o['mask']).sum())
        o['detector_mean'] = float(prob[y0:y1, x0:x1][o['mask']].mean())
    return objects


def percentile(values):
    """图内排名百分位（大值靠前得高分），平局取平均名次；单个对象记 1。"""
    values = np.asarray(values, np.float64)
    if len(values) <= 1:
        return np.ones(len(values))
    order = values.argsort(kind='stable')
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values))
    for v in np.unique(values):
        same = values == v
        ranks[same] = ranks[same].mean()
    return ranks / (len(values) - 1)


def rule_keys(objects, rule, priority, f1=None):
    """返回升序排序键（越小越靠前），元素可跨图比较（r4 的百分位只在图内有意义，已在报告注明）。"""
    if rule == 'r0':
        return [(-o['a'] / o['area'],) for o in objects]
    if rule == 'r0b':
        # 设计外补充的 ΔF1 上界：值得并入（精度 > F/2）的候选按新增真值像素降序在前，其余在后。
        return [(0, -o['a']) if o['a'] / o['area'] > f1 / 2 else (1, -o['a']) for o in objects]
    if rule == 'r1':
        return [r1_key(o) for o in objects]
    if rule == 'r2':
        return [(-o['detector_mean'],) for o in objects]
    if rule == 'r3':
        keys = []
        for o in objects:
            top = min(o['sources'], key=priority.index)
            keys.append((priority.index(top), -o['src_scores'][top]))
        return keys
    if rule == 'r4':
        has_h1 = [i for i, o in enumerate(objects) if 'H1' in o['src_scores']]
        rest = [i for i, o in enumerate(objects) if 'H1' not in o['src_scores']]
        value = np.zeros(len(objects))
        if has_h1:
            value[has_h1] = percentile([objects[i]['src_scores']['H1'] for i in has_h1])
        if rest:
            value[rest] = percentile([objects[i]['detector_mean'] for i in rest])
        return [(-float(v),) for v in value]
    raise ValueError(rule)


def ranked(objects, keys):
    order = sorted(range(len(objects)), key=lambda i: (keys[i], i))
    return [objects[i] for i in order], order


# ---------------------------------------------------------------- 漏检对象是否贴着检测器预测
def adjacency(missed, base):
    """漏检对象与检测器 0.5 掩码是否 8 邻接（对象外扩一像素后与掩码相交）。"""
    from scipy import ndimage
    padded = np.pad(base, 1)
    rows = []
    for t in missed:
        x0, y0, x1, y1 = t['bbox']
        canvas = np.zeros((y1 - y0 + 2, x1 - x0 + 2), bool)
        canvas[1:-1, 1:-1] = t['mask']
        grown = ndimage.binary_dilation(canvas, structure=np.ones((3, 3), bool))
        rows.append(bool((grown & padded[y0:y1 + 2, x0:x1 + 2]).any()))
    return rows


def best_iou(objects, targets):
    best = np.zeros(len(targets))
    for o in objects:
        for i, t in enumerate(targets):
            inter = overlap(o, t)
            if inter:
                best[i] = max(best[i], inter / (o['area'] + t['area'] - inter))
    return best


# ---------------------------------------------------------------- 逐图
def load_image(ds, name, priority):
    path = BASE[ds] / 'interim/exp8s' / ds / Path(name).stem
    gt, base, prob = pixels(path / 'pixels.npz')
    missed, _ = load_objects(path / 'missed')
    no_signal, _ = load_objects(path / 'no_signal')
    by_source = {}
    for s in SOURCES:
        objs, shape = load_objects(path / level_key(s, DEFAULTS[s]))
        if shape != gt.shape:
            raise ValueError(f'{path}: geometry mismatch')
        for o in objs:
            o['sources'] = [s]
            o['src_scores'] = {s: float(o['score'])}
            o['source'] = s
        by_source[s] = objs
    return gt, base, prob, missed, no_signal, by_source


def stat_row(objects, gt, base, missed, no_signal, f1):
    row = image_stats(objects, gt, base, missed, no_signal, f1)
    row['main'].pop('hit_ids', None)
    row['no_signal'].pop('hit_ids', None)
    return row


def shard_items(ds, shard, shards, limit):
    items = image_list(ds)[:limit or None]
    return [(i, n) for i, n in enumerate(items) if i % shards == shard]


def run_stats(args, ds, f1, priority):
    dest = args.out / ds / f'stats_{args.shard:03d}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.with_suffix('.tmp').open('w') as fh:
        for index, name in shard_items(ds, args.shard, args.shards, args.limit):
            gt, base, prob, missed, ns, by_source = load_image(ds, name, priority)
            row = dict(index=index, image=name, method={}, rule={}, check={})
            per_method = {}
            for method in METHODS:
                objs = annotate(apply_method(by_source, method), gt, prob)
                per_method[method] = objs
                order, _ = ranked(objs, rule_keys(objs, 'r1', priority))
                row['method'][method] = {k_label(k): stat_row(order[:k], gt, base, missed, ns, f1) for k in KS}
            # D1 与 batch30 原实现逐对象一致（几何与面积），否则中止。
            ref = combined({s: by_source[s] for s in SOURCES}, SOURCES)
            mine = per_method['D1']
            sig = lambda o: (tuple(o['bbox']), o['area'], o['mask'].tobytes())  # noqa: E731
            if sorted(map(sig, ref)) != sorted(map(sig, mine)):
                raise ValueError(f'{ds}/{name}: D1 differs from batch30 combined()')
            row['check']['d1_equal'] = True
            d1 = per_method['D1']
            objects_out = []
            keys = {rule: rule_keys(d1, rule, priority, f1) for rule in RULES}
            for rule in RULES:
                order, _ = ranked(d1, keys[rule])
                row['rule'][rule] = {k_label(k): stat_row(order[:k], gt, base, missed, ns, f1) for k in KS}
            for i, o in enumerate(d1):
                objects_out.append(dict(i=i, pos=bool(o['a'] / o['area'] > f1 / 2),
                                        keys={rule: list(keys[rule][i]) for rule in RULES}))
            row['objects'] = objects_out
            adj = adjacency(missed, base)
            hit = best_iou(d1, missed) >= .5
            row['adjacent'] = dict(objects=len(missed), adjacent=int(sum(adj)),
                                   adjacent_pixels=int(sum(t['area'] for t, a in zip(missed, adj) if a)),
                                   pixels=int(sum(t['area'] for t in missed)),
                                   hit_adjacent=int(sum(h and a for h, a in zip(hit, adj))),
                                   hit_isolated=int(sum(h and not a for h, a in zip(hit, adj))))
            fh.write(json.dumps(row) + '\n')
            print(f'stats {ds} {index} {name}', flush=True)
    dest.with_suffix('.tmp').replace(dest)


def read_rows(path, kind):
    rows = []
    for f in sorted((path).glob(f'{kind}_*.jsonl')):
        rows += [json.loads(l) for l in f.read_text().splitlines()]
    return sorted(rows, key=lambda r: r['index'])


def run_thresholds(args, ds):
    rows = read_rows(args.out / ds, 'stats')
    expected = len(image_list(ds)[:args.limit or None])
    if len(rows) != expected:
        raise ValueError(f'{ds}: {len(rows)} stats rows, expected {expected}')
    n_images = len(rows)
    result = dict(images=n_images, take={}, ap={}, ap_mean_image={})
    for rule in RULES:
        pooled = [(tuple(o['keys'][rule]), r['index'], o['i'], o['pos']) for r in rows for o in r['objects']]
        pooled.sort()
        labels = np.array([p[3] for p in pooled], bool)
        result['ap'][rule] = average_precision(labels)
        per_image = []
        for r in rows:
            objs = sorted(r['objects'], key=lambda o: (tuple(o['keys'][rule]), o['i']))
            ap = average_precision(np.array([o['pos'] for o in objs], bool))
            if ap is not None:
                per_image.append(ap)
        result['ap_mean_image'][rule] = float(np.mean(per_image)) if per_image else None
        result['take'][rule] = {}
        for k in KS:
            total = len(pooled) if k is None else min(len(pooled), k * n_images)
            counts = {}
            for _, index, _, _ in pooled[:total]:
                counts[index] = counts.get(index, 0) + 1
            result['take'][rule][k_label(k)] = counts
    write_json(args.out / ds / 'thresholds.json', result)


def average_precision(labels):
    if not labels.any():
        return None
    hits = np.cumsum(labels)
    precision = hits / np.arange(1, len(labels) + 1)
    return float(precision[labels].mean())


def run_global(args, ds, f1, priority):
    thr = read_json(args.out / ds / 'thresholds.json')
    dest = args.out / ds / f'global_{args.shard:03d}.jsonl'
    with dest.with_suffix('.tmp').open('w') as fh:
        for index, name in shard_items(ds, args.shard, args.shards, args.limit):
            gt, base, prob, missed, ns, by_source = load_image(ds, name, priority)
            d1 = annotate(apply_method(by_source, 'D1'), gt, prob)
            row = dict(index=index, image=name, rule={})
            for rule in RULES:
                order, _ = ranked(d1, rule_keys(d1, rule, priority, f1))
                row['rule'][rule] = {}
                for k in KS:
                    take = thr['take'][rule][k_label(k)].get(str(index), 0)
                    row['rule'][rule][k_label(k)] = stat_row(order[:take], gt, base, missed, ns, f1)
            fh.write(json.dumps(row) + '\n')
            print(f'global {ds} {index} {name}', flush=True)
    dest.with_suffix('.tmp').replace(dest)


# ---------------------------------------------------------------- 汇总
def accumulate(rows, getter):
    acc = Accumulator()
    for r in rows:
        acc.add(getter(r))
    return acc


def run_merge(args, ds, manifest):
    stats = read_rows(args.out / ds, 'stats')
    glob = read_rows(args.out / ds, 'global')
    expected = len(image_list(ds)[:args.limit or None])
    if len(stats) != expected or len(glob) != expected:
        raise ValueError(f'{ds}: incomplete shards ({len(stats)}, {len(glob)} of {expected})')
    if not all(r['check'].get('d1_equal') for r in stats):
        raise ValueError(f'{ds}: D1 check missing')
    baseline = manifest['baseline']
    thr = read_json(args.out / ds / 'thresholds.json')
    summary = dict(dataset=ds, split='val' if ds in ('second_cv', 'sysu_val') else 'test',
                   images=expected, partial=bool(args.limit), baseline=baseline,
                   priority=read_json(BASE[ds] / 'orders' / f'{VALIDATION[ds]}.json')['order'],
                   step3={}, step4_per_image={}, step4_global={}, ap=thr['ap'], ap_mean_image=thr['ap_mean_image'])
    for m in METHODS:
        summary['step3'][m] = {k_label(k): accumulate(stats, lambda r: r['method'][m][k_label(k)]).finish(baseline)
                               for k in KS}
    for rule in RULES:
        summary['step4_per_image'][rule] = {k_label(k): accumulate(stats, lambda r: r['rule'][rule][k_label(k)]).finish(baseline)
                                            for k in KS}
        summary['step4_global'][rule] = {k_label(k): accumulate(glob, lambda r: r['rule'][rule][k_label(k)]).finish(baseline)
                                         for k in KS}
    adj = {key: sum(r['adjacent'][key] for r in stats) for key in stats[0]['adjacent']}
    summary['missed_adjacency'] = adj
    full = summary['step4_per_image']['r1']['all']['oracle_delta_f1_pp']
    summary['budget_for_fraction'] = {}
    for scheme, table in [('r0_per_image', summary['step4_per_image']['r0'])] + \
            [(f'{r}_per_image', summary['step4_per_image'][r]) for r in RULES[1:]] + \
            [(f'{r}_global', summary['step4_global'][r]) for r in RULES]:
        out = {}
        for frac in (.8, .9):
            hit = next(((k, v['candidates_mean']) for k, v in table.items()
                        if v['oracle_delta_f1_pp'] >= frac * full), None)
            out[str(frac)] = None if hit is None else dict(k=hit[0], candidates_mean=hit[1])
        summary['budget_for_fraction'][scheme] = out
    summary['full_d1_oracle_delta_f1_pp'] = full
    write_json(args.out / ds / 'summary.json', summary)
    write_csv(args.out / ds, summary)
    plot(args.out / ds, summary)
    report(args.out / ds, summary)


COLUMNS = ('candidates_mean', 'candidates_p90', 'R_obj_03', 'R_obj_05', 'AR_obj', 'R_pix', 'Frag',
           'small_lt100_fraction', 'Pos_percent', 'oracle_delta_f1_pp', 'oracle_delta_iou_pp',
           'exp8_delta_f1_pp', 'exp8_delta_iou_pp', 'area_fraction', 'no_signal_R_obj_05')


def write_csv(dest, s):
    import csv
    for name, part, label in [('step3.csv', s['step3'], 'method'), ('step4_per_image.csv', s['step4_per_image'], 'rule'),
                              ('step4_global.csv', s['step4_global'], 'rule')]:
        with (dest / name).open('w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow([label, 'K', *COLUMNS])
            for key, table in part.items():
                for k, v in table.items():
                    w.writerow([key, k, *[v[c] for c in COLUMNS]])


def plot(dest, s):
    import os
    os.environ.setdefault('MPLCONFIGDIR', str(dest / '.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = ['SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    split = s['split']
    figs = dest / 'figures'
    figs.mkdir(exist_ok=True)
    for name, groups in [('step3', {m: s['step3'][m] for m in METHODS}),
                         ('step4', {**{f'{r} 每图': s['step4_per_image'][r] for r in RULES},
                                    **{f'{r} 全局': s['step4_global'][r] for r in RULES}})]:
        for metric, ylabel in [('oracle_delta_f1_pp', 'ΔF1_oracle（百分点，F/2 规则）'), ('R_obj_05', 'R_obj@0.5')]:
            fig, ax = plt.subplots(figsize=(7, 4.5))
            palette = plt.get_cmap('tab10').colors
            for label, table in groups.items():
                xs = [v['candidates_mean'] for v in table.values()]
                ys = [v[metric] if v[metric] is not None else np.nan for v in table.values()]
                key = label.split()[0]
                color = palette[(METHODS + RULES).index(key) % 10]
                ax.plot(xs, ys, marker='o', ms=3, label=label, color=color, ls='--' if '全局' in label else '-')
            ax.set_xscale('log')
            ax.set_xlabel('每图候选数（去重后，对数轴）')
            ax.set_ylabel(ylabel)
            ax.set_title(f'{s["dataset"]}（{split}）{"去重方式（r1 截取）" if name == "step3" else "排序规则（D1）"}')
            ax.grid(alpha=.3)
            ax.legend(fontsize=7, ncol=2)
            fig.tight_layout()
            fig.savefig(figs / f'{name}_{metric}.png', dpi=130)
            plt.close(fig)


def fmt(v, pct=False):
    if v is None:
        return '—'
    return f'{100 * v:.1f}' if pct else f'{v:.2f}'


def report(dest, s):
    lines = [f'# {s["dataset"]}（{s["split"]}）第 3、4 步', '',
             f'图像 {s["images"]}；冒烟 {s["partial"]}；r3 来源优先级 {"→".join(s["priority"])}；'
             f'D1 全集 oracle ΔF1 {s["full_d1_oracle_delta_f1_pp"]:.2f}。', '',
             '## 第 3 步（r1 截取每图前 K 个）', '',
             '| 方式 | K | 每图候选 | R_obj@0.5 % | R_pix % | ΔF1 | ΔIoU | Frag | <100 px % | Pos % |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for m in METHODS:
        for k, v in s['step3'][m].items():
            lines.append(f'| {m} | {k} | {v["candidates_mean"]:.1f} | {fmt(v["R_obj_05"], 1)} | {fmt(v["R_pix"], 1)} | '
                         f'{fmt(v["oracle_delta_f1_pp"])} | {fmt(v["oracle_delta_iou_pp"])} | {fmt(v["Frag"])} | '
                         f'{fmt(v["small_lt100_fraction"], 1)} | {v["Pos_percent"]:.1f} |' if v['Pos_percent'] is not None else
                         f'| {m} | {k} | 0 | — | — | 0 | 0 | — | — | — |')
    lines += ['', '## 第 4 步（D1 全集上排序）', '',
              '| 规则 | AP（全部对象混排） | AP（逐图平均） |', '|---|---:|---:|']
    for r in RULES:
        lines.append(f'| {r} | {fmt(s["ap"][r])} | {fmt(s["ap_mean_image"][r])} |')
    lines += ['', '| 规则 | 方式 | K/N | 每图候选 | R_obj@0.5 % | ΔF1 | 与 r0b 差 |', '|---|---|---|---:|---:|---:|---:|']
    for r in RULES:
        for mode in ('per_image', 'global'):
            for k, v in s[f'step4_{mode}'][r].items():
                gap = v['oracle_delta_f1_pp'] - s[f'step4_{mode}']['r0b'][k]['oracle_delta_f1_pp']
                lines.append(f'| {r} | {"每图" if mode == "per_image" else "全局"} | {k} | {v["candidates_mean"]:.1f} | '
                             f'{fmt(v["R_obj_05"], 1)} | {fmt(v["oracle_delta_f1_pp"])} | {gap:+.2f} |')
    lines += ['', '## 达到 D1 全集 ΔF1 80% / 90% 的最小网格点', '']
    for scheme, v in s['budget_for_fraction'].items():
        lines.append(f'- {scheme}：80% → {v["0.8"]}；90% → {v["0.9"]}')
    a = s['missed_adjacency']
    lines += ['', '## 漏检对象是否贴着检测器预测（8 邻接）', '',
              f'贴着预测：{a["adjacent"]}/{a["objects"]} 个对象，像素 {a["adjacent_pixels"]}/{a["pixels"]}；'
              f'D1 全集 IoU≥0.5 命中：贴着 {a["hit_adjacent"]}，孤立 {a["hit_isolated"]}。']
    (dest / 'report.md').write_text('\n'.join(lines) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('stats', 'thresholds', 'global', 'merge'))
    p.add_argument('--dataset', required=True, choices=tuple(BASE))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    args = p.parse_args()
    ds = args.dataset
    manifest = read_json(BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')
    if not manifest['config']['h1_available']:
        raise ValueError(f'{ds}: H1 unavailable in the data base')
    if args.limit:
        # 冒烟只用前 N 张：基线与 F/2 门槛都按这些图重算，与 batch30 的冒烟口径一致。
        rows = [read_json(BASE[ds] / 'interim/exp8s' / ds / Path(n).stem / 'image.json')['baseline']
                for n in image_list(ds)[:args.limit]]
        manifest = {**manifest, 'baseline': {k: sum(r[k] for r in rows) for k in ('tp', 'fp', 'fn')}}
    f1 = scores(manifest['baseline'])[0]
    priority = read_json(BASE[ds] / 'orders' / f'{VALIDATION[ds]}.json')['order']
    if args.action == 'stats':
        run_stats(args, ds, f1, priority)
    elif args.action == 'thresholds':
        run_thresholds(args, ds)
    elif args.action == 'global':
        run_global(args, ds, f1, priority)
    else:
        run_merge(args, ds, manifest)


if __name__ == '__main__':
    main()
