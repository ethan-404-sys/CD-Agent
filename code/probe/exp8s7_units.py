"""Freeze filtered units, labels, metadata and packed novel masks, by image shards."""
from exp8s7_common import *
import time


def part_path(a, name):
    return a.out / a.dataset / 'unit_parts' / a.split / (Path(name).stem + '.npz')


def build(a):
    scheme = c.read_json(s6.HERE / 'run' / a.dataset / 'frozen6.json')['scheme']
    if scheme != {'second': 'F3A200', 'sysu': 'F2'}[a.dataset]:
        raise ValueError('Upstream frozen filter changed')
    names = s5.scope_names(a.dataset, a.split, a.limit)
    for i in range(a.shard, len(names), a.shards):
        name = names[i]
        d = s5.load(a.dataset, a.split, name)
        kept = s6.units(d, scheme)
        ref = c.read_json(s6.HERE / 'run' / a.dataset / 'filter6' / a.split / (Path(name).stem + '.json'))
        expected = ref['schemes'][scheme]['candidates']
        if len(kept) != expected:
            raise ValueError(f'{name}: {len(kept)} != batch47 {expected}')
        m1 = s5.image_scores(d)
        boxes, masks, rows = [], [], []
        distance = ndi.distance_transform_cdt(~d['base'], metric='chessboard') if d['base'].any() else None
        for u in kept:
            x0, y0, x1, y1 = u['bbox']
            mask = u['mask']
            truth = d['gt'][y0:y1, x0:x1][mask]
            cells = d['cell'][y0:y1, x0:x1][mask]
            ct = np.array([(cells == k).sum() for k in CELLS])
            precision = float(truth.mean())
            majority = CELLS[int(ct.argmax())] if ct.sum() else 0
            yy, xx = np.nonzero(mask)
            xa, xb, ya, yb = int(xx.min()), int(xx.max())+1, int(yy.min()), int(yy.max())+1
            boxes.append([x0+xa, y0+ya, x0+xb, y0+yb])
            masks.append(np.packbits(mask[ya:yb, xa:xb]))
            rows.append((u['id'], u['index'], precision, majority, int(mask.sum()),
                         int(distance[y0:y1, x0:x1][mask].min()) if distance is not None else -1))
        n, dim = len(rows), d['z']['x'].shape[1]
        r = np.asarray(rows, float).reshape(n, 6)
        indices = r[:, 1].astype(int)
        precision = r[:, 2].astype(np.float32)
        save(part_path(a, name), dataset=a.dataset, split=a.split, limit=a.limit, scheme=scheme,
             image=name, shape=d['gt'].shape, id=r[:, 0].astype(np.int32), image_id=np.full(n, i, np.int32),
             bbox=np.asarray(boxes, np.int32).reshape(n, 4),
             mask_data=np.concatenate(masks) if masks else np.empty(0, np.uint8),
             mask_offsets=np.r_[0, np.cumsum([len(m) for m in masks])].astype(np.int64),
             precision=precision, label=np.where(precision >= .5, 1, np.where(precision < .1, 0, -1)).astype(np.int8),
             majority=r[:, 3].astype(np.int8), novel=r[:, 4].astype(np.int32), distance=r[:, 5].astype(np.int32),
             x=d['z']['x'][indices].reshape(n, dim), dscore=d['z']['x'][indices, 0], m1score=m1[indices],
             expected=expected)
        print(f'{a.dataset}/{a.split}/{name}: {n} units, batch47 count matched', flush=True)


def merge(a):
    names = s5.scope_names(a.dataset, a.split, a.limit)
    parts = [c.archive(part_path(a, n)) for n in names]
    scheme = c.read_json(s6.HERE / 'run' / a.dataset / 'frozen6.json')['scheme']
    for i, (name, z) in enumerate(zip(names, parts)):
        if (str(z['image']) != name or int(z['limit']) != a.limit or str(z['scheme']) != scheme
                or str(z['dataset']) != a.dataset or str(z['split']) != a.split
                or not np.all(z['image_id'] == i)):
            raise ValueError(f'{name}: incompatible shard')
    keys = ('id', 'image_id', 'bbox', 'mask_data', 'precision', 'label', 'majority', 'novel', 'distance', 'x', 'dscore', 'm1score')
    arrays = {k: np.concatenate([z[k] for z in parts]) for k in keys}
    lengths = np.concatenate([np.diff(z['mask_offsets']) for z in parts])
    arrays['mask_offsets'] = np.r_[0, np.cumsum(lengths)]
    count = len(arrays['id'])
    expected = sum(int(z['expected']) for z in parts)
    if not a.limit:
        expected = c.read_json(s6.HERE / 'run' / a.dataset / f'filter6_{a.split}.json')['schemes'][scheme]['candidates']
    if count != expected:
        raise ValueError(f'Merged {count} != batch47 {expected}')
    save(unit_path(a, a.split), **arrays, images=np.array(names), shapes=np.array([z['shape'] for z in parts]),
         dataset=a.dataset, split=a.split, limit=a.limit, scheme=scheme)
    write(unit_path(a, a.split).with_suffix('.json'), dict(dataset=a.dataset, split=a.split, scheme=scheme,
          images=len(names), candidates=count, expected=expected, count_check='exact', partial=bool(a.limit),
          labels={str(k): int((arrays['label'] == k).sum()) for k in (-1, 0, 1)}))
    print(f'merged {a.dataset}/{a.split}: {count}, expected={expected}', flush=True)


def main():
    p = parser(__doc__)
    p.add_argument('stage', choices=('build', 'merge', 'all'))
    p.add_argument('--split', choices=('train', 'val', 'test'), required=True)
    shard_args(p)
    a = setup(p)
    if a.stage == 'all' and a.shards != 1:
        p.error('Use build shards followed by merge')
    if a.stage in ('build', 'all'):
        build(a)
    if a.stage in ('merge', 'all'):
        merge(a)


if __name__ == '__main__':
    main()
