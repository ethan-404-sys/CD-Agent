"""实验 8 补充第 5 步：81 种来源组合的帕累托前沿、工作点冻结与测试评估（只读 batch30 数据底座与 batch34 四格）。

组合：四个来源各取 3 档（G 0.05/0.2/0.4，B 0/100/500，H1 全部/10/3，C 0/100/500），共 3⁴ = 81 种；
每种组合先在来源内部去重，再跨来源按 D1 去重（与 batch32 的 D1 实现相同，默认档组合与 batch30 combined() 逐对象核对）。
每种组合再按 r0b（oracle 上界）、r2、r3 排序截取每图前 K 个（K 同第 3、4 步，全部即不截取）。
每个点报告：每图候选数、ΔF1_oracle（含 exp8 口径）、R_obj@0.3/0.5、AR_obj、R_pix、Pos%、Frag，
以及按 batch34 四格拆分的 ①+② 与 ③+④ 的 R_obj@0.5、R_pix。
指标在每图内用前缀累积一次算出所有 K；全部为可加计数，分片汇总后再算比例。

阶段：
  grid   逐图（按图分片），写 grid_XXX.npz（计数和）与每图候选数；--only 给出组合序号时只算这些组合（测试用）。
  merge  汇总；验证部分画前沿并按预算冻结工作点（frozen.json）；测试部分只报告冻结点。
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, '/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch30')
sys.path.insert(0, '/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch32')
from exp8s_common import (np, SOURCES, DEFAULTS, read_json, write_json, level_key,  # noqa: E402
                         load_objects, combined, overlap, scores)
import exp8s_step34 as s34  # noqa: E402

OUT = s34.OUT
CELLS = OUT / 'exp8s_batch34' / 'interim'
GRID = {'G': (.05, .2, .4), 'B': (0, 100, 500), 'H1': (None, 10, 3), 'C': (0, 100, 500)}
COMBOS = [dict(zip(SOURCES, levels)) for levels in itertools.product(*(GRID[s] for s in SOURCES))]
DEFAULT_COMBO = COMBOS.index({s: DEFAULTS[s] for s in SOURCES})
RULES = ('r0b', 'r2', 'r3')
KS = s34.KS
TAUS = (.3, .4, .5, .6, .7)
M = ('candidates', 'positive', 'small', 'summed_area', 'oracle_tp', 'oracle_fp', 'exp8_tp', 'exp8_fp',
     'fn_covered', 'cov12', 'cov34', 'hit03', 'hit04', 'hit05', 'hit06', 'hit07', 'hit05_12', 'hit05_34',
     'frag_sum', 'frag_objects')
D = ('images', 'fn_pixels', 'px12', 'px34', 'comps', 'comps12', 'comps34')
BUDGETS = (3, 8, 20)


def combo_label(c):
    return ' '.join(f'{s}{"全部" if c[s] is None else c[s]}' for s in ('G', 'B', 'H1', 'C'))


# ---------------------------------------------------------------- 逐图
class Image:
    def __init__(self, ds, name, f1, priority):
        path = s34.BASE[ds] / 'interim/exp8s' / ds / Path(name).stem
        self.gt, self.base, self.prob = s34.pixels(path / 'pixels.npz')
        self.f1, self.priority, self.path = f1, priority, path
        with np.load(CELLS / ds / (Path(name).stem + '.npz')) as z:
            cell = z['cell']
        self.flat_gt = self.gt.ravel()
        self.c12 = np.isin(cell, (1, 2)).ravel()
        self.c34 = np.isin(cell, (3, 4)).ravel()
        self.missed, _ = load_objects(path / 'missed')
        self.comp_cell = []
        for t in self.missed:
            x0, y0, x1, y1 = t['bbox']
            values = np.unique(cell[y0:y1, x0:x1][t['mask']])
            if len(values) != 1 or values[0] not in (1, 2, 3, 4):
                raise ValueError(f'{path}: missed object does not map to one cell')
            self.comp_cell.append(int(values[0]))
        self.comp_cell = np.asarray(self.comp_cell, np.int64)
        self.comp_area = np.asarray([t['area'] for t in self.missed], np.float64)
        self.denominators = dict(images=1, fn_pixels=int((self.gt & ~self.base).sum()),
                                 px12=int(self.c12.sum()), px34=int(self.c34.sum()), comps=len(self.missed),
                                 comps12=int(np.isin(self.comp_cell, (1, 2)).sum()),
                                 comps34=int(np.isin(self.comp_cell, (3, 4)).sum()))
        self.internal = {}

    def source_level(self, s, level):
        """来源内部去重（与 batch32 apply_method 的内部阶段相同），按 (来源, 档) 缓存。"""
        key = (s, level)
        if key not in self.internal:
            objs, shape = load_objects(self.path / level_key(s, level))
            if shape != self.gt.shape:
                raise ValueError(f'{self.path}: geometry mismatch')
            for o in objs:
                o['sources'], o['src_scores'], o['source'] = [s], {s: float(o['score'])}, s
            self.internal[key] = [s34.merge_group(objs, g, False) if len(g) > 1 else objs[g[0]]
                                  for g in s34.direct_groups(objs, .5, internal=True)]
        return self.internal[key]

    def candidates(self, combo):
        pooled = [o for s in SOURCES for o in self.source_level(s, combo[s])]
        merged = [s34.merge_group(pooled, g, False) if len(g) > 1 else pooled[g[0]]
                  for g in s34.direct_groups(pooled, .5, internal=False)]
        return s34.annotate(merged, self.gt, self.prob)

    def evaluate(self, objs):
        """返回 (len(RULES), len(KS), len(M)) 的计数。"""
        h, w = self.gt.shape
        n = len(objs)
        idx = []
        for o in objs:
            x0, y0, x1, y1 = o['bbox']
            ys, xs = np.nonzero(o['mask'])
            idx.append((ys + y0) * w + (xs + x0))
        inter = np.zeros((n, len(self.missed)))
        for i, o in enumerate(objs):
            for j, t in enumerate(self.missed):
                inter[i, j] = overlap(o, t)
        area = np.asarray([o['area'] for o in objs], np.float64)
        iou = inter / (area[:, None] + self.comp_area[None, :] - inter) if n else inter
        frag = inter >= .1 * self.comp_area[None, :]
        precision = np.asarray([o['a'] / o['area'] for o in objs])
        out = np.zeros((len(RULES), len(KS), len(M)), np.int64)
        for r, rule in enumerate(RULES):
            _, order = s34.ranked(objs, s34.rule_keys(objs, rule, self.priority, self.f1))
            covered = np.zeros(h * w, bool)
            oracle = np.zeros(h * w, bool)
            exp8 = np.zeros(h * w, bool)
            acc = np.zeros(len(M), np.int64)
            best = np.zeros(len(self.missed))
            fragments = np.zeros(len(self.missed), np.int64)
            checkpoints = {min(k, n) if k else n for k in KS}
            snapshots = {}
            if 0 in checkpoints:
                snapshots[0] = self._snapshot(acc, best, fragments)
            for step, i in enumerate(order, start=1):
                new = idx[i][~covered[idx[i]]]
                covered[new] = True
                acc[0] += 1
                acc[2] += area[i] < 100
                acc[3] += int(area[i])
                acc[8] += int(self.flat_gt[new].sum())
                acc[9] += int(self.c12[new].sum())
                acc[10] += int(self.c34[new].sum())
                if precision[i] > self.f1 / 2:
                    acc[1] += 1
                    add = idx[i][~oracle[idx[i]]]
                    oracle[add] = True
                    tp = int(self.flat_gt[add].sum())
                    acc[4] += tp
                    acc[5] += len(add) - tp
                if precision[i] >= .5:
                    add = idx[i][~exp8[idx[i]]]
                    exp8[add] = True
                    tp = int(self.flat_gt[add].sum())
                    acc[6] += tp
                    acc[7] += len(add) - tp
                best = np.maximum(best, iou[i])
                fragments += frag[i]
                if step in checkpoints:
                    snapshots[step] = self._snapshot(acc, best, fragments)
            for k_i, k in enumerate(KS):
                out[r, k_i] = snapshots[min(k, n) if k else n]
        return out

    def _snapshot(self, acc, best, fragments):
        row = acc.copy()
        for t_i, tau in enumerate(TAUS):
            row[11 + t_i] = int((best >= tau).sum())
        hit = best >= .5
        row[16] = int((hit & np.isin(self.comp_cell, (1, 2))).sum())
        row[17] = int((hit & np.isin(self.comp_cell, (3, 4))).sum())
        row[18] = int(fragments[hit].sum())
        row[19] = int(hit.sum())
        return row


def run_grid(args, ds, f1, priority):
    only = [int(v) for v in args.only.split(',')] if args.only else list(range(len(COMBOS)))
    items = [(i, n) for i, n in enumerate(s34.image_list(ds)[:args.limit or None]) if i % args.shards == args.shard]
    sums = np.zeros((len(only), len(RULES), len(KS), len(M)), np.int64)
    counts = np.zeros((len(items), len(only), len(RULES), len(KS)), np.int32)
    den = np.zeros(len(D), np.int64)
    for row, (index, name) in enumerate(items):
        img = Image(ds, name, f1, priority)
        den += [img.denominators[k] for k in D]
        for c_i, c in enumerate(only):
            objs = img.candidates(COMBOS[c])
            if c == DEFAULT_COMBO:
                ref = combined({s: load_objects(img.path / level_key(s, DEFAULTS[s]))[0] for s in SOURCES}, SOURCES)
                sig = lambda o: (tuple(o['bbox']), o['area'], o['mask'].tobytes())  # noqa: E731
                if sorted(map(sig, ref)) != sorted(map(sig, objs)):
                    raise ValueError(f'{ds}/{name}: default combo differs from batch30 combined()')
            value = img.evaluate(objs)
            sums[c_i] += value
            counts[row, c_i] = value[..., 0]
    dest = args.out / ds / f'grid_{args.shard:03d}.npz'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out / ds / f'grid_{args.shard:03d}.tmp.npz'
    np.savez_compressed(tmp, sums=sums, counts=counts, den=den,
                        only=np.asarray(only), index=np.asarray([i for i, _ in items]))
    tmp.replace(dest)


# ---------------------------------------------------------------- 汇总
def load_grid(args, ds):
    files = sorted(f for f in (args.out / ds).glob('grid_*.npz') if '.tmp' not in f.name)
    parts = [dict(np.load(f)) for f in files]
    only = parts[0]['only']
    if any((p['only'] != only).any() for p in parts):
        raise ValueError(f'{ds}: shards computed different combos')
    index = np.concatenate([p['index'] for p in parts])
    expected = len(s34.image_list(ds)[:args.limit or None])
    if len(index) != expected or len(set(index.tolist())) != expected:
        raise ValueError(f'{ds}: {len(index)} images, expected {expected}')
    sums = sum(p['sums'] for p in parts)
    counts = np.concatenate([p['counts'] for p in parts])
    den = dict(zip(D, (int(v) for v in sum(p['den'] for p in parts))))
    return only.tolist(), sums, counts, den


def metrics(vec, count_col, den, baseline):
    v = dict(zip(M, (int(x) for x in vec)))
    f1, iou = scores(baseline)
    ratio = lambda a, b: a / b if b else None  # noqa: E731
    out = dict(candidates_mean=float(count_col.mean()), candidates_p90=float(np.quantile(count_col, .9)),
               R_obj_03=ratio(v['hit03'], den['comps']), R_obj_05=ratio(v['hit05'], den['comps']),
               AR_obj=ratio(sum(v[f'hit0{t}'] for t in range(3, 8)), 5 * den['comps']),
               R_pix=ratio(v['fn_covered'], den['fn_pixels']),
               R_obj_05_12=ratio(v['hit05_12'], den['comps12']), R_pix_12=ratio(v['cov12'], den['px12']),
               R_obj_05_34=ratio(v['hit05_34'], den['comps34']), R_pix_34=ratio(v['cov34'], den['px34']),
               Pos_percent=ratio(100 * v['positive'], v['candidates']),
               small_fraction=ratio(v['small'], v['candidates']),
               Frag=ratio(v['frag_sum'], v['frag_objects']))
    for kind in ('oracle', 'exp8'):
        c = dict(tp=baseline['tp'] + v[f'{kind}_tp'], fp=baseline['fp'] + v[f'{kind}_fp'],
                 fn=baseline['fn'] - v[f'{kind}_tp'])
        nf, ni = scores(c)
        out[f'{kind}_dF1'] = 100 * (nf - f1)
        out[f'{kind}_dIoU'] = 100 * (ni - iou)
    return out


def points(only, sums, counts, den, baseline):
    rows = []
    for c_i, c in enumerate(only):
        for r_i, rule in enumerate(RULES):
            for k_i, k in enumerate(KS):
                if k is None and r_i:
                    continue  # 不截取时三条规则相同，只保留一行（rule 记为 full）
                rows.append(dict(combo=c, label=combo_label(COMBOS[c]), rule='full' if k is None else rule,
                                 K=s34.k_label(k),
                                 **metrics(sums[c_i, r_i, k_i], counts[:, c_i, r_i, k_i], den, baseline)))
    return rows


def pareto(rows, y):
    """横轴每图候选数，按横轴升序扫描，只保留 y 严格上升的点（上包络）。"""
    front, best = [], -np.inf
    for r in sorted(rows, key=lambda r: (r['candidates_mean'], -(r[y] or 0.))):
        if (r[y] or 0.) > best:
            front.append(r)
            best = r[y] or 0.
    return front


def freeze(rows):
    """验证部分：每个预算下取可部署（不用真值排序）且每图平均候选数 ≤ 预算的点中 ΔF1_oracle 最高者，
    平局看 ①+② R_obj@0.5、再看更少候选。"""
    deployable = [r for r in rows if r['rule'] != 'r0b']
    frozen = []
    for b in BUDGETS:
        ok = [r for r in deployable if r['candidates_mean'] <= b]
        best = max(ok, key=lambda r: (round(r['oracle_dF1'], 6), r['R_obj_05_12'] or 0., -r['candidates_mean']))
        frozen.append(dict(budget=b, combo=best['combo'], label=best['label'], rule=best['rule'], K=best['K']))
    return frozen


def plot(ds, rows, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['SimHei']
    plt.rcParams['axes.unicode_minus'] = False
    full = [r for r in rows if r['rule'] == 'full']
    panels = (('oracle_dF1', 'ΔF1_oracle（百分点）'), ('R_obj_05', 'R_obj@0.5（全部漏检）'),
              ('R_obj_05_12', 'R_obj@0.5（①+② 无信号漏检）'), ('R_pix_12', 'R_pix（①+② 无信号漏检）'))
    colors = {'r0b': '#2a7de1', 'r2': '#e8743b', 'r3': '#19a979'}
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    for ax, (y, label) in zip(axes.ravel(), panels):
        ax.scatter([r['candidates_mean'] for r in full], [r[y] or 0. for r in full], s=14, c='#999999',
                   label='81 种组合（D1，不截取）')
        f = pareto(full, y)
        ax.plot([r['candidates_mean'] for r in f], [r[y] or 0. for r in f], 'k-', lw=1.6, label='组合帕累托前沿')
        for rule in RULES:
            cur = [r for r in rows if r['combo'] == DEFAULT_COMBO and r['rule'] in (rule, 'full')]
            cur.sort(key=lambda r: r['candidates_mean'])
            ax.plot([r['candidates_mean'] for r in cur], [r[y] or 0. for r in cur], '-o', ms=3, color=colors[rule],
                    label=f'默认组合 {rule} 每图前 K')
            env = pareto([r for r in rows if r['rule'] in (rule, 'full')], y)
            ax.plot([r['candidates_mean'] for r in env], [r[y] or 0. for r in env], '--', color=colors[rule], lw=1,
                    label=f'任一组合 + {rule} 的上包络')
        ax.set_xscale('log')
        ax.set_xlabel('每图候选数（D1 去重后，对数轴）')
        ax.set_ylabel(label)
        ax.grid(alpha=.3)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle(f'{ds}：第 5 步帕累托前沿（验证部分；四源各 3 档共 81 组合）')
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run_merge(args, ds, baseline):
    only, sums, counts, den = load_grid(args, ds)
    rows = points(only, sums, counts, den, baseline)
    summary = dict(dataset=ds, denominators=den, grid={s: list(GRID[s]) for s in SOURCES},
                   default_combo=DEFAULT_COMBO, partial=bool(args.limit))
    if args.frozen is None:
        summary['fronts'] = {y: pareto([r for r in rows if r['rule'] == 'full'], y)
                             for y in ('oracle_dF1', 'R_obj_05', 'R_obj_05_12', 'R_pix_12')}
        summary['frozen'] = freeze(rows)
        write_json(args.out / ds / 'frozen.json', dict(dataset=ds, frozen=summary['frozen'],
                                                         partial=bool(args.limit)))
        plot(ds, rows, args.out / ds / 'figures' / 'step5_front.png')
    else:
        frozen = read_json(args.frozen)['frozen']
        summary['frozen'] = frozen
        keep = {(f['combo'], f['rule'], f['K']) for f in frozen}
        rows = [r for r in rows if (r['combo'], r['rule'], r['K']) in keep]
    summary['frozen_rows'] = [r for f in summary['frozen'] for r in rows
                              if (r['combo'], r['rule'], r['K']) == (f['combo'], f['rule'], f['K'])]
    write_json(args.out / ds / 'summary.json', summary)
    with (args.out / ds / 'points.jsonl').open('w') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(json.dumps(summary['frozen_rows'], ensure_ascii=False, indent=1))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('grid', 'merge'))
    p.add_argument('--dataset', required=True, choices=tuple(s34.BASE))
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--only', default='')
    p.add_argument('--frozen', type=Path)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    ds = args.dataset
    manifest = read_json(s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')
    if args.limit:
        rows = [read_json(s34.BASE[ds] / 'interim/exp8s' / ds / Path(n).stem / 'image.json')['baseline']
                for n in s34.image_list(ds)[:args.limit]]
        manifest = {**manifest, 'baseline': {k: sum(r[k] for r in rows) for k in ('tp', 'fp', 'fn')}}
    f1 = scores(manifest['baseline'])[0]
    priority = read_json(s34.BASE[ds] / 'orders' / f'{s34.VALIDATION[ds]}.json')['order']
    if args.stage == 'grid':
        run_grid(args, ds, f1, priority)
    else:
        run_merge(args, ds, manifest['baseline'])


if __name__ == '__main__':
    main()
