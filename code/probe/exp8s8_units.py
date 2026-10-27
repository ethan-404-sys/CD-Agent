"""冻结补漏单元及紧致 packed 掩码；build 可按图分片。"""
from exp8s8_common import *
import time


def build(a):
    scheme = frozen(a)
    seq = names(a)
    for i in range(a.shard, len(seq), a.shards):
        name = seq[i]
        d = load(a.dataset, a.split, name)
        kept = s6.units(d, scheme)
        ref = read(part_path(a, 'filter_parts', name))
        if ref['limit'] != a.limit or len(kept) != ref['schemes'][scheme]['candidates']:
            raise ValueError('过滤统计与冻结单元数量不符')
        boxes, masks, rows = [], [], []
        distance = ndi.distance_transform_cdt(~d['base'], metric='chessboard') if d['base'].any() else None
        for u in kept:
            x0, y0, x1, y1 = u['bbox']
            mask = u['mask']
            values = d['cell'][y0:y1, x0:x1][mask]
            ct = np.array([(values == k).sum() for k in CELLS])
            precision = float(d['gt'][y0:y1, x0:x1][mask].mean())
            label = 1 if precision >= .5 else 0 if precision < .1 else -1
            majority = CELLS[int(ct.argmax())] if ct.sum() else 0
            yy, xx = np.nonzero(mask)
            xa, xb, ya, yb = int(xx.min()), int(xx.max())+1, int(yy.min()), int(yy.max())+1
            boxes.append([x0+xa, y0+ya, x0+xb, y0+yb])
            masks.append(np.packbits(mask[ya:yb, xa:xb]))
            rows.append((u['id'], precision, label, majority, int(mask.sum()),
                         int(distance[y0:y1, x0:x1][mask].min()) if distance is not None else -1,
                         u['dscore']))
        n = len(rows)
        r = np.asarray(rows, float).reshape(n, 7)
        save(part_path(a, 'unit_parts', name, '.npz'), dataset=a.dataset, split=a.split, limit=a.limit,
             scheme=scheme, image=name, shape=d['gt'].shape, id=r[:, 0].astype(np.int32),
             image_id=np.full(n, i, np.int32), bbox=np.asarray(boxes, np.int32).reshape(n, 4),
             mask_data=np.concatenate(masks) if masks else np.empty(0, np.uint8),
             mask_offsets=np.r_[0, np.cumsum([len(m) for m in masks])].astype(np.int64),
             precision=r[:, 1], label=r[:, 2].astype(np.int8), majority=r[:, 3].astype(np.int8),
             novel=r[:, 4].astype(np.int32), distance=r[:, 5].astype(np.int32), dscore=r[:, 6])
        print(f'units {a.dataset}/{a.split}/{name}: {n}', flush=True)


def merge(a):
    seq = names(a)
    parts = [archive(part_path(a, 'unit_parts', n, '.npz')) for n in seq]
    for i, (name, z) in enumerate(zip(seq, parts)):
        if (str(z['image']) != name or int(z['limit']) != a.limit or str(z['scheme']) != frozen(a)
                or str(z['dataset']) != a.dataset or str(z['split']) != a.split
                or not np.all(z['image_id'] == i)):
            raise ValueError(f'{name}: 分片不匹配')
    keys = ('id', 'image_id', 'bbox', 'mask_data', 'precision', 'label', 'majority', 'novel', 'distance', 'dscore')
    arrays = {k: np.concatenate([z[k] for z in parts]) for k in keys}
    arrays['mask_offsets'] = np.r_[0, np.cumsum(np.concatenate([np.diff(z['mask_offsets']) for z in parts]))]
    expected = read(a.out / a.dataset / f'filter_{a.split}.json')['schemes'][frozen(a)]['candidates']
    if len(arrays['id']) != expected:
        raise ValueError('合并数量与过滤统计不符')
    save(unit_path(a), **arrays, images=np.array(seq), shapes=np.array([z['shape'] for z in parts]),
         dataset=a.dataset, split=a.split, limit=a.limit, scheme=frozen(a))
    write(unit_path(a).with_suffix('.json'), envelope(a, scheme=frozen(a), images=len(seq),
          candidates=expected, labels={str(k): int((arrays['label'] == k).sum()) for k in (-1, 0, 1)}))


if __name__ == '__main__':
    a = setup(parser(__doc__, ('build', 'merge', 'all')))
    if a.stage == 'all' and a.shards != 1:
        raise ValueError('all 不接受多分片')
    started = time.monotonic()
    if a.stage in ('build', 'all'): build(a)
    if a.stage in ('merge', 'all'): merge(a)
    finish(a, 'units-'+a.stage, started)
