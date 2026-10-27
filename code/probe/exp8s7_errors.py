"""Validation-only disagreement gallery and empty human annotation template."""
from exp8s7_common import *
import csv
import html
from PIL import Image, ImageDraw


def threshold(y, values):
    if len(np.unique(y)) != 2:
        raise ValueError('Threshold selection needs positive and negative validation units')
    weight = float((y == 0).sum() / (y == 1).sum())
    rank = c.b40.Ranking(y, values)
    positives, negatives = rank.counts(np.where(y == 1, weight, 1.))
    tp, fp = np.cumsum(positives), np.cumsum(negatives)
    f1 = 2*tp/(tp+fp+tp[-1])
    ix = int(np.argmax(f1))
    score = values[rank.order][rank.starts[ix]]
    return dict(threshold=float(score), weighted_f1=float(f1[ix]), positive_weight=weight,
                rule='validation labeled units; positive sample weight=Nneg/Npos, negative=1; maximum binary F1; highest threshold tie')


def outlined(rgb, mask):
    result = rgb.copy()
    edge = mask & ~ndi.binary_erosion(mask, structure=s5.EIGHT)
    result[edge] = [255, 40, 40]
    return result


def render(a, z, j, path):
    image_id = z['image_id'][j]; name = str(z['images'][image_id])
    raw = c.raw_dataset(a.dataset, 'val')
    t1, t2 = [s5.load_rgb(raw, name, view) for view in ('t1', 't2')]
    _, _, prob, _ = s5.pixels(a.dataset, 'val', name)
    u = unpack(z, j); h, w = t2.shape[:2]
    mask = np.zeros((h, w), bool); s5.add_mask(mask, u)
    x0, y0, x1, y1 = u['bbox']; pad = max(8, int(max(x1-x0, y1-y0)*.15))
    close = (max(0, x0-pad), max(0, y0-pad), min(w, x1+pad), min(h, y1+pad))
    pad = max(32, max(x1-x0, y1-y0))
    context = (max(0, x0-pad), max(0, y0-pad), min(w, x1+pad), min(h, y1+pad))
    heat = np.stack([prob, .3*(1-np.abs(2*prob-1)), 1-prob], axis=-1)
    heat = np.clip(heat*255, 0, 255).astype(np.uint8)
    panels = [(t1, close, 'T1 close'), (t2, close, 'T2 close'),
              (t2, context, 'T2 context'), (heat, context, 'D probability (blue=0 red=1)')]
    canvas = Image.new('RGB', (4*320, 350), 'white'); draw = ImageDraw.Draw(canvas)
    for i, (rgb, box, title) in enumerate(panels):
        panel = Image.fromarray(outlined(rgb, mask)).crop(box)
        scale = min(320/panel.width, 320/panel.height)
        panel = panel.resize((max(1, round(panel.width*scale)), max(1, round(panel.height*scale))),
                             Image.Resampling.LANCZOS)
        canvas.paste(panel, (i*320+(320-panel.width)//2, 25+(320-panel.height)//2))
        draw.text((i*320+5, 5), title, fill='black')
    canvas.save(path, quality=90)


def main():
    p = parser(__doc__)
    p.add_argument('--scores', type=Path, required=True)
    p.add_argument('--count', type=int, default=12)
    p.add_argument('--seed', type=int, default=20261026)
    a = setup(p)
    if a.count < 1:
        p.error('--count must be positive')
    z = load_units(a, 'val')
    judge = align_scores(a.scores / 'val.npz', z)
    take = z['label'] >= 0
    jt, dt = [threshold(z['label'][take], v[take]) for v in (judge, z['dscore'])]
    jc = (judge >= jt['threshold']) == z['label']
    dc = (z['dscore'] >= dt['threshold']) == z['label']
    groups = dict(judge_right_D_wrong=take & jc & ~dc, judge_wrong_D_right=take & ~jc & dc)
    dest = local(a.out / a.dataset / 'errors' / a.scores.name)
    if dest.exists():
        raise FileExistsError(f'{dest}: preserve existing annotations')
    dest.mkdir(parents=True)
    rng = np.random.default_rng(a.seed)
    rows, selections = [], {}
    for group, mask in groups.items():
        indices = np.flatnonzero(mask)
        chosen = rng.choice(indices, min(a.count, len(indices)), replace=False)
        selections[group] = dict(available=len(indices), selected=len(chosen))
        for number, j in enumerate(chosen, 1):
            file = f'{group}_{number:02d}.jpg'
            render(a, z, int(j), dest / file)
            rows.append(dict(group=group, image=str(z['images'][z['image_id'][j]]), id=int(z['id'][j]),
                             label=int(z['label'][j]), majority=int(z['majority'][j]),
                             precision=float(z['precision'][j]), judge_score=float(judge[j]), d_score=float(z['dscore'][j]),
                             file=file, category='', explanation=''))
    fields = ['group', 'image', 'id', 'label', 'majority', 'precision', 'judge_score', 'd_score', 'file', 'category', 'explanation']
    with (dest / 'annotations.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    lines = ['<!doctype html><meta charset="utf-8"><title>验证错误样例</title>',
             '<style>body{font:16px sans-serif;max-width:1320px;margin:24px auto}img{width:100%}article{margin:32px 0}</style>',
             '<h1>验证错误样例</h1>', '<p>红色轮廓为过滤后的新增区域。阈值按各自验证集加权二分类 F1 最大值选择；正例权重=负例数/正例数。</p>',
             '<p>人工分类填写 annotations.csv：认错地物 / 季节或色调 / 配准错位 / 真值可疑 / 太小看不清 / 其他。category 和 explanation 留空等待审核。</p>',
             '<p>'+html.escape(json.dumps(selections, ensure_ascii=False))+'</p>']
    for r in rows:
        caption = f"{r['group']}：{r['image']} 单元 {r['id']}，标签 {r['label']}，判断器 {r['judge_score']:.4f}，D {r['d_score']:.4f}。视觉原因待人工填写。"
        lines.append(f'<article><p>{html.escape(caption)}</p><img src="{r["file"]}" alt="{html.escape(caption)}"></article>')
    (dest / 'index.html').write_text('\n'.join(lines))
    write(dest / 'selection.json', dict(judge=jt, D=dt, groups=selections, seed=a.seed, split='val'))
    print(dest / 'index.html', flush=True)


if __name__ == '__main__':
    main()
