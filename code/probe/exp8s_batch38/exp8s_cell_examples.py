"""实验 8 补充四格划分的抽样审查页（只读 batch30 像素底座与 batch34 格标签，不重跑模型）。

阶段：
  index   按图分片，列出 SECOND/SYSU 测试的全部漏检连通域（含 ⑤ 残余），并与 batch34 格标签逐像素核对。
  render  固定种子抽样，画每例一行的 PNG 并写离线 index.html。

抽样（种子 SEED，numpy default_rng）：每个数据集 ①、②、③ 各 8 个连通域；
③ 另抽 4 个检测器覆盖率 10–30% 的目标（每目标取其中一个 ③ 连通域）；
① 另抽 4 个“目标内覆盖为 0、只从外沿碰到检测器掩码”的连通域；⑤ 抽 4 个。补抽与主抽样不重复目标。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, '/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch34')
from exp8s_cells import BASE, OUT, image_list, pixels, touches  # noqa: E402
from exp8s_common import np, rt, gt_label  # noqa: E402
import common  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402

CELLS34 = OUT / 'exp8s_batch34/interim'
DATASETS = ('second', 'sysu')
SEED = 20261024
CELL_NAMES = {1: '① 无信号·贴边', 2: '② 无信号·孤立', 3: '③ 有信号·贴边', 4: '④ 有信号·孤立', 5: '⑤ 残余'}
SIDE = 224
K3 = np.ones((3, 3), bool)
COLORS = dict(comp=(230, 40, 40), base=(40, 140, 255), rest=(255, 210, 0), other=(170, 170, 170),
              contact=(255, 0, 255), box=(255, 210, 0))


def components(mask):
    labels, _ = ndimage.label(mask, K3)
    for k, sl in enumerate(ndimage.find_objects(labels), start=1):
        yield labels == k


def run_index(args, ds):
    dest = args.out / 'index' / f'{ds}_{args.shard:03d}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    items = [(i, n) for i, n in enumerate(image_list(ds)[:args.limit or None]) if i % args.shards == args.shard]
    with dest.with_suffix('.tmp').open('w') as fh:
        for index, name in items:
            stem = Path(name).stem
            gt, base = pixels(BASE[ds] / 'interim/exp8s' / ds / stem / 'pixels.npz')
            with np.load(CELLS34 / ds / (stem + '.npz')) as z:
                cell = z['cell']
            gl, count, transitions = gt_label(ds, name, gt)
            check = np.zeros(gt.shape, np.uint8)
            for ident in range(1, count + 1):
                original = gl == ident
                area = int(original.sum())
                inside = int(base[original].sum())
                covered = inside / area
                for mask in components(original & ~base):
                    size = int(mask.sum())
                    touch = touches(mask, base)
                    if area < rt.e4.MIN_TARGET or size < rt.e4.MIN_TARGET:
                        c = 5
                    else:
                        c = (3 if covered >= .1 else 1) + (0 if touch else 1)
                    check[mask] = c
                    values = np.unique(cell[mask])
                    if values.tolist() != [c]:
                        raise ValueError(f'{ds}/{name}: target {ident} cell {c} vs batch34 {values.tolist()}')
                    ys, xs = np.nonzero(mask)
                    fh.write(json.dumps(dict(
                        index=index, image=name, target=ident, cell=c, target_area=area, comp_area=size,
                        coverage=covered, inside=inside, touch=touch, transition=transitions.get(ident),
                        seed=[int(ys[0]), int(xs[0])]), ensure_ascii=False) + '\n')
            if not np.array_equal(check, cell):
                raise ValueError(f'{ds}/{name}: recomputed cell map differs from batch34')
    dest.with_suffix('.tmp').replace(dest)


def load_index(args, ds):
    """分片整片原子写入；没有漏检的图不产生行，所以按分片齐全与图序号归属核对完整性。"""
    files = sorted((args.out / 'index').glob(f'{ds}_*.jsonl'))
    if [f.name for f in files] != [f'{ds}_{k:03d}.jsonl' for k in range(len(files))]:
        raise ValueError(f'{ds}: index shards are incomplete')
    rows = []
    for k, f in enumerate(files):
        part = [json.loads(line) for line in f.read_text().splitlines()]
        if any(r['index'] % len(files) != k for r in part):
            raise ValueError(f'{f}: rows from another shard')
        rows += part
    return sorted(rows, key=lambda r: (r['index'], r['target'], r['seed'])), len(files)


def sample(rows, rng):
    picks, used = [], set()

    def draw(group, pool, k):
        pool = [r for r in pool if (r['image'], r['target']) not in used]
        chosen = [pool[i] for i in sorted(rng.choice(len(pool), size=min(k, len(pool)), replace=False))]
        for r in chosen:
            used.add((r['image'], r['target']))
            picks.append(dict(r, group=group))
        return len(pool)

    pools = {}
    for c in (1, 2, 3):
        pools[f'cell{c}'] = draw(f'cell{c}', [r for r in rows if r['cell'] == c], 8)
    # 每个目标只取一个 ③ 连通域，按目标均匀抽。
    low = {}
    for r in rows:
        if r['cell'] == 3 and .1 <= r['coverage'] < .3:
            low.setdefault((r['image'], r['target']), []).append(r)
    targets = [v[int(rng.integers(len(v)))] for _, v in sorted(low.items())]
    pools['cell3_cov10_30'] = draw('cell3_cov10_30', targets, 4)
    pools['cell1_zero_inside'] = draw('cell1_zero_inside', [r for r in rows if r['cell'] == 1 and r['inside'] == 0], 4)
    pools['cell5'] = draw('cell5', [r for r in rows if r['cell'] == 5], 4)
    return picks, pools


def upscale(mask, f):
    return np.kron(mask.astype(np.uint8), np.ones((f, f), np.uint8)).astype(bool)


def contour(mask):
    return mask & ~ndimage.binary_erosion(mask, K3)


def blend(img, mask, color, alpha):
    out = img.astype(np.float32)
    out[mask] = out[mask] * (1 - alpha) + np.asarray(color, np.float32) * alpha
    return out.astype(np.uint8)


def crop_box(target, shape):
    ys, xs = np.nonzero(target)
    cy, cx = (ys.min() + ys.max()) / 2, (xs.min() + xs.max()) / 2
    side = int(min(max(64, 1.5 * max(np.ptp(ys) + 1, np.ptp(xs) + 1)), *shape))
    y0 = int(np.clip(round(cy - side / 2), 0, shape[0] - side))
    x0 = int(np.clip(round(cx - side / 2), 0, shape[1] - side))
    return y0, x0, side


def render(ds, row, dest):
    name = row['image']
    stem = Path(name).stem
    gt, base = pixels(BASE[ds] / 'interim/exp8s' / ds / stem / 'pixels.npz')
    gl, _, _ = gt_label(ds, name, gt)
    target = gl == row['target']
    labels, _ = ndimage.label(target & ~base, K3)
    comp = labels == labels[tuple(row['seed'])]
    if int(comp.sum()) != row['comp_area']:
        raise ValueError(f'{ds}/{name}: component mismatch')
    t1, t2 = (common.load_rgb(ds, name, v) for v in ('t1', 't2'))
    y0, x0, side = crop_box(target, gt.shape)
    f = max(1, SIDE // side)
    sl = (slice(y0, y0 + side), slice(x0, x0 + side))

    def up_rgb(img):
        return np.asarray(Image.fromarray(img[sl]).resize((side * f,) * 2, Image.NEAREST))

    def fit(img):
        return np.asarray(Image.fromarray(img).resize((SIDE, SIDE), Image.NEAREST if side * f >= SIDE else Image.BILINEAR))

    def mask_rgb(m, other=None):
        img = np.zeros(m.shape + (3,), np.uint8)
        if other is not None:
            img[other] = 90
        img[m] = 255
        return img

    contact = ndimage.binary_dilation(comp, K3) & base
    up = {k: upscale(v[sl], f) for k, v in dict(comp=comp, base=base, target=target, gt=gt, contact=contact).items()}
    over = blend(up_rgb(t2), up['base'], COLORS['base'], .45)
    over = blend(over, up['comp'], COLORS['comp'], .8)
    over[contour(up['gt'] & ~up['target'])] = COLORS['other']
    over[contour(up['target']) & ~up['comp']] = COLORS['rest']
    over[up['contact']] = COLORS['contact']
    panels = [up_rgb(t1), up_rgb(t2),
              up_rgb(mask_rgb(target, gt & ~target)), up_rgb(mask_rgb(base)), over]
    thumb = blend(t2, base, COLORS['base'], .45)
    thumb = blend(thumb, comp, COLORS['comp'], .9)
    thumb = np.asarray(Image.fromarray(thumb).resize((SIDE, SIDE), Image.BILINEAR)).copy()
    s = SIDE / gt.shape[0]
    im = Image.fromarray(thumb)
    ImageDraw.Draw(im).rectangle([x0 * s, y0 * s, (x0 + side) * s - 1, (y0 + side) * s - 1],
                                 outline=COLORS['box'], width=2)
    panels.append(np.asarray(im))
    gap = 4
    strip = np.full((SIDE, SIDE * len(panels) + gap * (len(panels) - 1), 3), 255, np.uint8)
    for i, p in enumerate(panels):
        strip[:, i * (SIDE + gap): i * (SIDE + gap) + SIDE] = fit(p) if p.shape[0] != SIDE else p
    Image.fromarray(strip).save(dest, optimize=True)
    return dict(crop=[y0, x0, side], scale=f, contact_pixels=int(contact.sum()))


GROUPS = (('cell1', '①：随机 8 个连通域'), ('cell2', '②：随机 8 个连通域'), ('cell3', '③：随机 8 个连通域'),
          ('cell3_cov10_30', '③ 补抽：检测器覆盖率 10–30% 的目标（每目标一个 ③ 连通域）'),
          ('cell1_zero_inside', '① 补抽：目标内覆盖为 0，只从外沿碰到检测器掩码'),
          ('cell5', '⑤ 残余：随机 4 个连通域'))
DS_NAMES = dict(second='SECOND 测试', sysu='SYSU-CD 测试')
STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--mute:#666;--line:#ddd;--card:#f7f7f8}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#eee;--mute:#aaa;--line:#333;--card:#202024}}
body{background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif;margin:0 auto;max-width:1460px;padding:16px}
h1{font-size:22px}h2{font-size:18px;margin-top:32px;border-bottom:1px solid var(--line)}h3{font-size:15px;margin-top:22px}
.ex{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px;margin:10px 0}
.meta{font-size:13px;margin-bottom:6px}.meta b{margin-right:10px}.mute{color:var(--mute)}
.hdr{display:grid;grid-template-columns:repeat(6,1fr);gap:4px;max-width:1364px;font-size:12px;color:var(--mute);text-align:center}
img{display:block;width:100%;max-width:1364px;height:auto}
.sw{display:inline-block;width:12px;height:12px;vertical-align:-1px;margin:0 4px 0 12px;border:1px solid #888}
table{border-collapse:collapse}td,th{border:1px solid var(--line);padding:3px 8px;text-align:right}
"""


def transition_text(ds, t):
    if not t:
        return '—'
    classes = common.DATASETS['second']['classes']
    return f'{classes.get(t[0], t[0])} → {classes.get(t[1], t[1])}'


def run_render(args):
    dest = args.out / 'examples'
    report = {}
    body = []
    for ds in DATASETS:
        rows, shards = load_index(args, ds)
        if shards != args.shards:
            raise ValueError(f'{ds}: {shards} index shards, expected {args.shards}')
        rng = np.random.default_rng(SEED + DATASETS.index(ds))
        picks, pools = sample(rows, rng)
        (dest / ds).mkdir(parents=True, exist_ok=True)
        counts = {c: sum(r['cell'] == c for r in rows) for c in range(1, 6)}
        report[ds] = dict(seed=SEED + DATASETS.index(ds), pool_sizes=pools, components=counts, examples=[])
        body.append(f'<h2>{DS_NAMES[ds]}</h2><p class="mute">抽样种子 {SEED + DATASETS.index(ds)}；'
                    f'连通域总数 ① {counts[1]}、② {counts[2]}、③ {counts[3]}、④ {counts[4]}、⑤ {counts[5]}。</p>')
        for group, title in GROUPS:
            sel = [p for p in picks if p['group'] == group]
            body.append(f'<h3>{title}<span class="mute">（候选 {pools[group]} 个，抽 {len(sel)} 个）</span></h3>')
            if not sel:
                body.append('<p class="mute">不存在这种情况。</p>')
            for k, r in enumerate(sel):
                file = f'{ds}/{group}_{k}.png'
                extra = render(ds, r, dest / file)
                report[ds]['examples'].append(dict(r, file=file, **extra))
                meta = (f'<b>{html.escape(r["image"])}</b><b>{CELL_NAMES[r["cell"]]}</b>'
                        f'目标面积 {r["target_area"]} px · 连通域面积 {r["comp_area"]} px · '
                        f'检测器覆盖率 {100 * r["coverage"]:.1f}%（{r["inside"]} px） · '
                        f'贴边 {"是" if r["touch"] else "否"}（接触 {extra["contact_pixels"]} px）')
                if ds == 'second':
                    meta += f' · 语义转移 {html.escape(transition_text(ds, r["transition"]))}'
                y0, x0, side = extra['crop']
                meta += f' <span class="mute">· 裁块 y={y0} x={x0} 边长 {side}，放大 {extra["scale"]}×</span>'
                body.append(f'<div class="ex"><div class="meta">{meta}</div><img loading="lazy" src="{file}" '
                            f'alt="{html.escape(r["image"])} {CELL_NAMES[r["cell"]]}"></div>')
    legend = ''.join(f'<span class="sw" style="background:rgb{COLORS[k]}"></span>{t}' for k, t in (
        ('comp', '该漏检连通域'), ('rest', '所属真值目标其余部分（细轮廓）'), ('base', '检测器 0.5 掩码（半透明）'),
        ('contact', '连通域外扩 1 像素后与掩码的接触处'), ('other', '其他真值目标（灰色细轮廓）')))
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>四格划分抽样审查</title>
<style>{STYLE}</style></head><body>
<h1>实验 8 补充：四格划分抽样审查</h1>
<p>数据：exp8s batch34 的逐像素格标签（<code>interim/&lt;划分&gt;/&lt;图&gt;.npz</code>），与 batch30 像素底座的真值、检测器 0.5 掩码。
不训练，不重跑任何模型。生成时已逐像素核对：按下列定义重算的格标签与 batch34 完全一致。</p>
<p>定义：真值目标为 objects.gt_label（SECOND 按两期语义转移拆）；检测器覆盖目标像素 &lt; 10% 为无信号；
漏检连通域为目标内 GT &amp; ~pred 的 8 连通域（≥ 20 像素）；连通域外扩 1 像素（3×3）后与检测器掩码相交为贴边。
① 无信号·贴边 ② 无信号·孤立 ③ 有信号·贴边 ④ 有信号·孤立（真值目标连通，按构造为空）；
不足 20 像素的连通域或面积 &lt; 20 的目标为 ⑤ 残余。</p>
<p>每行从左到右：T1、T2、真值（白色为该目标，灰色为其他真值）、检测器预测、格标签叠加图（底图 T2）、整图小图（黄框为裁块）。
裁块以该目标为中心，边长取目标外接框长边的 1.5 倍（至少 64 像素），放大为整数倍。</p>
<p>叠加图图例：{legend}</p>
<div class="hdr"><div>T1</div><div>T2</div><div>真值</div><div>检测器预测</div><div>格标签叠加</div><div>整图</div></div>
{''.join(body)}
</body></html>"""
    (dest / 'index.html').write_text(page)
    (dest / 'samples.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({ds: dict(pool_sizes=v['pool_sizes'], components=v['components']) for ds, v in report.items()},
                     ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('index', 'render'))
    p.add_argument('--dataset', choices=DATASETS)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    run_index(args, args.dataset) if args.stage == 'index' else run_render(args)


if __name__ == '__main__':
    main()
