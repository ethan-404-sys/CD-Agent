"""真实候选外观、来源和建筑变化理由表。"""
from exp8s8_icl_common import *
from PIL import ImageFilter


def semantics(ds, split, name):
    raw = raw_dataset(ds, split)
    if ds == 'whu':
        return [np.asarray(Image.open(DATASETS[raw][k] / name)) > 0 for k in ('sem1', 'sem2')], '两期建筑真值'
    path = (B18 / 'sam3/levir_val' if split == 'val' else OUT / 'exp4/levir/sam3') / (Path(name).stem+'.npz')
    with np.load(path, allow_pickle=False) as z:
        return [z[k] == 5 for k in ('cls_t1', 'cls_t2')], '缓存SAM3建筑语义（推断）'


def positive_reason(sem, mask, box, source, evidence):
    if evidence.startswith('缓存') and 'B' not in source:
        return '有变化', 'G来源缺少可判建筑方向的真值'
    x0, y0, x1, y1 = box
    one, two = [s[y0:y1, x0:x1][mask] for s in sem]
    new, gone = float((~one & two).mean()), float((one & ~two).mean())
    # 在新增区域超过一半像素支持单一方向时给出方向，否则保留不确定性。
    kind = '新建建筑' if new > .5 else '拆除建筑' if gone > .5 else '有变化'
    return kind, f'{evidence}；新增区域新建比例={new:.4f}，拆除比例={gone:.4f}'


def build(a):
    z = load_units(a)
    rows = []
    for i in range(a.shard, len(z['images']), a.shards):
        name = str(z['images'][i])
        rgb = rgb_pair(a.dataset, a.split, name)
        delta = np.abs(rgb[0].astype(np.float32)-rgb[1]).mean(axis=2)
        details = source_details(a.dataset, a.split, name)
        sem, evidence = semantics(a.dataset, a.split, name)
        js = image_rows(z, i)
        for j in js:
            if z['label'][j] not in (0, 1):
                continue
            unit = unpack(z, j)
            x0, y0, x1, y1 = box = list(map(int, unit['bbox']))
            mask = unit['mask']
            if int(mask.sum()) != int(z['novel'][j]):
                raise ValueError('新增面积不匹配')
            l, t, r, b = max(0, x0-3), max(0, y0-3), min(delta.shape[1], x1+3), min(delta.shape[0], y1+3)
            local_mask = np.zeros((b-t, r-l), bool)
            local_mask[y0-t:y1-t, x0-l:x1-l] = mask
            dilated = np.asarray(Image.fromarray(local_mask.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(7))) > 0
            ring = dilated & ~local_mask
            inner = float(delta[y0:y1, x0:x1][mask].mean())
            outer = float(delta[t:b, l:r][ring].mean()) if ring.any() else None
            sources = sorted(details[unit['id']]['sources'])
            kind, why = positive_reason(sem, mask, box, sources, evidence) if z['label'][j] else ('', '')
            rows.append(dict(dataset=a.dataset, split=a.split, image=name,
                cid=f"{Path(name).stem}:{unit['id']}", unit_id=unit['id'], label=int(z['label'][j]),
                majority=int(z['majority'][j]), novel=int(mask.sum()), area=int(mask.sum()),
                area_bin=area_bin(int(mask.sum())), sources=sources, source='+'.join(sources),
                det_mean=float(z['dscore'][j]), bbox=box, precision=float(z['precision'][j]),
                appearance_diff=inner, ring_diff=outer, appearance_ring_delta=inner-outer if outer is not None else None,
                all_candidates_in_image=len(js), eligible_candidates_in_image=int((z['label'][js]>=0).sum()),
                semantic_text='', change_kind=kind, reason_evidence=why))
        print(f'table {a.dataset}/{a.split}/{name}: {len(js)}', flush=True)
    write(a.out / a.dataset / 'table_parts' / a.split / f'{a.shard}.json',
          envelope(a, shard=a.shard, shards=a.shards, images=z['images'][a.shard::a.shards].tolist(), rows=rows))


def merge(a):
    z = load_units(a)
    rows = []
    for i in range(a.shards):
        part = read(a.out / a.dataset / 'table_parts' / a.split / f'{i}.json')
        for k, v in dict(dataset=a.dataset, split=a.split, limit=a.limit, shard=i, shards=a.shards,
                         images=z['images'][i::a.shards].tolist()).items():
            if part[k] != v:
                raise ValueError('table 分片不匹配')
        rows.extend(part['rows'])
    rows.sort(key=lambda r: (r['image'], r['unit_id']))
    expected = {(str(z['images'][z['image_id'][j]]), int(z['id'][j])) for j in range(len(z['id'])) if z['label'][j]>=0}
    if len(rows) != len(expected) or {(r['image'], r['unit_id']) for r in rows} != expected:
        raise ValueError('table 缺失或重复')
    negatives = [r for r in rows if not r['label']]
    thresholds = {k: float(np.quantile([r[k] for r in negatives], .8)) if negatives else None
                  for k in ('det_mean', 'appearance_diff')}
    for row in rows:
        row['hard_det'] = bool(not row['label'] and row['det_mean'] >= thresholds['det_mean'])
        row['hard_appearance'] = bool(not row['label'] and row['appearance_diff'] >= thresholds['appearance_diff'])
        row['hard_demo'] = row['hard_det'] or row['hard_appearance']
        row['stratum'] = ('pos2' if row['majority']==2 else 'pos_other') if row['label'] else ('hard_neg' if row['hard_det'] else 'rand_neg')
    write_csv(table_path(a), rows)
    write(table_path(a).with_suffix('.json'), envelope(a, images=z['images'].tolist(), count=len(rows), thresholds=thresholds,
          hard_rule='复用exp6k：负例D或外观差>=各自80%分位点；并列全部纳入，故比例可超过20%'))
