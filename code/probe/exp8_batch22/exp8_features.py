"""Read batch18/20 objects and adapt the existing 6b Pool/Features interface."""
from __future__ import annotations

import json
from pathlib import Path

import exp8_config as cfg

np, old, rt = cfg.rt.np, cfg.old, cfg.rt


def extract(split, name, args):
    objects, shape, details = cfg.read_units(split, name, args)
    prob = rt.probability(cfg.RAW_DATASET[split], name, args.batch18)
    if shape != prob.shape or not np.isfinite(prob).all():
        raise ValueError(f'{name}: invalid detector probability map')
    meta = np.zeros((len(objects), 12), np.float32)
    extra = np.zeros((len(objects), 8), np.float32)
    labels = np.empty(len(objects), np.int8)
    for i, (obj, row) in enumerate(zip(objects, details)):
        x0, y0, x1, y1 = obj['bbox']
        values = prob[y0:y1, x0:x1][obj['mask']]
        meta[i, :5] = [values.mean(), values.max(), values.min(),
                       np.percentile(values, 90), np.log(obj['area'])]
        sources = set(row['sources'])
        if not sources or not sources.issubset(cfg.SOURCES):
            raise ValueError(f'{name}/{i}: unexpected object sources {sources}')
        for source in sources:
            meta[i, 5 + old.SOURCES.index(source)] = 1
        code = sum(1 << cfg.SOURCES.index(s) for s in sources)
        extra[i, 0], extra[i, code] = len(sources), 1
        labels[i] = {'positive': 1, 'negative': 0, 'middle': -1}[row['label']]
    return meta, extra, labels


def prepare(split, args):
    images = cfg.names(split, args.limit)
    root = args.out / 'features' / split
    config = cfg.protocol(args)
    cfg.b20.write_fixed(root / 'config.json', config)
    counts = []
    for number, name in enumerate(images, 1):
        path = root / (Path(name).stem + '.npz')
        if not path.exists():
            meta, extra, labels = extract(split, name, args)
            old.save_npz(path, meta=meta, extra=extra, label=labels, image=name)
        with np.load(path, allow_pickle=False) as z:
            if str(z['image']) != name:
                raise ValueError(f'{path}: wrong image')
            counts.append(len(z['label']))
        if number % 50 == 0:
            print(f'features {split} {number}/{len(images)}', flush=True)
    cfg.b20.write_fixed(root / 'manifest.json', dict(images=images, counts=counts, config=config))


class Pool(old.Pool):
    def __init__(self, split, args):
        root = args.out / 'features' / split
        manifest = rt.read_json(root / 'manifest.json')
        if manifest['config'] != cfg.protocol(args) or manifest['images'] != cfg.names(split, args.limit):
            raise ValueError(f'{root}: feature configuration differs')
        self.dataset = cfg.RAW_DATASET[split]
        self.images = manifest['images']
        self.image_index = {name: i for i, name in enumerate(self.images)}
        count = sum(manifest['counts'])
        self.meta = np.empty((count, 12), np.float32)
        self.extra = np.empty((count, 8), np.float32)
        self.label = np.empty(count, np.int8)
        self.piece_id = np.empty(count, np.int32)
        self.image_id = np.empty(count, np.int32)
        self.slices = {}
        start = 0
        for image_id, (name, size) in enumerate(zip(self.images, manifest['counts'])):
            sl = slice(start, start + size)
            with np.load(root / (Path(name).stem + '.npz'), allow_pickle=False) as z:
                self.meta[sl], self.extra[sl], self.label[sl] = z['meta'], z['extra'], z['label']
            self.piece_id[sl] = np.arange(size)
            self.image_id[sl] = image_id
            self.slices[name] = sl
            start += size
        if not np.isfinite(self.meta).all() or not np.isfinite(self.extra).all():
            raise ValueError('Non-finite feature values')


class Features(old.Features):
    def __init__(self, pool):
        super().__init__(pool, 'none')
        self.input_dim = len(cfg.FEATURE_NAMES)
        self.config = dict(backbone='none', names=cfg.FEATURE_NAMES)

    def batches(self, selected, batch_size, rng=None):
        for idx, x in super().batches(selected, batch_size, rng):
            yield idx, np.concatenate((x, self.pool.extra[idx]), axis=1)


def cleaned_selection(pool, args):
    selected_path = args.batch20 / 'cleaning/selected.json'
    record = rt.read_json(selected_path)
    if (record['partial'] or record['training_images'] != cfg.names('train')
            or record['validation_images'] != cfg.names('val') or record['test_cleaned']):
        raise ValueError('Cleaning must be frozen on complete train/validation splits')
    path = args.batch20 / 'cleaning/training_ids' / f'{cfg.UNIT}.jsonl'
    selected = np.zeros(len(pool), bool)
    seen = set()
    with path.open() as handle:
        for row in map(json.loads, handle):
            name = row['image']
            expected_archive = args.batch20 / 'pool' / cfg.POOL_DATASET['train'] / cfg.UNIT / (Path(name).stem + '.npz')
            if (name in seen or row['unit'] != cfg.UNIT or row['rule'] != record['rule']
                    or Path(row['archive']).resolve() != expected_archive.resolve()):
                raise ValueError(f'{path}: inconsistent cleaning row for {name}')
            seen.add(name)
            if name not in pool.slices:
                continue
            sl = pool.slices[name]
            ids = np.asarray(row['unit_ids'], np.int64)
            if len(np.unique(ids)) != len(ids) or np.any(ids < 0) or np.any(ids >= sl.stop - sl.start):
                raise ValueError(f'{name}: invalid retained unit IDs')
            selected[sl.start + ids] = True
    if seen != set(cfg.names('train')) or np.any(pool.label[selected] < 0):
        raise ValueError('Cleaning IDs must cover training images and contain only supervised units')
    return selected, dict(selection_file=str(selected_path), ids_file=str(path), selected=record)
