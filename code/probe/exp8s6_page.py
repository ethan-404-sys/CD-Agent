"""实验 8 补充 6 第 2 部分：② 覆盖审核页（只读缓存）。

每个数据集测试部分抽 10 张含 ② 的图（种子 20261026）：
  ② 目标数最多的三分之一里抽 3 张，最少的三分之一里抽 3 张，
  含大块 ②（最大 ② 目标面积在含 ② 图的前 10%）里抽 2 张，其余图里抽 2 张；不重复。
用 frozen6.json 冻结的过滤方案。叠加图画在 T2 上：D 白色细轮廓，过滤后补漏候选浅灰半透明，
② 目标按被候选覆盖的比例上色：绿 ≥ 50%（算覆盖）、橙 (0, 50%)、红 = 0，并标编号。
② 目标与 D 没有重叠（exp8s5 全量自检），所以覆盖比例就是候选新增并集占目标面积的比例，
与 exp8s5 的对象口径一致。
"""
import html
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exp8s6_filter as f6  # noqa: E402
from exp8s6_filter import *  # noqa: E402,F401,F403
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

HERE = Path(__file__).resolve().parent
RUN = HERE / 'run'
PAGE = HERE / 'page'
SEED = 20261026
FONT = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 11)
COLORS = dict(green=(46, 204, 113), orange=(255, 159, 28), red=(235, 59, 59))
DS_NAME = dict(second='SECOND', sysu='SYSU')


def pick(ds):
    records = []
    for name in manifest(ds)['images']['test']:
        r = read(RUN / ds / 'filter6' / 'test' / (Path(name).stem + '.json'))
        n2 = r['schemes']['F0']['objects2']
        if n2:
            records.append((name, n2))
    rng = random.Random(SEED)
    by_count = sorted(records, key=lambda x: (x[1], x[0]))
    third = len(by_count) // 3
    chosen = []

    def take(pool, k, why):
        pool = [p for p in pool if p[0] not in {c[0] for c in chosen}]
        for p in rng.sample(pool, min(k, len(pool))):
            chosen.append((p[0], why))
    take(by_count[-third:], 3, '② 目标多')
    take(by_count[:third], 3, '② 目标少')
    # 大块 ②：需要目标面积，只在候选池里算
    sizes = []
    for name, _ in records:
        d = geometry(ds, 'test', name)
        areas = [int(d['ga'][r['target']]) for r in d['rows'] if r['cell'] == 2]
        sizes.append((name, max(areas)))
    sizes.sort(key=lambda x: (-x[1], x[0]))
    take(sizes[:max(1, len(sizes) // 10)], 2, '含大块 ②')
    take(records, 2, '随机')
    return chosen, len(records)


def contour(mask):
    return mask & ~ndi.binary_erosion(mask, structure=EIGHT)


def render(ds, name, scheme):
    d = f6.s5.load(ds, 'test', name)
    raw = c.raw_dataset(ds, 'test')
    t1, t2 = (np.asarray(load_rgb(raw, name, v)) for v in ('t1', 't2'))
    union = np.zeros_like(d['gt'])
    for u in f6.units(d, scheme):
        add_mask(union, u)
    over = t2.astype(np.float32).copy()
    over[union] = .65 * over[union] + .35 * 210
    far = f6.targets2(d)
    rows = []
    gl = d['gl']
    for k, t in enumerate(sorted(far), 1):
        mask = gl == t
        area = int(mask.sum())
        cov = float((mask & union).sum() / area)
        color = 'green' if cov >= .5 else 'orange' if cov > 0 else 'red'
        over[mask] = .45 * over[mask] + .55 * np.array(COLORS[color])
        over[contour(mask)] = COLORS[color]
        ys, xs = np.nonzero(mask)
        rows.append(dict(no=k, area=area, distance=far[t], coverage=cov, color=color,
                         xy=(int(xs.min()), int(ys.min()))))
    over[contour(d['base'])] = 255
    im = Image.fromarray(np.clip(over, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(im)
    for r in rows:
        x, y = r['xy']
        text = str(r['no'])
        box = draw.textbbox((x, max(0, y - 12)), text, font=FONT)
        draw.rectangle(box, fill=(20, 20, 20))
        draw.text((x, max(0, y - 12)), text, font=FONT, fill=(255, 255, 255))
    stem = Path(name).stem
    out = PAGE / 'img' / ds
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for tag, arr in (('t1', t1), ('t2', t2), ('overlay', np.asarray(im))):
        p = out / f'{stem}-{tag}.jpg'
        Image.fromarray(arr).save(p, 'JPEG', quality=85, optimize=True)
        paths.append(str(p.relative_to(PAGE)))
    return paths, rows


def table(rows):
    lines = ['<div class="scroll"><table><thead><tr><th>编号</th><th>面积（像素）</th><th>到 D 的距离（像素）</th>'
             '<th>被候选覆盖</th><th>算覆盖</th></tr></thead><tbody>']
    for r in rows:
        dist = '图内无 D' if r['distance'] is None else str(r['distance'])
        far = r['distance'] is None or r['distance'] > FAR
        mark = ' <span class="far" title="距离 > 5 像素">远</span>' if far else ''
        lines.append(f'<tr><td><span class="dot {r["color"]}"></span>{r["no"]}</td><td>{r["area"]:,}</td>'
                     f'<td>{dist}{mark}</td><td>{100 * r["coverage"]:.0f}%</td>'
                     f'<td>{"是" if r["coverage"] >= .5 else "否"}</td></tr>')
    lines.append('</tbody></table></div>')
    return '\n'.join(lines)


CSS = """
:root{--bg:#f7f8f6;--fg:#1f2428;--muted:#5f6870;--line:#d6dad8;--surface:#eceeeb;--accent:#1d6f7a;
--green:#2ecc71;--orange:#ff9f1c;--red:#eb3b3b;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15191c;--fg:#e6e9eb;--muted:#a9b2b9;--line:#3b4349;--surface:#20262a;--accent:#7fd3dc;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#15191c;--fg:#e6e9eb;--muted:#a9b2b9;--line:#3b4349;--surface:#20262a;--accent:#7fd3dc;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 "Noto Sans SC",system-ui,-apple-system,"PingFang SC",sans-serif;
padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:24px 64px}
main{max-width:1240px;margin:0 auto;min-width:0}
h1{font-size:26px;margin:0 0 6px;text-wrap:balance}h2{font-size:19px;margin:28px 0 8px}
p{max-width:90ch;margin:6px 0}.muted{color:var(--muted);font-size:14px}
nav{display:flex;gap:18px;border-bottom:1px solid var(--line);margin:18px 0 8px}
nav a{color:var(--fg);text-decoration:none;padding:8px 2px;border-bottom:3px solid transparent;font-weight:600}
nav a[aria-selected="true"]{border-color:var(--accent);color:var(--accent)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px;margin:8px 0}
.dot{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.dot.green{background:var(--green)}.dot.orange{background:var(--orange)}.dot.red{background:var(--red)}
.dot.cand{background:#c8c8c8}.dot.det{background:transparent;border:2px solid var(--fg)}
.stats{font-variant-numeric:tabular-nums;font-size:15px;background:var(--surface);padding:10px 14px;border-radius:6px;margin:10px 0}
.row{border-top:1px solid var(--line);padding:14px 0 6px}
.imgs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.imgs figure{margin:0;min-width:0}.imgs img{width:100%;max-width:100%;height:auto;display:block;image-rendering:auto}
figcaption{font-size:13px;color:var(--muted)}
.scroll{max-width:100%;overflow-x:auto;margin-top:8px}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}
th,td{padding:4px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
.far{font-size:12px;color:var(--accent);border:1px solid var(--accent);border-radius:3px;padding:0 3px;margin-left:4px}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (max-width:640px){.imgs{grid-template-columns:minmax(0,1fr)}}
"""


def main():
    sections, tabs = [], []
    for ds in ('second', 'sysu'):
        frozen = read(RUN / ds / 'frozen6.json')['scheme']
        total = read(RUN / ds / 'filter6_test.json')['schemes'][frozen]
        chosen, pool = pick(ds)
        blocks = []
        sample = []
        for name, why in chosen:
            paths, rows = render(ds, name, frozen)
            covered = sum(r['coverage'] >= .5 for r in rows)
            sample.append(dict(image=name, reason=why, targets=len(rows), covered=covered))
            figs = ''.join(f'<figure><a href="{p}" target="_blank" rel="noopener"><img src="{p}" loading="lazy" alt="{cap}"></a>'
                           f'<figcaption>{cap}</figcaption></figure>' for p, cap in zip(paths, ('T1', 'T2', 'T2 叠加')))
            blocks.append(f'<div class="row"><h3>{html.escape(name)} <span class="muted">· {why} · ② 目标 {len(rows)} 个，'
                          f'覆盖 {covered} 个</span></h3><div class="imgs">{figs}</div>{table(rows)}</div>')
        write(PAGE / f'samples_{ds}.json', dict(seed=SEED, scheme=frozen, images_with_cell2=pool, sample=sample))
        tabs.append(f'<a href="#{ds}" data-tab="{ds}">{DS_NAME[ds]}</a>')
        sections.append(
            f'<section id="sec-{ds}" data-ds="{ds}"><p class="stats">{DS_NAME[ds]} 测试部分（过滤方案 {frozen}）：'
            f'② 目标 {total["objects2"]:,} 个，被候选覆盖 {total["hits2"]:,} 个，'
            f'占 {100 * total["object_recall2"]:.1f}%；每图补漏候选 {total["candidates_per_image"]:.1f} 个。</p>'
            + ''.join(blocks) + '</section>')
    page = f"""<title>② 覆盖审核</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;600&display=swap">
<style>{CSS}</style>
<main>
<h1>② 漏检覆盖审核</h1>
<p>每张测试图在 T2 上标出全部 ② 漏检目标（无信号·孤立），颜色表示过滤后的补漏候选盖住了它多少。点击图片看原分辨率。</p>
<div class="legend"><span><span class="dot green"></span>覆盖 ≥ 50%（算覆盖）</span><span><span class="dot orange"></span>覆盖 0–50%</span>
<span><span class="dot red"></span>完全没盖住</span><span><span class="dot cand"></span>补漏候选（过滤后）</span><span><span class="dot det"></span>检测结果 D 轮廓</span></div>
<p class="muted">每个数据集抽 10 张含 ② 的测试图（种子 {SEED}）：② 目标多的 3 张、少的 3 张、含大块 ② 的 2 张、随机 2 张。“远”表示到 D 的距离大于 5 像素。</p>
<nav>{''.join(tabs)}</nav>
{''.join(sections)}
</main>
<script>
(function(){{
  var secs=document.querySelectorAll('section[data-ds]'), links=document.querySelectorAll('nav a');
  function show(ds){{secs.forEach(function(s){{s.hidden=s.dataset.ds!==ds}});links.forEach(function(a){{a.setAttribute('aria-selected',a.dataset.tab===ds)}})}}
  links.forEach(function(a){{a.addEventListener('click',function(e){{e.preventDefault();show(a.dataset.tab);history.replaceState(null,'','#'+a.dataset.tab)}})}});
  var h=location.hash.slice(1); show(h==='sysu'?'sysu':'second');
}})();
</script>
"""
    (PAGE / 'index.html').write_text(page)
    print('page written', flush=True)


if __name__ == '__main__':
    main()
