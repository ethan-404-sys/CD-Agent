"""复用 exp6j A近景及 exp6k 看图表；B真值整图仅用于示例。"""
from exp8s8_icl_common import *
from PIL import ImageDraw
import exp6k_render as k_render


def render(a):
    crop_box, render_pair = load_crop_functions()
    items = {}
    for kind in a.sets:
        for row in read(manifest_path(a, kind)):
            items[(row['split'], row['cid'])] = row
    groups = {}
    for row in items.values():
        groups.setdefault((row['split'], row['image']), []).append(row)
    inventories, count = {}, 0
    for group_index, ((split, name), rows) in enumerate(sorted(groups.items())):
        if group_index % a.shards != a.shard:
            continue
        if split not in inventories:
            inventories[split] = load_units(a, split)
        z = inventories[split]
        image_id = z['images'].tolist().index(name)
        by_unit = {int(z['id'][j]): j for j in image_rows(z, image_id)}
        rgb = rgb_pair(a.dataset, split, name)
        shape = rgb[0].shape[:2]
        gt = load_label(raw_dataset(a.dataset, split), name) if any(r['role']=='demo' for r in rows) else None
        for row in rows:
            paths = inputs_for(a.out, row)
            if paths != row['inputs']:
                raise ValueError('清单渲染路径与out不一致')
            u = unpack(z, by_unit[row['unit_id']])
            box = list(map(int, u['bbox']))
            if box != row['bbox']:
                raise ValueError('渲染bbox不符')
            x0, y0, x1, y1 = box
            mask = np.zeros(shape, bool)
            mask[y0:y1, x0:x1] = u['mask']
            rendered = dict(zip(('t1', 't2'), render_pair(rgb, mask, None, crop_box(box, shape))))
            thumb = Image.fromarray(rgb[1])
            thumb.thumbnail((256, 256), Image.Resampling.LANCZOS)
            sx, sy = thumb.width/shape[1], thumb.height/shape[0]
            ImageDraw.Draw(thumb).rectangle((x0*sx, y0*sy, (x1-1)*sx, (y1-1)*sy), outline='red', width=2)
            rendered['thumb'] = thumb
            if row['role']=='demo':
                for k, array in zip(('full_t1', 'full_t2'), rgb):
                    im = Image.fromarray(array)
                    ImageDraw.Draw(im).rectangle((x0, y0, x1-1, y1-1), outline='red', width=2)
                    im.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    rendered[k] = im
                im = Image.fromarray(gt.astype(np.uint8)*255)
                im.thumbnail((1024, 1024), Image.Resampling.NEAREST)
                rendered['mask'] = im
            for k, im in rendered.items():
                path = local(paths[k])
                path.parent.mkdir(parents=True, exist_ok=True)
                im.save(path, quality=90, subsampling=0)
            count += 1
    write(a.out / a.dataset / 'render_reports' / f'{a.shard}.json',
          dict(limit=a.limit, sets=a.sets, shard=a.shard, shards=a.shards, candidates=count))


def sheets(a):
    # 原接触图与CSV格式直接复用；函数内部不加载模型。
    k_render.manifest_path = manifest_path
    k_render.local = local
    k_render.key = key
    k_render.sheets(a)
