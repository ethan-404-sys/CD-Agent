"""6b none metadata and original 6a piece IDs for the SYSU U0 control."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import exp8_config as cfg
import exp6_pool_io as pool_io

np, old, rt = cfg.rt.np, cfg.old, cfg.rt


@lru_cache(maxsize=2)
def raw_pool(dataset, root):
    previous_root, previous_pool = old.ROOT, pool_io.POOL
    try:
        if dataset == 'sysu_val':
            pool = cfg.verifier.open_pool(root)
        else:
            old.ROOT = Path(root)
            pool = old.Pool('sysu')
    finally:
        old.ROOT, pool_io.POOL = previous_root, previous_pool
    return pool


def image_arrays(split, name, args):
    dataset = cfg.RAW_DATASET[split]
    root = args.u0_test if split == 'test' else args.batch19
    pool = raw_pool(dataset, root.resolve())
    sl = pool.slices[name]
    indices = np.arange(sl.start, sl.stop)
    indices = indices[np.isin(pool.source[indices], [old.SOURCES.index(s) for s in cfg.SOURCES])]
    return pool.meta[indices], pool.label[indices], pool.piece_id[indices]


def extract(split, name, args):
    meta, labels, _ = image_arrays(split, name, args)
    return meta, np.empty((len(labels), 0), np.float32), labels


def prepare(split, args):
    images = cfg.names(split, args.limit)
    root = args.out / 'features' / split
    config = cfg.protocol(args)
    cfg.b20.write_fixed(root / 'config.json', config)
    counts = []
    for name in images:
        meta, labels, ids = image_arrays(split, name, args)
        path = root / (Path(name).stem + '.npz')
        if not path.exists():
            old.save_npz(path, image=name, meta=meta, label=labels, piece_id=ids,
                         extra=np.empty((len(labels), 0), np.float32))
        with np.load(path, allow_pickle=False) as z:
            if str(z['image']) != name or not np.array_equal(ids, z['piece_id']):
                raise ValueError(f'{name}: U0 cached piece IDs differ')
            counts.append(len(z['label']))
    cfg.b20.write_fixed(root / 'manifest.json', dict(images=images, counts=counts, config=config))


def read_units(split, name, args):
    meta, labels, ids = image_arrays(split, name, args)
    dataset = cfg.RAW_DATASET[split]
    root = args.u0_test / 'pool' if split == 'test' else args.batch19 / 'exp6/pool'
    previous = pool_io.POOL
    try:
        pool_io.POOL = root
        masks = pool_io.load_piece_masks(dataset, name)
    finally:
        pool_io.POOL = previous
    with np.load(root / dataset / 'masks' / (Path(name).stem + '.npz'), allow_pickle=False) as z:
        shape = tuple(map(int, z['image_shape']))
    objects = []
    for i, piece_id in enumerate(ids):
        bbox, mask = masks[int(piece_id)]
        area = int(mask.sum())
        if area == 0 or not np.isclose(np.log(area), meta[i, 4], rtol=1e-6):
            raise ValueError(f'{name}/{piece_id}: U0 mask area differs from 6b metadata')
        objects.append(dict(bbox=bbox, mask=mask, area=area))
    return objects, shape, labels


def bind(features):
    base = features.Pool

    class Pool(base):
        def __init__(self, split, args):
            super().__init__(split, args)
            for name in self.images:
                path = args.out / 'features' / split / (Path(name).stem + '.npz')
                with np.load(path, allow_pickle=False) as z:
                    self.piece_id[self.slices[name]] = z['piece_id']

    features.Pool = Pool
    features.prepare = prepare
    features.extract = extract
