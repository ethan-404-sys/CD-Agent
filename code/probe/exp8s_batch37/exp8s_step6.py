"""实验 8 补充第 6 步：真实评分器下的召回视角（不训练；V1 只做推理）。

方案（每个数据集）：
  wp3 / wp8 / wp20  第 5 步在验证部分冻结的工作点（组合 + D1 + r3 每图前 K）。
  u5                exp8 第 3 步原设置：冻结 U5_x4 单元与 exp8 已保存的逐单元分数（对照）。
评分器：exp8 第 3 步冻结的 V1 full / clean 与 detector（单元内检测器平均概率）。
  - 工作点候选的 V1 特征与 exp8 相同（19 维 SECOND / 15 维 SYSU），在候选的**完整掩码**上计算
    （成员原始掩码之并，从 SAM3 / AnyChange / 检测器概率缓存重建，并核对其掩码外部分与缓存一致），
    与 U5 单元口径相同；并入时只加检测器掩码外的像素。
  - V1 训练时没有见过的来源（SECOND 的 C；SYSU 的 H1、C）不置来源位；只来自这些来源的候选
    V1 不适用，记为拒绝（分数 -inf），detector 不受影响。
阈值（只在验证部分选）：
  pixel     验证像素 F1 最大（并列取更高阈值）；
  recall80  验证正例（新增像素真值占比 ≥ 0.5）召回 ≥ 80% 的最高阈值；
  maxrec    ΔF1 ≥ −0.3 个百分点时像素召回最高的阈值。
  u5 的 pixel / recall80 直接沿用 exp8 frozen.json，maxrec 在验证部分另选。
漏检率另按 batch34 四格拆为 ①+②（无信号）与 ③（有信号；④ 为空）。

阶段：hist（逐图，按图分片，写像素分数直方图与候选分数）→ sweep（验证，选阈值、画曲线）
     → eval（逐图，按图分片，冻结阈值下的对象级指标）→ merge（汇总、写报告）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
STAGING = Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging')
for sub in ('batch30', 'batch32', 'batch36'):
    sys.path.insert(0, str(STAGING / sub))
from exp8s_common import (np, rt, SOURCES, read_json, write_json, level_key, load_objects,  # noqa: E402
                         crop, runtime, upstream, sam_path, h1_path, u5_path, scores)
from exp8s_sources import instances  # noqa: E402
import exp8s_step34 as s34  # noqa: E402
import exp8s_step5 as s5  # noqa: E402
from scipy import ndimage  # noqa: E402

OUT = s34.OUT
CELLS = OUT / 'exp8s_batch34' / 'interim'
STEP5 = OUT / 'exp8s_batch36'
VALIDATION = s34.VALIDATION
TEST = {'second_cv': 'second', 'sysu_val': 'sysu'}
SCHEMES = ('wp3', 'wp8', 'wp20', 'u5')
SCORERS = ('full', 'clean', 'detector')
POLICIES = ('pixel', 'recall80', 'maxrec')
V1 = {'second_cv': dict(root=OUT / 'exp8_batch22', models=OUT / 'exp8_batch22/models',
                        scores=OUT / 'exp8_batch22/evaluation', known=('B', 'G0.05', 'H1')),
      'sysu_val': dict(root=OUT / 'exp8_batch24/u5', models=OUT / 'exp8_batch24/u5/models',
                       scores=OUT / 'exp8_batch24/u5/evaluation', known=('B', 'G0.05'))}
OLD_SOURCES = ('B', 'G0.05', 'C1', 'C2', 'H1', 'pred')   # exp6b_common.SOURCES 的顺序
SOURCE_NAME = {'B': 'B', 'G': 'G0.05', 'H1': 'H1', 'C': None}
EIGHT = np.ones((3, 3), bool)


def family(ds):
    return VALIDATION[ds]


def split_name(ds):
    return 'val' if ds in TEST else 'test'


# ---------------------------------------------------------------- V1
def classifier(input_dim):
    import torch
    from torch import nn
    return nn.Sequential(nn.Linear(input_dim, 512), nn.ReLU(), nn.Dropout(.15),
                         nn.Linear(512, 256), nn.ReLU(), nn.Dropout(.15), nn.Linear(256, 1))


def load_models(ds):
    import torch
    torch.set_num_threads(1)
    cfg = V1[family(ds)]
    frozen = read_json(cfg['root'] / 'frozen.json')
    models = {}
    for key in ('full', 'clean'):
        ckpt = torch.load(cfg['models'] / key / 'model.pt', map_location='cpu', weights_only=True)
        if ckpt['run_id'] != frozen['run_ids'][key] or ckpt['protocol'] != frozen['protocol']:
            raise ValueError(f'{key}: checkpoint differs from exp8 frozen scheme')
        model = classifier(ckpt['input_dim']).eval()
        model.load_state_dict(ckpt['state_dict'])
        models[key] = (model, ckpt['mean'].numpy(), ckpt['std'].numpy(), ckpt['input_dim'])
    return models, frozen


def features(full_masks, sources, prob, known):
    """与 batch22/batch24 extract 相同的 meta(12) + extra(1 + 组合)；返回 x 与是否适用。"""
    combos = 2 ** len(known) - 1
    x = np.zeros((len(full_masks), 11 + 1 + combos), np.float32)
    usable = np.zeros(len(full_masks), bool)
    for i, (obj, srcs) in enumerate(zip(full_masks, sources)):
        x0, y0, x1, y1 = obj['bbox']
        values = prob[y0:y1, x0:x1][obj['mask']]
        x[i, :5] = [values.mean(), values.max(), values.min(), np.percentile(values, 90), np.log(obj['area'])]
        names = sorted({SOURCE_NAME[s] for s in srcs if SOURCE_NAME[s] in known})
        if not names:
            continue
        usable[i] = True
        for n in names:
            x[i, 5 + OLD_SOURCES.index(n)] = 1
        code = sum(1 << known.index(n) for n in names)
        x[i, 11], x[i, 11 + code] = len(names), 1
    return x, usable


def infer(models, x, usable):
    import torch
    out = dict(detector=x[:, 0].astype(np.float64))
    for key, (model, mean, std, dim) in models.items():
        if x.shape[1] != dim:
            raise ValueError(f'{key}: feature dimension {x.shape[1]} != {dim}')
        result = np.full(len(x), -np.inf)
        if usable.any():
            with torch.inference_mode():
                t = torch.from_numpy((x[usable] - mean) / std)
                result[usable] = model(t).squeeze(1).sigmoid().numpy().astype(np.float64)
        out[key] = result
    return out


# ---------------------------------------------------------------- 候选的完整掩码
def raw_rows(ds, name, prob, needed):
    """按 batch30 generate 的顺序重建每个 (来源, 档) 的原始候选（未去掉检测器掩码内部分）。"""
    rows = {}
    cut = read_json(s34.BASE[ds] / 'calibration.json')['datasets'][ds]['cut']
    need_sam = any(s in ('B', 'C') for s, _ in needed)
    if need_sam:
        with np.load(sam_path(ds, name), allow_pickle=False) as z:
            a, b = z['cls_t1'], z['cls_t2']
            binary = (a > 0) & (b > 0) & (a != b)
            conf = z['conf_t1'].astype(np.float32) * z['conf_t2'].astype(np.float32)
            b_rows = [crop(m) for m, _ in rt.components(binary, conf)]
            c_rows = [m for sub, m, s, _ in instances(z, prob.shape) if sub == 'C1' or cut is None or s >= cut]
        for s, level in needed:
            if s == 'B':
                rows[(s, level)] = [m for m in b_rows if m['area'] >= level]
            elif s == 'C':
                rows[(s, level)] = [m for m in c_rows if m['area'] >= level]
    for s, level in needed:
        if s == 'G':
            rows[(s, level)] = [crop(m) for m, _ in rt.components(prob > level, prob)]
        elif s == 'H1':
            with np.load(h1_path(ds, name), allow_pickle=False) as z:
                packed, h1_scores = z['h1_masks'], z['h1_scores']
                order = np.argsort(-h1_scores, kind='stable')
                ids = range(len(order)) if level is None else order[:level]
                rows[(s, level)] = [crop(np.unpackbits(packed[i], count=prob.size).reshape(prob.shape).astype(bool))
                                    for i in ids]
    return rows


def union(objs):
    x0 = min(o['bbox'][0] for o in objs)
    y0 = min(o['bbox'][1] for o in objs)
    x1 = max(o['bbox'][2] for o in objs)
    y1 = max(o['bbox'][3] for o in objs)
    mask = np.zeros((y1 - y0, x1 - x0), bool)
    for o in objs:
        a, b, c, d = o['bbox']
        mask[b - y0:d - y0, a - x0:c - x0] |= o['mask']
    return dict(bbox=[x0, y0, x1, y1], mask=mask, area=int(mask.sum()))


class Image6(s5.Image):
    """在第 5 步的逐图对象上记录每个候选的原始成员 (来源, 档, original_id)。"""

    def source_level(self, s, level):
        key = (s, level)
        if key not in self.internal:
            objs, shape = load_objects(self.path / level_key(s, level))
            if shape != self.gt.shape:
                raise ValueError(f'{self.path}: geometry mismatch')
            for o in objs:
                o['sources'], o['src_scores'], o['source'] = [s], {s: float(o['score'])}, s
                o['orig'] = [(s, level, int(o['original_id']))]
            merged = []
            for g in s34.direct_groups(objs, .5, internal=True):
                m = s34.merge_group(objs, g, False) if len(g) > 1 else objs[g[0]]
                m['orig'] = [t for i in g for t in objs[i]['orig']]
                merged.append(m)
            self.internal[key] = merged
        return self.internal[key]

    def candidates(self, combo):
        pooled = [o for s in SOURCES for o in self.source_level(s, combo[s])]
        merged = []
        for g in s34.direct_groups(pooled, .5, internal=False):
            m = s34.merge_group(pooled, g, False) if len(g) > 1 else pooled[g[0]]
            m['orig'] = [t for i in g for t in pooled[i]['orig']]
            merged.append(m)
        return s34.annotate(merged, self.gt, self.prob)


# ---------------------------------------------------------------- 逐图：方案的单元、分数与新增像素
def frozen_points(ds):
    return {f'wp{f["budget"]}': f for f in read_json(STEP5 / family(ds) / 'frozen.json')['frozen']}


def scheme_units(ds, name, img, models, points, prob):
    """返回 {scheme: (novel_objs, scores_dict, labels)}。novel_objs 为检测器掩码外部分（裁剪对象）。"""
    result = {}
    base = img.base
    for scheme, f in points.items():
        objs = img.candidates(s5.COMBOS[f['combo']])
        k = None if f['K'] == 'all' else int(f['K'])
        order, _ = s34.ranked(objs, s34.rule_keys(objs, f['rule'], img.priority, img.f1))
        chosen = order[:k]
        needed = sorted({(s, lv) for o in chosen for s, lv, _ in o['orig']}, key=str)
        rows = raw_rows(ds, name, prob, needed)
        fulls = []
        for o in chosen:
            members = [rows[(s, lv)][i] for s, lv, i in o['orig']]
            full = union(members)
            # 核对：完整掩码去掉检测器掩码后，必须等于缓存候选（D1 并集）的新增像素。
            x0, y0, x1, y1 = full['bbox']
            novel = full['mask'] & ~base[y0:y1, x0:x1]
            a, b, c, d = o['bbox']
            check = np.zeros_like(novel)
            check[b - y0:d - y0, a - x0:c - x0] = o['mask']
            if not np.array_equal(novel, check):
                raise ValueError(f'{ds}/{name}: rebuilt full mask differs from cached candidate')
            fulls.append(full)
        x, usable = features(fulls, [o['sources'] for o in chosen], prob, V1[family(ds)]['known'])
        sc = infer(models, x, usable) if len(chosen) else {k: np.zeros(0) for k in SCORERS}
        labels = np.asarray([1 if o['a'] / o['area'] >= .5 else 0 if o['a'] / o['area'] < .1 else -1
                             for o in chosen], np.int8)
        result[scheme] = (chosen, sc, labels, usable)
    # 对照：exp8 第 3 步 U5 单元与已保存的分数
    units, shape = rt.load_units(u5_path(ds, name))
    with np.load(V1[family(ds)]['scores'] / split_name(ds) / 'scores' / (Path(name).stem + '.npz'),
                 allow_pickle=False) as z:
        if len(z['unit_id']) != len(units) or (z['unit_id'] != np.arange(len(units))).any():
            raise ValueError(f'{ds}/{name}: U5 units differ from exp8 saved scores')
        sc = {k: z[k].astype(np.float64) for k in SCORERS}
        labels = z['label'].astype(np.int8)
    novel = []
    for u in units:
        x0, y0, x1, y1 = u['bbox']
        novel.append(dict(bbox=u['bbox'], mask=u['mask'] & ~base[y0:y1, x0:x1]))
    result['u5'] = (novel, sc, labels, np.ones(len(units), bool))
    return result


def field(objs, values, shape):
    out = np.full(shape, -np.inf)
    for o, v in zip(objs, values):
        x0, y0, x1, y1 = o['bbox']
        view = out[y0:y1, x0:x1]
        view[o['mask']] = np.maximum(view[o['mask']], v)
    return out


class Context:
    def __init__(self, ds, args):
        self.ds = ds
        manifest = read_json(s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')
        self.baseline = manifest['baseline']
        self.f1 = scores(self.baseline)[0]
        self.priority = read_json(s34.BASE[ds] / 'orders' / f'{VALIDATION[ds]}.json')['order']
        self.models, self.v1_frozen = load_models(ds)
        self.points = frozen_points(ds)
        self.args = args

    def image(self, name):
        img = Image6(self.ds, name, self.f1, self.priority)
        prob = runtime(self.ds).probability(self.ds, name, upstream(self.ds))
        if prob.shape != img.gt.shape or not np.array_equal(prob > .5, img.base):
            raise ValueError(f'{self.ds}/{name}: probability differs from data base')
        with np.load(CELLS / self.ds / (Path(name).stem + '.npz')) as z:
            cell = z['cell']
        return img, prob, cell, scheme_units(self.ds, name, img, self.models, self.points, prob)


def items(ds, args):
    names = s34.image_list(ds)[:args.limit or None]
    return [(i, n) for i, n in enumerate(names) if i % args.shards == args.shard]


def run_hist(args, ds):
    ctx = Context(ds, args)
    dest = args.out / ds / 'hist' / f'{args.shard:03d}.npz'
    dest.parent.mkdir(parents=True, exist_ok=True)
    store = {}
    for index, name in items(ds, args):
        img, prob, cell, schemes = ctx.image(name)
        gt = img.gt
        for scheme, (objs, sc, labels, usable) in schemes.items():
            for scorer in SCORERS:
                f = field(objs, sc[scorer], gt.shape)
                sel = np.isfinite(f) & ~img.base
                values, inverse = np.unique(f[sel], return_inverse=True)
                g, c = gt[sel], cell[sel]
                rows = np.stack([np.bincount(inverse, weights=w, minlength=len(values))
                                 for w in (g, ~g, g & np.isin(c, (1, 2)), g & (c == 3), g & (c == 5))], 1)
                key = f'{scheme}/{scorer}'
                store.setdefault(key + '/px', []).append(np.column_stack([values, rows]))
                store.setdefault(key + '/obj', []).append(np.column_stack([sc[scorer], labels]))
            store.setdefault(f'{scheme}/count', []).append([len(objs), int(usable.sum())])
    out = {k: (np.concatenate(v) if not k.endswith('/count') else np.asarray(v)) for k, v in store.items()}
    tmp = dest.with_name(dest.stem + '.tmp.npz')
    np.savez_compressed(tmp, **{k.replace('/', '__'): v for k, v in out.items()})
    tmp.replace(dest)


# ---------------------------------------------------------------- 阈值扫描（验证）
def load_hist(args, ds):
    parts = [dict(np.load(f)) for f in sorted((args.out / ds / 'hist').glob('[0-9]*.npz'))]
    if not parts:
        raise FileNotFoundError(f'{ds}: no histograms')
    keys = parts[0].keys()
    merged = {k.replace('__', '/'): np.concatenate([p[k] for p in parts]) for k in keys}
    images = len(merged['u5/count'])
    if images != len(s34.image_list(ds)[:args.limit or None]):
        raise ValueError(f'{ds}: {images} images in histograms')
    return merged


def curve(px, baseline, gt_total):
    """按分数降序累积：返回每个候选阈值下的 (阈值, ΔTP, ΔFP, Δ①+②, Δ③, Δ⑤)。"""
    if not len(px):
        return np.zeros((1, 6))
    order = np.argsort(-px[:, 0], kind='stable')
    px = px[order]
    thr, idx = np.unique(-px[:, 0], return_index=True)
    cum = np.cumsum(px[:, 1:], 0)
    ends = np.r_[idx[1:], len(px)] - 1
    rows = np.column_stack([-thr, cum[ends]])
    return np.vstack([[np.inf, 0, 0, 0, 0, 0], rows])


def summarize(baseline, d, gt_total):
    tp, fp, fn = baseline['tp'] + d[1], baseline['fp'] + d[2], baseline['fn'] - d[1]
    f1, iou = scores(dict(tp=tp, fp=fp, fn=fn))
    return f1, iou, tp / (tp + fn), tp / (tp + fp)


def choose(ds, merged, baseline, v1_frozen):
    gt_total = baseline['tp'] + baseline['fn']
    f0 = scores(baseline)[0]
    chosen, curves = {}, {}
    for scheme in SCHEMES:
        for scorer in SCORERS:
            key = f'{scheme}/{scorer}'
            c = curve(merged[key + '/px'], baseline, gt_total)
            m = np.array([summarize(baseline, row, gt_total) for row in c])
            curves[key] = (c, m)
            if scheme == 'u5':
                pixel = v1_frozen['scorers'][scorer]['pixel_threshold']
                rec80 = v1_frozen['scorers'][scorer]['recall80_threshold']
            else:
                best = np.flatnonzero(np.isclose(m[:, 0], m[:, 0].max(), rtol=0, atol=1e-12))
                pixel = float(c[best, 0].max())
                obj = merged[key + '/obj']
                pos = np.sort(obj[obj[:, 1] == 1, 0])[::-1]
                need = int(np.ceil(.8 * len(pos)))
                finite = pos[np.isfinite(pos)]
                if not len(pos):
                    rec80 = float('inf')
                elif np.isfinite(pos[need - 1]):
                    rec80 = float(pos[need - 1])
                else:  # V1 不适用的正例过多，80% 不可达：取全部可评分候选
                    rec80 = float(finite.min()) if len(finite) else float('inf')
            ok = np.flatnonzero(100 * (m[:, 0] - f0) >= -.3)
            best = ok[np.argmax(c[ok, 1])]
            chosen[key] = dict(pixel=float(pixel), recall80=float(rec80), maxrec=float(c[best, 0]))
    return chosen, curves


def at(c, threshold):
    """阈值 t 下（分数 ≥ t 并入）的累积行。"""
    take = c[:, 0] >= threshold
    return c[np.flatnonzero(take)[-1]] if take.any() else c[0]


def plot_curves(ds, curves, chosen, baseline, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['SimHei']
    plt.rcParams['axes.unicode_minus'] = False
    f0, _, r0, _ = summarize(baseline, np.zeros(6), 0)
    colors = {'wp3': '#2a7de1', 'wp8': '#e8743b', 'wp20': '#19a979', 'u5': '#555555'}
    marks = {'pixel': 'o', 'recall80': 's', 'maxrec': '*'}
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.6))
    for ax, scorer in zip(axes, SCORERS):
        for scheme in SCHEMES:
            c, m = curves[f'{scheme}/{scorer}']
            ax.plot(100 * m[:, 2], 100 * m[:, 0], '-', color=colors[scheme], lw=1.2, label=scheme)
            for policy, mk in marks.items():
                row = at(c, chosen[f'{scheme}/{scorer}'][policy])
                f1, _, rec, _ = summarize(baseline, row, 0)
                ax.plot(100 * rec, 100 * f1, mk, color=colors[scheme], ms=8 if mk != '*' else 12,
                        mec='k', label=f'{scheme} {policy}' if scheme == 'wp3' else None)
        ax.axhline(100 * f0, color='k', ls=':', lw=1)
        ax.axhline(100 * f0 - .3, color='r', ls=':', lw=1)
        ax.set_title(f'评分器 {scorer}')
        ax.set_xlabel('像素召回（%）')
        ax.set_ylabel('像素 F1（%）')
        ax.set_ylim(100 * f0 - 6, 100 * f0 + 2)
        ax.grid(alpha=.3)
    axes[0].legend(fontsize=8)
    fig.suptitle(f'{ds}：验证部分评分器阈值扫描（虚线：检测器 F1 与 -0.3 线；○ pixel，□ recall80，★ maxrec）')
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run_sweep(args, ds):
    if ds not in TEST:
        raise ValueError('thresholds are chosen on validation splits only')
    manifest = read_json(s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')
    merged = load_hist(args, ds)
    _, v1_frozen = load_models(ds)
    chosen, curves = choose(ds, merged, manifest['baseline'], v1_frozen)
    write_json(args.out / ds / 'thresholds.json', dict(dataset=ds, thresholds=chosen, partial=bool(args.limit)))
    plot_curves(ds, curves, chosen, manifest['baseline'], args.out / ds / 'figures' / 'step6_sweep.png')
    print(json.dumps(chosen, indent=1))


# ---------------------------------------------------------------- 冻结阈值下逐图评估
def objects_eval(pred, gt, before):
    gl, gn = ndimage.label(gt, EIGHT)
    hit = 0
    if gn:
        area = np.bincount(gl.ravel(), minlength=gn + 1)[1:]
        cov = np.bincount(gl[pred], minlength=gn + 1)[1:]
        hit = int((2 * cov >= area).sum())

    def false_objects(mask):
        pl, pn = ndimage.label(mask, EIGHT)
        if not pn:
            return 0
        touched = np.zeros(pn + 1, bool)
        touched[np.unique(pl[mask & gt])] = True
        return int((~touched[1:]).sum())
    return gn, hit, max(0, false_objects(pred) - false_objects(before))


def run_eval(args, ds):
    ctx = Context(ds, args)
    thresholds = read_json(args.out / VALIDATION[ds] / 'thresholds.json')['thresholds']
    dest = args.out / ds / 'eval' / f'{args.shard:03d}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    with tmp.open('w') as fh:
        for index, name in items(ds, args):
            img, prob, cell, schemes = ctx.image(name)
            gt, base = img.gt, img.base
            gn, hit0, _ = objects_eval(base, gt, base)
            row = dict(index=index, image=name, gt_objects=gn, base_hit=hit0, gt=int(gt.sum()),
                       fn12=int((gt & ~base & np.isin(cell, (1, 2))).sum()),
                       fn3=int((gt & ~base & (cell == 3)).sum()), result={},
                       counts={k: [len(v[0]), int(v[3].sum())] for k, v in schemes.items()})
            for scheme, (objs, sc, labels, usable) in schemes.items():
                for scorer in SCORERS:
                    f = field(objs, sc[scorer], gt.shape)
                    for policy in POLICIES:
                        t = thresholds[f'{scheme}/{scorer}'][policy]
                        added = (f >= t) & ~base
                        pred = base | added
                        _, hit, new_false = objects_eval(pred, gt, base)
                        row['result'][f'{scheme}/{scorer}/{policy}'] = [
                            int((added & gt).sum()), int((added & ~gt).sum()),
                            int((added & gt & np.isin(cell, (1, 2))).sum()), int((added & gt & (cell == 3)).sum()),
                            hit, new_false, int((sc[scorer] >= t).sum())]
            fh.write(json.dumps(row) + '\n')
    tmp.replace(dest)


def run_merge(args, ds):
    manifest = read_json(s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')
    base = manifest['baseline']
    if args.limit:  # 冒烟：基线按前 N 张图重算
        part = [read_json(s34.BASE[ds] / 'interim/exp8s' / ds / Path(n).stem / 'image.json')['baseline']
                for n in s34.image_list(ds)[:args.limit]]
        base = {k: sum(r[k] for r in part) for k in ('tp', 'fp', 'fn')}
    rows = [json.loads(line) for f in sorted((args.out / ds / 'eval').glob('*.jsonl')) for line in f.open()]
    if len(rows) != len(s34.image_list(ds)[:args.limit or None]):
        raise ValueError(f'{ds}: {len(rows)} evaluated images')
    n = len(rows)
    gt = sum(r['gt'] for r in rows)
    if gt != base['tp'] + base['fn']:
        raise ValueError(f'{ds}: GT total differs from manifest baseline')
    fn12, fn3 = sum(r['fn12'] for r in rows), sum(r['fn3'] for r in rows)
    objects, hit0 = sum(r['gt_objects'] for r in rows), sum(r['base_hit'] for r in rows)
    f0, i0 = scores(base)
    out = dict(dataset=ds, images=n, baseline=dict(f1=100 * f0, iou=100 * i0, recall=100 * base['tp'] / gt,
                                                     precision=100 * base['tp'] / (base['tp'] + base['fp']),
                                                     miss=100 * base['fn'] / gt, miss12=100 * fn12 / gt,
                                                     miss3=100 * fn3 / gt, object_recall=100 * hit0 / objects),
               schemes={})
    for key in rows[0]['result']:
        v = np.sum([r['result'][key] for r in rows], 0)
        tp, fp, fn = base['tp'] + v[0], base['fp'] + v[1], base['fn'] - v[0]
        f1, iou = scores(dict(tp=tp, fp=fp, fn=fn))
        scheme = key.split('/')[0]
        res = dict(f1=100 * f1, iou=100 * iou, recall=100 * tp / gt, precision=100 * tp / (tp + fp),
                   miss=100 * fn / gt, miss12=100 * (fn12 - v[2]) / gt, miss3=100 * (fn3 - v[3]) / gt,
                   object_recall=100 * v[4] / objects, new_false_objects_per_image=v[5] / n,
                   added_precision=100 * v[0] / (v[0] + v[1]) if v[0] + v[1] else None,
                   accepted_units_per_image=v[6] / n)
        for k in ('f1', 'iou', 'recall', 'precision', 'miss', 'miss12', 'miss3', 'object_recall'):
            res['d_' + k] = res[k] - out['baseline'][k]
        cnt = np.asarray([r['counts'][scheme] for r in rows])
        res['units_per_image'] = float(cnt[:, 0].mean())
        res['v1_usable_fraction'] = float(cnt[:, 1].sum() / max(1, cnt[:, 0].sum()))
        out['schemes'][key] = res
    write_json(args.out / ds / 'summary.json', out)
    print(json.dumps(out['baseline'], ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('hist', 'sweep', 'eval', 'merge'))
    p.add_argument('--dataset', required=True, choices=('second_cv', 'second', 'sysu_val', 'sysu'))
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    {'hist': run_hist, 'sweep': run_sweep, 'eval': run_eval, 'merge': run_merge}[args.stage](args, args.dataset)


if __name__ == '__main__':
    main()
