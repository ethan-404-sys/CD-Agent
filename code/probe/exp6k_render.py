"""exp6j 同款 A 近景、B 整图与人工审核接触图。"""
from exp6k_common import *
from PIL import ImageDraw, ImageFont


def render(a):
    crop_box, render_pair = load_crop_functions()
    items = {}
    for kind in a.sets:
        for row in read(manifest_path(a, kind)):
            items[(row['split'], row['cid'])] = row
    groups = {}
    for row in items.values():
        groups.setdefault((row['split'], row['image']), []).append(row)
    groups = sorted(groups.items())
    count = 0
    inventories = {}
    for group_index, ((split, name), rows) in enumerate(groups):
        if group_index % a.shards != a.shard:
            continue
        if split not in inventories:
            inventories[split] = inventory(a.dataset, split, masks=True)
        z = inventories[split]
        image_id = z['images'].tolist().index(name)
        by_unit = {int(z['id'][j]): j for j in image_indices(z, image_id)}
        rgb = rgb_pair(a.dataset, split, name)
        shape = rgb[0].shape[:2]
        gt = load_label(upstream.raw_dataset(a.dataset, split), name) if split == 'train' else None
        sem = semantics(name) if split == 'train' and a.dataset == 'second' else None
        palette = semantic_spec()[2] if sem is not None else None
        for row in rows:
            paths = inputs_for(a.out, row)
            if row['inputs'] != paths:
                raise ValueError('清单渲染路径与 --out 不一致')
            j = by_unit[row['unit_id']]
            box = list(map(int, z['bbox'][j]))
            if box != row['bbox']:
                raise ValueError('渲染 bbox 与冻结候选不一致')
            mask = np.zeros(shape, bool)
            x0, y0, x1, y1 = box
            mask[y0:y1, x0:x1] = unpack(z, j)
            near = crop_box(box, shape)
            pair = render_pair(rgb, mask, None, near)
            rendered = dict(zip(('t1', 't2'), pair))
            for key_, array in zip(('full_t1', 'full_t2'), rgb):
                im = Image.fromarray(array)
                ImageDraw.Draw(im).rectangle((x0, y0, x1-1, y1-1), outline='red', width=2)
                rendered[key_] = im
            thumb = Image.fromarray(rgb[1])
            thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)
            sx, sy = thumb.width/shape[1], thumb.height/shape[0]
            ImageDraw.Draw(thumb).rectangle((x0*sx, y0*sy, (x1-1)*sx, (y1-1)*sy), outline='red', width=2)
            rendered['thumb'] = thumb
            if gt is not None:
                rendered['mask'] = Image.fromarray(gt.astype(np.uint8)*255)
            if sem is not None:
                for key_, array in zip(('sem1', 'sem2'), sem):
                    rendered[key_] = Image.fromarray(palette[array])
            for key_, im in rendered.items():
                path = local(paths[key_])
                path.parent.mkdir(parents=True, exist_ok=True)
                im.save(path, quality=90, subsampling=0)
            count += 1
    write(a.out / a.dataset / 'render_reports' / f'part-{a.shard:03d}.json',
          dict(sets=a.sets, shard=a.shard, shards=a.shards, candidates=count))
    print(f'{a.dataset}: rendered {count}', flush=True)


def sheets(a):
    rows = [r for r in read(manifest_path(a, 'shortlist')) if r['label'] == 0]
    font = ImageFont.truetype(str(a.font), 15)
    folder = local(a.out / a.dataset / 'sheets')
    folder.mkdir(parents=True, exist_ok=True)
    for label, prefix in ((0, 'neg'),):
        selected = [r for r in rows if r['label'] == label]
        for start in range(0, len(selected), 12):
            canvas = Image.new('RGB', (4*400, 3*262), 'white')
            draw = ImageDraw.Draw(canvas)
            for i, row in enumerate(selected[start:start+12]):
                x, y = (i%4)*400+4, (i//4)*262+4
                for k, offset in [('t1', 0), ('t2', 192)]:
                    with Image.open(row['inputs'][k]) as im:
                        canvas.paste(im.resize((192, 192), Image.Resampling.LANCZOS), (x+offset, y))
                line = f"#{row['number']:03d}  label={label}  主格={row['majority']}  面积={row['area']}"
                draw.text((x, y+195), line, fill='black', font=font)
                draw.text((x, y+215), f"D={row['det_mean']:.3f}  {row['source']}", fill='black', font=font)
                if a.dataset == 'second':
                    draw.text((x, y+235), row['change_kind'], fill='black', font=font)
            canvas.save(folder / f'{prefix}_{start//12+1:02d}.jpg', quality=90, subsampling=0)
    path = local(a.out / f'annotations_{a.dataset}.csv')
    # 重新渲染接触图时保留人工标注。
    if not path.exists():
        fields = ['number', 'image', 'cid', 'label', 'pseudo_type', 'rationale', 'gt_doubt', 'doubt_reason']
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] if k in ('number', 'image', 'cid', 'label') else '' for k in fields})
    else:
        with path.open(encoding='utf-8-sig', newline='') as f:
            previous = list(csv.DictReader(f))
        if {(r['image'], r['cid']) for r in previous} != {key(r) for r in rows}:
            raise ValueError('已有审核表与 shortlist 不一致；请换 --out，避免覆盖人工审核')
    print(f'{a.dataset}: sheets + {path}', flush=True)
