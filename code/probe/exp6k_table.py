"""从冻结单元生成逐候选表，可按图分片再合并。"""
from exp6k_common import *
from PIL import ImageFilter


def part_path(a, shard):
    return a.out / a.dataset / 'table_parts' / a.split / f'part-{shard:03d}.json'


def build(a):
    z = inventory(a.dataset, a.split, masks=True)
    images = z['images'].tolist()[:a.limit or None]
    rows = []
    for image_id in range(a.shard, len(images), a.shards):
        name = images[image_id]
        rgb = rgb_pair(a.dataset, a.split, name)
        delta = np.abs(rgb[0].astype(np.float32) - rgb[1]).mean(axis=2)
        details = source_details(a.dataset, a.split, name)
        sem = semantics(name) if a.dataset == 'second' else None
        full_hist = [distribution(s) for s in sem] if sem is not None else None
        js = list(image_indices(z, image_id))
        for j in js:
            if z['label'][j] not in (0, 1):
                continue
            unit = int(z['id'][j])
            box = list(map(int, z['bbox'][j]))
            x0, y0, x1, y1 = box
            mask = unpack(z, j)
            area = int(mask.sum())
            if area != int(z['novel'][j]):
                raise ValueError(f'{name}/{unit}: 新增面积不匹配')
            # 三像素外环，图像边界截断；环内差异与候选内差异采用同一 RGB 单位。
            l, t, r, b = max(0, x0-3), max(0, y0-3), min(delta.shape[1], x1+3), min(delta.shape[0], y1+3)
            local_mask = np.zeros((b-t, r-l), bool)
            local_mask[y0-t:y1-t, x0-l:x1-l] = mask
            dilated = np.asarray(Image.fromarray(local_mask.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(7))) > 0
            ring = dilated & ~local_mask
            inner = float(delta[y0:y1, x0:x1][mask].mean())
            outer = float(delta[t:b, l:r][ring].mean()) if ring.any() else None
            sources = sorted(details[unit]['sources'])
            row = dict(dataset=a.dataset, split=a.split, image=name, cid=f'{Path(name).stem}:{unit}',
                       unit_id=unit, label=int(z['label'][j]), majority=int(z['majority'][j]),
                       novel=area, area=area, area_bin=area_bin(area), sources=sources, source='+'.join(sources),
                       det_mean=float(z['dscore'][j]), m1_score=float(z['m1score'][j]), bbox=box,
                       precision=float(z['precision'][j]), appearance_diff=inner, ring_diff=outer,
                       appearance_ring_delta=inner-outer if outer is not None else None,
                       all_candidates_in_image=len(js), eligible_candidates_in_image=int(sum(z['label'][k] in (0, 1) for k in js)),
                       semantic_text='', change_kind='')
            if sem is not None:
                hist = [distribution(s[y0:y1, x0:x1][mask]) for s in sem]
                for i in (0, 1):
                    row[f'semantic_t{i+1}'] = hist[i]
                    row[f'semantic_t{i+1}_majority'] = hist[i][0]['name']
                    row[f'semantic_t{i+1}_fraction'] = hist[i][0]['fraction']
                row['semantic_full'] = full_hist
                row['change_kind'] = f"{hist[0][0]['name']} → {hist[1][0]['name']}"
                row['semantic_text'] = (f'候选区域 T1：{describe(hist[0])}；T2：{describe(hist[1])}；'
                                        f'整图两期主要类别（仅变化区提供地类） T1：{describe(full_hist[0])}；'
                                        f'T2：{describe(full_hist[1])}。无变化标签不表示已知地类。')
            rows.append(row)
        print(f'{a.dataset}/{a.split}/{name}: {len(js)} 个冻结候选', flush=True)
    write(part_path(a, a.shard), dict(dataset=a.dataset, split=a.split, limit=a.limit,
          shard=a.shard, shards=a.shards, images=images[a.shard::a.shards], rows=rows))


def merge(a):
    z = inventory(a.dataset, a.split)
    images = z['images'].tolist()[:a.limit or None]
    rows = []
    for i in range(a.shards):
        part = read(part_path(a, i))
        for k, value in dict(dataset=a.dataset, split=a.split, limit=a.limit, shard=i, shards=a.shards,
                             images=images[i::a.shards]).items():
            if part[k] != value:
                raise ValueError(f'表分片不匹配: {k}, part {i}')
        rows.extend(part['rows'])
    rows.sort(key=lambda r: (r['image'], r['unit_id']))
    expected = {(str(z['images'][z['image_id'][j]]), int(z['id'][j])) for j in range(len(z['id']))
                if z['image_id'][j] < len(images) and z['label'][j] in (0, 1)}
    actual = {(r['image'], r['unit_id']) for r in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError('分片合并后候选覆盖不完整或重复')
    neg = [r for r in rows if r['label'] == 0]
    thresholds = {k: float(np.quantile([r[k] for r in neg], .8)) if neg else None
                  for k in ('det_mean', 'appearance_diff')}
    for row in rows:
        row['hard_det'] = bool(row['label'] == 0 and row['det_mean'] >= thresholds['det_mean'])
        row['hard_appearance'] = bool(row['label'] == 0 and row['appearance_diff'] >= thresholds['appearance_diff'])
        row['hard_demo'] = row['hard_det'] or row['hard_appearance']
        row['stratum'] = ('pos2' if row['majority'] == 2 else 'pos_other') if row['label'] else (
            'hard_neg' if row['hard_det'] else 'rand_neg')
    write_csv(table_path(a), rows)
    write(table_path(a).with_suffix('.json'), dict(dataset=a.dataset, split=a.split, limit=a.limit,
          images=images, count=len(rows), thresholds=thresholds, partial=bool(a.limit)))
    print(f'merged {a.dataset}/{a.split}: {len(rows)}', flush=True)
