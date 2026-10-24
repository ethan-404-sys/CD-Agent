"""实验 8 补充 2 第 1 步：只用推理时可见信息的分流规则（不训练、不跑任何模型）。

候选 c 取完整掩码（成员原始掩码之并，与 exp8s 第 6 步 batch37 的重建与核对相同），D 为检测器 0.5 掩码：
  r = |D ∩ c| / |c|；k = D ∩ c 中 ≥ 5 像素的 8 连通域个数。
  R(τ)：r < τ 去补漏，否则去精修；R(τ, k₀)：r < τ 但 k ≥ k₀ 的也去精修。
候选集：默认四源 D1 全部候选（d1all）；SECOND / SYSU 另加第 5 步冻结的每图 8、20 工作点（wp8、wp20）。
漏检连通域与格同 batch34（目标内 GT & ~D 的 8 连通域，含 ⑤ 残余）；③ 按目标覆盖率分 10–30%（3a）与 ≥ 30%（3b）。
每个连通域由覆盖其像素最多的候选决定去向（并列取候选序号小者），无候选覆盖记为未覆盖。

阶段：route（按图分片，逐图写候选的 r、k 与连通域的最佳候选）→ merge（所有规则的统计）→ examples（审查页）。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
STAGING = Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging')
for sub in ('batch30', 'batch32', 'batch36', 'batch37', 'batch38'):
    sys.path.insert(0, str(STAGING / sub))
from exp8s_common import np, rt, gt_label, read_json, write_json, scores  # noqa: E402
import exp8s_step34 as s34  # noqa: E402
import exp8s_step5 as s5  # noqa: E402
import exp8s_step6 as s6  # noqa: E402
import exp8s_cell_examples as b38  # noqa: E402
import common  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402

OUT = s34.OUT
s34.BASE.update(levir=OUT / 'exp8s_batch30', whu=OUT / 'exp8s_batch30')
DATASETS = ('second_cv', 'second', 'sysu_val', 'sysu', 'levir', 'whu')
SPLIT = dict(second_cv='SECOND 验证', second='SECOND 测试', sysu_val='SYSU 验证', sysu='SYSU 测试',
             levir='LEVIR 测试', whu='WHU 测试')
TAUS = (.05, .1, .2, .3, .5)
K0S = (None, 3, 5)
RULES = [(t, k) for k in K0S for t in TAUS]
GROUPS = ('1', '2', '3a', '3b', '5')
GROUP_NAMES = {'1': '①', '2': '②', '3a': '③ 覆盖 10–30%', '3b': '③ 覆盖 ≥ 30%', '5': '⑤ 残余'}
ROUTES = ('fill', 'refine', 'none')
ROUTE_NAMES = dict(fill='补漏', refine='精修', none='未覆盖')
MIN_PIECE = 5
EIGHT = np.ones((3, 3), bool)
SEED = 20261024


def rule_name(t, k):
    return f'R({t:g})' if k is None else f'R({t:g},{k})'


def route(r, k, t, k0):
    return 'fill' if r < t and not (k0 is not None and k >= k0) else 'refine'


def sets_for(ds):
    return ('d1all', 'wp8', 'wp20') if ds in s34.VALIDATION else ('d1all',)


# ---------------------------------------------------------------- 逐图
def raw_rows(ds, name, prob, needed):
    """batch37 raw_rows；LEVIR / WHU 的 B 按 batch30 generate 取建筑类变化 (a == 5) != (b == 5)。"""
    if ds not in ('levir', 'whu') or not any(s == 'B' for s, _ in needed):
        return s6.raw_rows(ds, name, prob, needed)
    rows = s6.raw_rows(ds, name, prob, [x for x in needed if x[0] != 'B'])
    with np.load(s6.sam_path(ds, name), allow_pickle=False) as z:
        a, b = z['cls_t1'], z['cls_t2']
        conf = z['conf_t1'].astype(np.float32) * z['conf_t2'].astype(np.float32)
        b_rows = [s6.crop(m) for m, _ in rt.components((a == 5) != (b == 5), conf)]
    for s, level in needed:
        if s == 'B':
            rows[(s, level)] = [m for m in b_rows if m['area'] >= level]
    return rows


def full_masks(ds, name, img, prob, chosen):
    """与 batch37 scheme_units 相同的完整掩码重建与核对。"""
    needed = sorted({(s, lv) for o in chosen for s, lv, _ in o['orig']}, key=str)
    rows = raw_rows(ds, name, prob, needed)
    fulls = []
    for o in chosen:
        full = s6.union([rows[(s, lv)][i] for s, lv, i in o['orig']])
        x0, y0, x1, y1 = full['bbox']
        novel = full['mask'] & ~img.base[y0:y1, x0:x1]
        a, b, c, d = o['bbox']
        check = np.zeros_like(novel)
        check[b - y0:d - y0, a - x0:c - x0] = o['mask']
        if not np.array_equal(novel, check):
            raise ValueError(f'{ds}/{name}: rebuilt full mask differs from cached candidate')
        fulls.append(full)
    return fulls


def chosen_sets(ds, img):
    out = {'d1all': img.candidates(s5.COMBOS[s5.DEFAULT_COMBO])}
    if ds in s34.VALIDATION:
        for scheme, f in s6.frozen_points(ds).items():
            if scheme in ('wp8', 'wp20'):
                objs = img.candidates(s5.COMBOS[f['combo']])
                k = None if f['K'] == 'all' else int(f['K'])
                order, _ = s34.ranked(objs, s34.rule_keys(objs, f['rule'], img.priority, img.f1))
                out[scheme] = order[:k]
    return out


def rk(full, base):
    x0, y0, x1, y1 = full['bbox']
    inside = full['mask'] & base[y0:y1, x0:x1]
    labels, n = ndimage.label(inside, EIGHT)
    k = int((np.bincount(labels.ravel(), minlength=n + 1)[1:] >= MIN_PIECE).sum()) if n else 0
    return float(inside.sum() / full['area']), k


def missed_components(ds, name, gt, base, cell):
    """batch34 口径的全部漏检连通域；返回标签图与逐连通域记录，并核对格标签。"""
    gl, count, transitions = gt_label(ds, name, gt)
    labels = np.zeros(gt.shape, np.int32)
    comps = []
    for ident in range(1, count + 1):
        original = gl == ident
        area = int(original.sum())
        inside = int(base[original].sum())
        cover = inside / area
        for mask in b38.components(original & ~base):
            size = int(mask.sum())
            if area < rt.e4.MIN_TARGET or size < rt.e4.MIN_TARGET:
                group = '5'
            else:
                group = ('3a' if cover < .3 else '3b') if cover >= .1 else '1' if b38.touches(mask, base) else '2'
            expect = {'1': 1, '2': 2, '3a': 3, '3b': 3, '5': 5}[group]
            if np.unique(cell[mask]).tolist() != [expect]:
                raise ValueError(f'{ds}/{name}: cell differs from batch34')
            ys, xs = np.nonzero(mask)
            labels[mask] = len(comps) + 1
            comps.append(dict(target=ident, group=group, px=size, target_area=area, coverage=cover,
                              inside=inside, transition=transitions.get(ident), seed=[int(ys[0]), int(xs[0])]))
    if int((labels > 0).sum()) != int((gt & ~base).sum()):
        raise ValueError(f'{ds}/{name}: components do not cover all FN pixels')
    return labels, comps


def best_candidates(labels, n, fulls):
    """每个连通域：被覆盖像素最多的候选序号（-1 为未覆盖）与覆盖像素数。"""
    best, best_px = np.full(n, -1, np.int64), np.zeros(n, np.int64)
    for i, full in enumerate(fulls):
        x0, y0, x1, y1 = full['bbox']
        cnt = np.bincount(labels[y0:y1, x0:x1][full['mask']], minlength=n + 1)[1:]
        better = cnt > best_px
        best[better], best_px[better] = i, cnt[better]
    return best, best_px


class Context:
    def __init__(self, ds):
        self.ds = ds
        if ds in s34.VALIDATION:
            manifest = read_json(s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')
            self.f1 = scores(manifest['baseline'])[0]
            self.priority = read_json(s34.BASE[ds] / 'orders' / f'{s34.VALIDATION[ds]}.json')['order']
        else:
            self.f1, self.priority = None, None

    def image(self, name):
        img = s6.Image6(self.ds, name, self.f1, self.priority)
        with np.load(s6.CELLS / self.ds / (Path(name).stem + '.npz')) as z:
            cell = z['cell']
        return img, img.prob, cell


def run_route(args, ds):
    ctx = Context(ds)
    dest = args.out / ds / 'route' / f'{args.shard:03d}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    with tmp.open('w') as fh:
        for index, name in s6.items(ds, args):
            img, prob, cell = ctx.image(name)
            labels, comps = missed_components(ds, name, img.gt, img.base, cell)
            row = dict(index=index, image=name, comps=comps, sets={})
            for key, chosen in chosen_sets(ds, img).items():
                fulls = full_masks(ds, name, img, prob, chosen)
                cand = [[round(r, 6), k] + full['bbox'] for full in fulls for r, k in [rk(full, img.base)]]
                best, best_px = best_candidates(labels, len(comps), fulls)
                row['sets'][key] = dict(cand=cand, best=best.tolist(), best_px=best_px.tolist())
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    tmp.replace(dest)


# ---------------------------------------------------------------- 汇总
def load_rows(args, ds):
    rows = [json.loads(line) for f in sorted((args.out / ds / 'route').glob('*.jsonl')) for line in f.open()]
    if len(rows) != len(s34.image_list(ds)[:args.limit or None]) or len({r['index'] for r in rows}) != len(rows):
        raise ValueError(f'{ds}: {len(rows)} routed images')
    return sorted(rows, key=lambda r: r['index'])


def run_merge(args, ds):
    rows = load_rows(args, ds)
    n = len(rows)
    out = dict(dataset=ds, images=n, sets={})
    totals = {g: dict(comps=0, px=0) for g in GROUPS}
    for r in rows:
        for c in r['comps']:
            totals[c['group']]['comps'] += 1
            totals[c['group']]['px'] += c['px']
    out['components'] = totals
    for key in sets_for(ds):
        cand = [np.asarray(r['sets'][key]['cand'], np.float64).reshape(-1, 6) for r in rows]
        res = dict(candidates_per_image=float(np.mean([len(c) for c in cand])),
                   r_zero_fraction=float(sum((c[:, 0] == 0).sum() for c in cand) / max(1, sum(map(len, cand)))),
                   rules={})
        for t, k0 in RULES:
            fill = np.array([((c[:, 0] < t) & ~((k0 is not None) & (c[:, 1] >= (k0 or 0)))).sum() for c in cand])
            table = {g: {rt_: [0, 0] for rt_ in ROUTES} for g in GROUPS}
            for r, c in zip(rows, cand):
                best = r['sets'][key]['best']
                for comp, b in zip(r['comps'], best):
                    dest = 'none' if b < 0 else route(c[b, 0], c[b, 1], t, k0)
                    table[comp['group']][dest][0] += 1
                    table[comp['group']][dest][1] += comp['px']
            res['rules'][rule_name(t, k0)] = dict(fill_per_image=float(fill.mean()),
                                                  refine_per_image=float(np.mean([len(c) for c in cand]) - fill.mean()),
                                                  table=table)
        out['sets'][key] = res
    write_json(args.out / ds / 'summary.json', out)
    t = out['sets']['d1all']['rules']['R(0.1)']['table']
    print(ds, json.dumps({g: {k: v[0] for k, v in t[g].items()} for g in GROUPS}))


# ---------------------------------------------------------------- 审查页
EXAMPLE_SETS = ('second', 'sysu')
EXAMPLE_RULE = (.1, None)
CAND_COLOR = (0, 230, 90)


def render(ds, name, comp, cand_index, dest):
    ctx = Context(ds)
    img, prob, cell = ctx.image(name)
    gt, base = img.gt, img.base
    gl, _, _ = gt_label(ds, name, gt)
    target = gl == comp['target']
    labels, _ = ndimage.label(target & ~base, EIGHT)
    mask = labels == labels[tuple(comp['seed'])]
    if int(mask.sum()) != comp['px']:
        raise ValueError(f'{ds}/{name}: component mismatch')
    cand = np.zeros(gt.shape, bool)
    if cand_index >= 0:
        chosen = img.candidates(s5.COMBOS[s5.DEFAULT_COMBO])
        full = full_masks(ds, name, img, prob, [chosen[cand_index]])[0]
        x0, y0, x1, y1 = full['bbox']
        cand[y0:y1, x0:x1] = full['mask']
    y0, x0, side = b38.crop_box(target, gt.shape)
    f = max(1, b38.SIDE // side)
    sl = (slice(y0, y0 + side), slice(x0, x0 + side))
    t1, t2 = (common.load_rgb(ds, name, v) for v in ('t1', 't2'))

    def up_rgb(a):
        return np.asarray(Image.fromarray(a[sl]).resize((side * f,) * 2, Image.NEAREST))

    def mask_rgb(m, other):
        a = np.zeros(m.shape + (3,), np.uint8)
        a[other] = 90
        a[m] = 255
        return a

    up = {k: b38.upscale(v[sl], f) for k, v in dict(mask=mask, base=base, target=target, gt=gt, cand=cand).items()}
    over = b38.blend(up_rgb(t2), up['base'], b38.COLORS['base'], .45)
    over = b38.blend(over, up['mask'], b38.COLORS['comp'], .8)
    over[b38.contour(up['target']) & ~up['mask']] = b38.COLORS['rest']
    edge = up['cand'] & ~ndimage.binary_erosion(up['cand'], EIGHT, iterations=2)
    over[edge] = CAND_COLOR
    panels = [up_rgb(t1), up_rgb(t2), up_rgb(mask_rgb(target, gt & ~target)), up_rgb(mask_rgb(base, np.zeros_like(base))),
              over]
    thumb = b38.blend(t2, base, b38.COLORS['base'], .45)
    thumb = b38.blend(thumb, mask, b38.COLORS['comp'], .9)
    thumb[cand & ~ndimage.binary_erosion(cand, EIGHT)] = CAND_COLOR
    im = Image.fromarray(np.asarray(Image.fromarray(thumb).resize((b38.SIDE,) * 2, Image.BILINEAR)))
    s = b38.SIDE / gt.shape[0]
    ImageDraw.Draw(im).rectangle([x0 * s, y0 * s, (x0 + side) * s - 1, (y0 + side) * s - 1],
                                 outline=b38.COLORS['box'], width=2)
    panels.append(np.asarray(im))
    gap, S = 4, b38.SIDE
    strip = np.full((S, S * len(panels) + gap * (len(panels) - 1), 3), 255, np.uint8)
    for i, p in enumerate(panels):
        if p.shape[0] != S:
            p = np.asarray(Image.fromarray(p).resize((S, S), Image.NEAREST if p.shape[0] >= S else Image.BILINEAR))
        strip[:, i * (S + gap): i * (S + gap) + S] = p
    Image.fromarray(strip).save(dest, optimize=True)
    return [y0, x0, side, f]


def run_examples(args):
    dest = args.out / 'examples'
    t, k0 = EXAMPLE_RULE
    body, report = [], {}
    for n_ds, ds in enumerate(EXAMPLE_SETS):
        rows = load_rows(args, ds)
        pools = {(g, rt_): [] for g in GROUPS for rt_ in ROUTES}
        for r in rows:
            s = r['sets']['d1all']
            for ci, (comp, b) in enumerate(zip(r['comps'], s['best'])):
                rr, kk = (s['cand'][b][0], s['cand'][b][1]) if b >= 0 else (None, None)
                dest_ = 'none' if b < 0 else route(rr, kk, t, k0)
                pools[(comp['group'], dest_)].append(dict(comp, image=r['image'], cand=b, r=rr, k=kk,
                                                         best_px=s['best_px'][ci], route=dest_))
        rng = np.random.default_rng(SEED + n_ds)
        (dest / ds).mkdir(parents=True, exist_ok=True)
        report[ds] = dict(seed=SEED + n_ds, rule=rule_name(t, k0), examples=[],
                          pools={f'{g}/{x}': len(v) for (g, x), v in pools.items()})
        body.append(f'<h2>{SPLIT[ds]}</h2><p class="mute">抽样种子 {SEED + n_ds}；规则 {rule_name(t, k0)}；候选集为默认四源 D1 全部候选。</p>')
        for g in GROUPS:
            for x in ROUTES:
                pool = pools[(g, x)]
                pick = [pool[i] for i in sorted(rng.choice(len(pool), size=min(4, len(pool)), replace=False))]
                body.append(f'<h3>{GROUP_NAMES[g]} → {ROUTE_NAMES[x]}<span class="mute">（共 {len(pool)} 个连通域，抽 {len(pick)} 个）</span></h3>')
                if not pick:
                    body.append('<p class="mute">无。</p>')
                for j, e in enumerate(pick):
                    file = f'{ds}/{g}_{x}_{j}.png'
                    y0, x0, side, f = render(ds, e['image'], e, e['cand'], dest / file)
                    report[ds]['examples'].append(dict(e, file=file, crop=[y0, x0, side, f]))
                    cand_text = (f'候选 r = {e["r"]:.3f}，k = {e["k"]}，覆盖该连通域 {e["best_px"]} px'
                                 if e['cand'] >= 0 else '没有候选覆盖')
                    meta = (f'<b>{html.escape(e["image"])}</b><b>{GROUP_NAMES[g]}</b><b>去向：{ROUTE_NAMES[x]}</b>'
                            f'{cand_text} · 目标面积 {e["target_area"]} px · 连通域 {e["px"]} px · '
                            f'检测器对目标的覆盖率 {100 * e["coverage"]:.1f}%')
                    if ds == 'second':
                        meta += f' · 语义转移 {html.escape(b38.transition_text(ds, e["transition"]))}'
                    meta += f' <span class="mute">· 裁块 y={y0} x={x0} 边长 {side}，放大 {f}×</span>'
                    body.append(f'<div class="ex"><div class="meta">{meta}</div>'
                                f'<div class="hdr"><div>T1</div><div>T2</div><div>真值</div><div>检测器预测</div>'
                                f'<div>叠加</div><div>整图</div></div><img loading="lazy" src="{file}" alt=""></div>')
    legend = ''.join(f'<span class="sw" style="background:rgb{c}"></span>{t_}' for c, t_ in (
        (b38.COLORS['comp'], '该漏检连通域'), (b38.COLORS['rest'], '所属真值目标其余部分（细轮廓）'),
        (b38.COLORS['base'], '检测器 0.5 掩码 D（半透明）'), (CAND_COLOR, '决定去向的候选的完整掩码轮廓')))
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>分流规则抽样审查</title>
<style>{b38.STYLE}
.top{{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:6px 0;font-size:13px}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#161618;--fg:#eee;--mute:#aaa;--line:#333;--card:#202024}}}}
:root[data-theme="dark"]{{--bg:#161618;--fg:#eee;--mute:#aaa;--line:#333;--card:#202024}}
</style></head><body>
<h1>实验 8 补充 2：分流规则抽样审查</h1>
<p>候选 c 取完整掩码（成员原始掩码之并，同 exp8s 第 6 步）；D 为检测器 0.5 掩码。
r = |D ∩ c| / |c|，k = D ∩ c 中 ≥ 5 像素的 8 连通域个数。规则 R(0.1)：r &lt; 0.1 去补漏，否则去精修。
每个漏检连通域由覆盖其像素最多的候选决定去向；没有候选覆盖记为未覆盖。
格同 exp8s batch34（③ 按检测器对目标的覆盖率再分 10–30% 与 ≥ 30%）。不训练，不跑任何模型。</p>
<p>每行从左到右：T1、T2、真值（白色为该目标，灰色为其他真值）、检测器预测、叠加图（底图 T2）、整图小图（黄框为裁块）。
裁块以目标为中心，边长取目标外接框长边的 1.5 倍（至少 64 像素）；候选轮廓在裁块内截断，整图小图显示完整候选。</p>
<div class="top">{legend}</div>
{''.join(body)}
</body></html>"""
    (dest / 'index.html').write_text(page)
    (dest / 'samples.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({ds: v['pools'] for ds, v in report.items()}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('route', 'merge', 'examples'))
    p.add_argument('--dataset', choices=DATASETS)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.stage == 'examples':
        run_examples(args)
    else:
        {'route': run_route, 'merge': run_merge}[args.stage](args, args.dataset)


if __name__ == '__main__':
    main()
