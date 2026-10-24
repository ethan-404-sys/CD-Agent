"""实验 8 补充 2：只读缓存的逐单元分流、判断器与元数据统计。见 README_batch40.md。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time

sys.dont_write_bytecode = True
for _key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[_key] = '1'

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
sys.path.insert(0, str(WORKSPACE / 'Chang-Agent/probe'))
# 这两个旧模块只有 numpy/PIL；rt、cfg、batch39 则会间接导入模型库。
from common import OUT, load_pred
from exp6_pool_io import load_piece_masks

VERSION = 1
SEED = 20261024
BOOTSTRAPS = 2000
DATASETS = ('second_cv', 'second', 'sysu_val', 'sysu')
EXPECTED = dict(second_cv=297, second=1694, sysu_val=800, sysu=4000)
PAIRS = (('second_cv', 'second'), ('sysu_val', 'sysu'))
V1 = ('none', 'sam_vith', 'dinov3', 'remoteclip')
U5 = ('full', 'clean', 'detector')
SOURCES = ('B', 'G0.05', 'C1', 'C2', 'H1', 'pred')
META = ('mean', 'max', 'min', 'p90', 'log_area') + tuple('source_' + s for s in SOURCES)
GROUPS = dict(fill='a 补漏全部', refine='b 精修全部', cell12='c ①+② 正例 / 补漏负例',
              cell3='d ③ 正例 / 精修负例', cell12_same="c′ 补漏内 ①+② 正例 / 补漏负例",
              cell3_same="d′ 精修内 ③ 正例 / 精修负例", all='e 全体')
EIGHT = np.ones((3, 3), bool)


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp.json')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def save_npz(path, arrays):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp.npz')
    np.savez_compressed(tmp, **arrays)
    tmp.replace(path)


def split(ds):
    return 'val' if ds in ('second_cv', 'sysu_val') else 'test'


def base_dir(ds):
    return OUT / ('exp8s_batch30_sysu' if ds.startswith('sysu') else 'exp8s_batch30') / 'interim/exp8s' / ds


def u5_root(ds):
    return OUT / ('exp8_batch24/u5' if ds.startswith('sysu') else 'exp8_batch22')


def upstream(ds):
    return OUT / ('exp8_batch19' if ds.startswith('sysu') else 'exp8_batch18')


def image_list(ds):
    names = read_json(base_dir(ds) / 'manifest.json')['config']['images']
    if len(names) != EXPECTED[ds] or len(set(names)) != len(names):
        raise ValueError(f'{ds}: unexpected image inventory')
    if ds == 'second_cv':
        val = read_json(OUT / 'exp6/second_cv/manifest.json')['verifier_val_images']
        if sorted(val) != names:
            raise ValueError('P1/P2 SECOND validation images differ')
    if ds == 'sysu_val':
        val = read_json(upstream(ds) / 'frozen/sysu_val.json')['validation_images']
        if val != names:
            raise ValueError('SYSU validation images differ from the frozen split')
    return names


def populations(args, ds):
    allowed = ('u5', 'p6b', 'v2') if ds.startswith('second') else ('u5',)
    return allowed if args.population == 'all' else tuple(p for p in allowed if p == args.population)


def pixels(ds, name):
    """与 batch34.pixels 相同；附带 batch30 保存的概率与逐像素格。"""
    path = base_dir(ds) / Path(name).stem
    with np.load(path / 'pixels.npz', allow_pickle=False) as z:
        shape = tuple(int(v) for v in z['shape'])
        gt, base = (np.unpackbits(z[k], count=int(np.prod(shape))).reshape(shape).astype(bool)
                    for k in ('gt', 'base'))
        prob = z['probability']
    with np.load(OUT / 'exp8s_batch34/interim' / ds / (Path(name).stem + '.npz'), allow_pickle=False) as z:
        cell = z['cell']
    if cell.shape != shape or not np.array_equal(cell > 0, gt & ~base):
        raise ValueError(f'{ds}/{name}: cells do not cover precisely the baseline FN pixels')
    if not np.isin(cell, (0, 1, 2, 3, 5)).all():
        raise ValueError(f'{ds}/{name}: unexpected cell labels (including cell 4); review the cache')
    if prob.shape != shape or not np.array_equal(prob > .5, base):
        raise ValueError(f'{ds}/{name}: batch30 probability/baseline mismatch')
    counts = np.array([(base & gt).sum(), (base & ~gt).sum(), (~base & gt).sum(),
                       np.isin(cell, (1, 2)).sum(), (cell == 3).sum(), (cell == 5).sum()], np.int64)
    saved = read_json(path / 'image.json')['baseline']
    if counts[:3].tolist() != [saved[k] for k in ('tp', 'fp', 'fn')]:
        raise ValueError(f'{path}: per-image baseline differs')
    return gt, base, prob, cell, counts


def load_units(path):
    """复用 batch18 rt.load_units 的磁盘格式，避免其模型库传递依赖。"""
    with np.load(path, allow_pickle=False) as z:
        shape = tuple(z['image_shape'])
        boxes, packed, offsets = z['bboxes'], z['packed'], z['offsets']
    objects = []
    for i, box in enumerate(boxes):
        x0, y0, x1, y1 = box
        mask = np.unpackbits(packed[offsets[i]:offsets[i + 1]], count=(y1-y0)*(x1-x0))
        mask = mask.reshape(y1-y0, x1-x0).astype(bool)
        objects.append(dict(bbox=box.tolist(), mask=mask, area=int(mask.sum())))
    return objects, shape


def unit_geometry(objects, labels, gt, base, cell):
    r = np.empty(len(objects), np.float64)
    k = np.empty(len(objects), np.int32)
    group = np.zeros(len(objects), np.int8)
    for i, obj in enumerate(objects):
        x0, y0, x1, y1 = obj['bbox']
        mask = obj['mask']
        if obj['area'] <= 0 or mask.shape != (y1-y0, x1-x0):
            raise ValueError(f'Invalid unit mask at index {i}')
        d = base[y0:y1, x0:x1]
        inside = mask & d
        r[i] = inside.sum() / obj['area']
        components, n = ndimage.label(inside, EIGHT)
        k[i] = (np.bincount(components.ravel(), minlength=n+1)[1:] >= 5).sum()
        if labels[i] == 1:
            novel = mask & ~d & gt[y0:y1, x0:x1]
            if novel.any():
                counts = np.bincount(cell[y0:y1, x0:x1][novel], minlength=6)
                group[i] = counts.argmax()  # np.argmax 并列取最小格号。
    return dict(r=r, k=k, fill=r < .1, cell=group)


def histogram(objects, values, fill, gt, base, cell):
    field = np.full(gt.shape, -np.inf, np.float64)
    for obj, value, use in zip(objects, values, fill):
        if use:
            x0, y0, x1, y1 = obj['bbox']
            view = field[y0:y1, x0:x1]
            mask = obj['mask']
            view[mask] = np.maximum(view[mask], value)
    take = np.isfinite(field) & ~base
    values, inv = np.unique(field[take], return_inverse=True)
    g, c = gt[take], cell[take]
    rows = [np.bincount(inv, weights=w, minlength=len(values))
            for w in (g, ~g, g & np.isin(c, (1, 2)), g & (c == 3), g & (c == 5))]
    return np.column_stack([values, *rows])


def u5_data(ds, name, shape):
    stem = Path(name).stem
    pool = upstream(ds) / 'pool' / ds
    objects, stored_shape = load_units(pool / 'U5_x4' / (stem + '.npz'))
    saved = read_json(pool / 'images' / (stem + '.json'))
    details = saved['units']['U5_x4']['details']
    if saved['image'] != name or shape != stored_shape or len(details) != len(objects):
        raise ValueError(f'{ds}/{name}: U5 pool mismatch')
    detector = 'schanger_small' if ds.startswith('sysu') else 'mambascd_base'
    predroot = upstream(ds) / 'preds' if ds == 'sysu_val' else OUT / 'preds'
    prob = np.load(predroot / ds / detector / 'prob' / (stem + '.npy'), allow_pickle=False).astype(np.float32)
    if prob.shape != shape or not np.isfinite(prob).all():
        raise ValueError(f'{ds}/{name}: invalid U5 feature probability')
    meta = np.zeros((len(objects), 12), np.float32)
    source_count = np.empty(len(objects), np.float32)
    labels = np.empty(len(objects), np.int8)
    valid_sources = {'B', 'G0.05'} if ds.startswith('sysu') else {'B', 'G0.05', 'H1'}
    for i, (obj, row) in enumerate(zip(objects, details)):
        if row['unit_id'] != i or row['bbox'] != obj['bbox'] or row['area'] != obj['area']:
            raise ValueError(f'{ds}/{name}/{i}: U5 unit ID/mask metadata mismatch')
        x0, y0, x1, y1 = obj['bbox']
        v = prob[y0:y1, x0:x1][obj['mask']]
        meta[i, :5] = [v.mean(), v.max(), v.min(), np.percentile(v, 90), np.log(obj['area'])]
        sources = set(row['sources'])
        if not sources or not sources <= valid_sources:
            raise ValueError(f'{ds}/{name}/{i}: invalid U5 sources')
        for source in sources:
            meta[i, 5 + SOURCES.index(source)] = 1
        source_count[i] = len(sources)
        labels[i] = {'positive': 1, 'negative': 0, 'middle': -1}[row['label']]
    checks, missing, scores = {}, [], {}
    path = u5_root(ds) / 'evaluation' / split(ds) / 'scores' / (stem + '.npz')
    if not path.exists():
        missing.append(dict(population='u5', dataset=ds, image=name, scorers=list(U5), path=str(path),
                            reason='saved score archive missing; entire split/scorer will be omitted'))
    else:
        with np.load(path, allow_pickle=False) as z:
            if not np.array_equal(z['unit_id'], np.arange(len(objects))) or not np.array_equal(z['label'], labels):
                raise ValueError(f'{path}: U5 IDs/labels differ from the saved pool')
            labels = z['label'].astype(np.int8)
            for scorer in U5:
                if scorer not in z.files:
                    missing.append(dict(population='u5', dataset=ds, image=name, scorers=[scorer],
                                        path=str(path), reason='score key missing'))
                else:
                    scores[scorer] = z[scorer].astype(np.float64)
        if 'detector' in scores and not np.array_equal(scores['detector'], meta[:, 0]):
            raise ValueError(f'{path}: recomputed detector mean differs from saved detector score')
        checks['detector_mean_matches_score'] = 'detector' in scores
    if split(ds) == 'val':
        feature_path = u5_root(ds) / 'features/val' / (stem + '.npz')
        checks['feature_path'] = str(feature_path)
        if not feature_path.exists():
            checks['feature_status'] = 'missing'
            checks['comparison'] = 'recomputed using extract formula; detector score checks mean only; full 12D unverified'
        else:
            with np.load(feature_path, allow_pickle=False) as z:
                if 'meta' not in z.files or z['meta'].shape != meta.shape:
                    checks.update(feature_status='different_format', feature_keys=z.files,
                                  comparison='extract formula and saved detector mean; full 12D unverified')
                else:
                    mismatch = int(np.count_nonzero(z['meta'] != meta))
                    if mismatch:
                        raise ValueError(f'{feature_path}: {mismatch} metadata elements differ from extract')
                    if str(z['image']) != name or not np.array_equal(z['label'], labels):
                        raise ValueError(f'{feature_path}: image/labels mismatch')
                    if 'extra' not in z.files or not np.array_equal(z['extra'][:, 0], source_count):
                        raise ValueError(f'{feature_path}: source count mismatch')
                    checks.update(feature_status='exact', compared_elements=int(meta.size), max_abs_diff=0.0,
                                  source_count_exact=True)
    # 12 维 meta 的最后一列是旧 split 占位（U5 恒为 0），单项分析不把它当来源。
    analysis_meta = np.column_stack([meta[:, :11], source_count])
    return objects, labels, analysis_meta, scores, checks, missing


def image_rows(path, name):
    """按 image 排序的原始 JSONL：按字节二分定位，再只解析本图，避免每个分片扫描整池。"""
    with path.open('rb') as handle:
        lo, hi = 0, path.stat().st_size
        while lo < hi:
            mid = (lo + hi) // 2
            handle.seek(mid)
            if mid:
                handle.readline()
            line = handle.readline()
            if not line or json.loads(line)['image'] >= name:
                hi = mid
            else:
                lo = handle.tell()
        handle.seek(lo)
        rows = []
        for line in handle:
            row = json.loads(line)
            if row['image'] > name:
                break
            if row['image'] == name:
                rows.append(row)
        return rows


class PieceScores:
    """每个作业顺序读取四个分数文件，只保留本分片的图，按 (image,piece_id) 连接。"""
    def __init__(self, ds, names):
        self.scores, self.missing = {}, []
        for scorer in V1:
            path = OUT / 'exp6/verifier' / scorer / ds / 'scores.npz'
            if not path.exists():
                self.missing.append(dict(population='p6b', dataset=ds, scorers=[scorer], path=str(path),
                                         reason='saved score archive missing'))
                continue
            by_image = {}
            with np.load(path, allow_pickle=False) as z:
                images, ids, values = z['image'], z['piece_id'], z['score']
                if images.shape != ids.shape or ids.shape != values.shape or str(z['dataset']) != ds:
                    raise ValueError(f'{path}: invalid score arrays/dataset')
                for name in names:
                    left, right = np.searchsorted(images, name, side='left'), np.searchsorted(images, name, side='right')
                    local_id, local_score = ids[left:right].copy(), values[left:right].astype(np.float64)
                    if np.any(images[left:right] != name) or np.any(np.diff(local_id) <= 0):
                        raise ValueError(f'{path}/{name}: duplicate or unordered score keys')
                    by_image[name] = (local_id, local_score)
            self.scores[scorer] = by_image

    def join(self, name, ids):
        scores = {}
        for scorer, by_image in self.scores.items():
            keys, values = by_image[name]
            positions = np.searchsorted(keys, ids)
            if np.any(positions >= len(keys)) or not np.array_equal(keys[positions], ids):
                raise ValueError(f'{scorer}/{name}: saved scores do not cover all eligible piece IDs')
            scores[scorer] = values[positions]
        return scores


def piece_data(ds, name, score_store, expected):
    rows = image_rows(OUT / 'exp6/pool' / ds / 'pieces.jsonl', name)
    if len(rows) != expected:
        raise ValueError(f'{ds}/{name}: {len(rows)} piece records, expected {expected}')
    chosen = [r for r in rows if r['kind'] == 'recall' and r['label'] in (0, 1)]
    masks = load_piece_masks(ds, name)
    objects, labels, ids = [], [], []
    meta = np.zeros((len(chosen), 12), np.float32)
    for i, row in enumerate(chosen):
        pid = row['piece_id']
        box, mask = masks[pid]
        label = 1 if row['gt_frac'] >= .5 else 0 if row['gt_frac'] < .1 else -1
        if row['label'] != label or box != row['bbox'] or int(mask.sum()) != row['area']:
            raise ValueError(f'{ds}/{name}/{pid}: saved label or mask metadata mismatch')
        source = row['subsource'] or row['source']
        meta[i, :4] = [row['det_prob'][k] for k in ('mean', 'max', 'min', 'p90')]
        meta[i, 4] = np.log(row['area'])
        meta[i, 5 + SOURCES.index(source)] = 1
        meta[i, 11] = float(row['split'])
        objects.append(dict(bbox=box, mask=mask, area=row['area']))
        labels.append(label)
        ids.append(pid)
    ids = np.asarray(ids, np.int32)
    if len(np.unique(ids)) != len(ids):
        raise ValueError(f'{ds}/{name}: duplicate eligible piece IDs')
    return objects, np.asarray(labels, np.int8), ids, meta, score_store.join(name, ids)


class V2Sample:
    def __init__(self, ds):
        self.ds, self.scores, self.missing = ds, {}, []
        self.path = OUT / 'exp6/v2/data' / (split(ds) + '.jsonl')
        self.rows = read_jsonl(self.path)
        self.by_image = {}
        self.keys = {(r['image'], int(r['piece_id'])) for r in self.rows}
        expected = 3000 if split(ds) == 'val' else 6000
        if len(self.rows) != expected or len(self.keys) != expected:
            raise ValueError(f'{self.path}: expected {expected} unique sampled pieces')
        for row in self.rows:
            if row['dataset'] != ds or row['label'] not in (0, 1) or not np.isfinite(row['weight']) or row['weight'] <= 0:
                raise ValueError(f'{self.path}: invalid sample row')
            self.by_image.setdefault(row['image'], []).append(row)
        for variant in ('a', 'c'):
            root = OUT / 'exp6/v2' / variant / 'eval'
            paths = sorted(root.rglob('*scores*.jsonl'))
            found, used = {}, []
            for path in paths:
                rows = read_jsonl(path)
                selected = [r for r in rows if (r['image'], int(r['piece_id'])) in self.keys]
                if selected:
                    used.append(str(path))
                for row in selected:
                    key = (row['image'], int(row['piece_id']))
                    if key in found:
                        raise ValueError(f'{path}: duplicate V2 score key {key}')
                    found[key] = float(row['score'])
            scorer = 'v2' + variant
            if not found:
                self.missing.append(dict(population='v2', dataset=ds, scorers=[scorer],
                                         path=str(root), searched=[str(p) for p in paths],
                                         reason='no saved per-piece scores for this sample; aggregate validation thresholds are insufficient'))
            elif set(found) != self.keys or not np.isfinite(list(found.values())).all():
                raise ValueError(f'{root}: V2 scores must cover every sampled piece exactly, finite and unique')
            else:
                self.scores[scorer] = found

    def image(self, name, ids, labels, geometry, p6scores):
        rows = self.by_image.get(name, [])
        lookup = {int(pid): i for i, pid in enumerate(ids)}
        take = np.asarray([lookup[r['piece_id']] for r in rows], np.int64)
        if not np.array_equal(labels[take], np.asarray([r['label'] for r in rows], np.int8)):
            raise ValueError(f'{self.path}/{name}: sample labels differ from P2')
        values = {scorer: np.asarray([store[(name, r['piece_id'])] for r in rows], np.float64)
                  for scorer, store in self.scores.items()}
        values.update({'v1_' + scorer: v[take] for scorer, v in p6scores.items()})
        return dict(id=ids[take], label=labels[take], weight=np.asarray([r['weight'] for r in rows], np.float64),
                    **{k: v[take] for k, v in geometry.items()}), values


def run_units(args, ds):
    pops = populations(args, ds)
    if not pops:
        return
    names = image_list(ds)[:args.limit or None]
    names = names[args.shard::args.shards]
    startup = time.perf_counter()
    need_pieces = bool(set(pops) & {'p6b', 'v2'})
    piece_scores = PieceScores(ds, names) if need_pieces else None
    sample = V2Sample(ds) if 'v2' in pops else None
    expected_pieces = {}
    if need_pieces:
        expected_pieces = {r['image']: r['pieces'] for r in read_json(OUT / 'exp6/pool' / ds / 'pieces_summary.json')['per_image']}
    if sample and not set(sample.by_image) <= set(image_list(ds)):
        raise ValueError(f'{ds}: sample contains images outside the split')
    setup_seconds = time.perf_counter() - startup
    for name in names:
        started = time.perf_counter()
        gt, base, prob, cell, counts = pixels(ds, name)
        arrays = dict(version=np.array(VERSION), image=np.array(name), dataset=np.array(ds),
                      populations=np.asarray(pops), baseline=counts)
        audit = dict(missing_scores=[], checks={}, setup_seconds=setup_seconds)
        if 'u5' in pops:
            objects, labels, meta, scores, checks, missing = u5_data(ds, name, gt.shape)
            geo = unit_geometry(objects, labels, gt, base, cell)
            record_population(arrays, 'u5', dict(id=np.arange(len(labels)), label=labels, meta=meta,
                              weight=np.ones(len(labels)), **geo), scores, objects, gt, base, cell)
            audit['checks']['u5'] = checks
            audit['missing_scores'] += missing
        if need_pieces:
            objects, labels, ids, meta, scores = piece_data(ds, name, piece_scores, expected_pieces[name])
            geo = unit_geometry(objects, labels, gt, base, cell)
            original = load_pred(ds, 'mambascd_base', name)
            if original.shape != base.shape:
                raise ValueError(f'{ds}/{name}: historical mask geometry differs')
            audit['checks']['p6b'] = dict(eligible_pieces=len(ids),
                                         covered_scores={s: len(v) for s, v in scores.items()},
                                         detector_mismatch_pixels=int(np.count_nonzero(original != base)))
            audit['missing_scores'] += piece_scores.missing
            if 'p6b' in pops:
                record_population(arrays, 'p6b', dict(id=ids, label=labels, meta=meta, weight=np.ones(len(labels)), **geo),
                                  scores, objects, gt, base, cell)
            if sample:
                data, values = sample.image(name, ids, labels, geo, scores)
                record_population(arrays, 'v2', data, values)
                audit['missing_scores'] += sample.missing
                audit['checks']['v2'] = dict(sample_path=str(sample.path), sample_total=len(sample.rows),
                                             selected_pieces=len(data['id']))
        audit['seconds'] = time.perf_counter() - started
        audit['peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        arrays['audit'] = np.array(json.dumps(audit, ensure_ascii=False))
        dest = args.out / 'units' / args.population / ds / (Path(name).stem + '.npz')
        save_npz(dest, arrays)
        print(json.dumps(dict(dataset=ds, image=name, seconds=round(audit['seconds'], 3),
                              peak_rss_mib=round(audit['peak_rss_mib'], 1), output=str(dest)), ensure_ascii=False), flush=True)


def record_population(arrays, pop, data, scores, objects=None, gt=None, base=None, cell=None):
    for key, value in data.items():
        arrays[pop + '__' + key] = value
    arrays[pop + '__scorers'] = np.asarray(list(scores), dtype=str)
    for scorer, values in scores.items():
        if values.shape != data['label'].shape or not np.isfinite(values).all():
            raise ValueError(f'{pop}/{scorer}: invalid scores')
        arrays[f'{pop}__score__{scorer}'] = values
        if objects is not None:
            arrays[f'{pop}__hist__{scorer}'] = histogram(objects, values, data['fill'], gt, base, cell)


def masks_for(data):
    pos, neg, fill = data['label'] == 1, data['label'] == 0, data['fill']
    return dict(fill=(pos | neg) & fill, refine=(pos | neg) & ~fill,
                cell12=(pos & np.isin(data['cell'], (1, 2))) | (neg & fill),
                cell3=(pos & (data['cell'] == 3)) | (neg & ~fill),
                cell12_same=(pos & fill & np.isin(data['cell'], (1, 2))) | (neg & fill),
                cell3_same=(pos & ~fill & (data['cell'] == 3)) | (neg & ~fill), all=pos | neg)


class Ranking:
    """固定排序和同分组；自助法只变权重，避免 2000 次重新排序。"""
    def __init__(self, labels, values):
        self.order = np.argsort(-values, kind='stable')
        ordered = values[self.order]
        self.starts = np.r_[0, np.flatnonzero(np.diff(ordered)) + 1]
        self.positive = labels[self.order] == 1

    @staticmethod
    def from_counts(p, n):
        tp, fp = np.cumsum(p), np.cumsum(n)
        if tp[-1] <= 0 or fp[-1] <= 0:
            return np.nan, np.nan
        auc = np.sum(n * (tp - p / 2)) / (tp[-1] * fp[-1])
        precision = np.divide(tp, tp + fp, out=np.zeros_like(tp), where=(tp + fp) > 0)
        ap = np.sum(p * precision) / tp[-1]
        return float(auc), float(ap)

    def counts(self, weights):
        ordered = weights[self.order].astype(np.float64)
        return (np.add.reduceat(ordered * self.positive, self.starts),
                np.add.reduceat(ordered * ~self.positive, self.starts))

    def evaluate(self, weights):
        return self.from_counts(*self.counts(weights))


def metric(labels, values, weights, image_ids, image_count, args, weighted=False, include_ap=True):
    positive, negative = int((labels == 1).sum()), int((labels == 0).sum())
    result = dict(positive=positive, negative=negative, positive_weight=float(weights[labels == 1].sum()),
                  negative_weight=float(weights[labels == 0].sum()), auc=None, auc_ci95=None,
                  bootstrap_replicates=args.bootstrap, bootstrap_valid=0)
    if include_ap:
        result.update(ap=None, ap_ci95=None)
    if positive < 30 or negative == 0:
        result['status'] = 'positive_below_30' if positive < 30 else 'no_negative'
        return result
    rank = Ranking(labels, values)
    auc, ap = rank.evaluate(weights)
    result.update(auc=auc, status='ok')
    if include_ap:
        result['ap'] = ap
    rng = np.random.default_rng(args.seed)
    samples = np.empty((args.bootstrap, 2), np.float64)
    # 来源位等低基数特征按“同分组 × 图”聚合，保留精确的整群自助法。
    compact = None
    if not weighted and len(rank.starts) <= 32:
        score_ids = np.repeat(np.arange(len(rank.starts)), np.diff(np.r_[rank.starts, len(labels)]))
        joint = score_ids * image_count + image_ids[rank.order]
        pos = np.bincount(joint, weights=rank.positive, minlength=len(rank.starts)*image_count)
        neg = np.bincount(joint, weights=~rank.positive, minlength=len(rank.starts)*image_count)
        compact = pos.reshape(-1, image_count), neg.reshape(-1, image_count)
    for b in range(args.bootstrap):
        if weighted:
            # 按 weight 比例抽 n 个切块；抽样次数就是重抽后的权重，不能再乘原 weight。
            counts = rng.multinomial(len(labels), weights / weights.sum())
            samples[b] = rank.evaluate(counts)
        else:
            counts = np.bincount(rng.integers(image_count, size=image_count), minlength=image_count)
            samples[b] = (rank.from_counts(compact[0] @ counts, compact[1] @ counts) if compact is not None
                          else rank.evaluate(counts[image_ids].astype(np.float64)))
    valid = np.isfinite(samples).all(axis=1)
    result['bootstrap_valid'] = int(valid.sum())
    result['bootstrap_degenerate'] = int((~valid).sum())
    if valid.any():
        ci = np.percentile(samples[valid], (2.5, 97.5), axis=0)
        result['auc_ci95'] = ci[:, 0].tolist()
        if include_ap:
            result['ap_ci95'] = ci[:, 1].tolist()
    else:
        result['status'] = 'ci_unavailable_all_resamples_single_class'
    return result


def score_groups(data, values, args, weighted=False, metadata=False):
    result = {}
    for group, select in masks_for(data).items():
        if metadata and group == 'all':
            continue
        result[group] = metric(data['label'][select], values[select], data['weight'][select],
                               data['image_id'][select], data['image_count'], args, weighted, not metadata)
    return result


def load_parts(args, ds):
    names = image_list(ds)[:args.limit or None]
    root = args.out / 'units' / args.population / ds
    expected = {Path(n).stem + '.npz' for n in names}
    present = {p.name for p in root.glob('*.npz') if not p.name.endswith('.tmp.npz')}
    if expected != present:
        raise ValueError(f'{root}: missing {len(expected-present)} or extra {len(present-expected)} image shards; use a separate smoke directory')
    parts = []
    for name in names:
        with np.load(root / (Path(name).stem + '.npz'), allow_pickle=False) as z:
            part = {k: z[k] for k in z.files}
        if int(part['version']) != VERSION or str(part['image']) != name or str(part['dataset']) != ds:
            raise ValueError(f'{root}/{name}: incompatible shard')
        if part['populations'].tolist() != list(populations(args, ds)):
            raise ValueError(f'{root}/{name}: population mismatch')
        parts.append(part)
    return parts


def assemble(parts, pop):
    keys = ('id', 'label', 'weight', 'r', 'k', 'fill', 'cell') + (('meta',) if pop != 'v2' else ())
    data = {k: np.concatenate([p[f'{pop}__{k}'] for p in parts]) for k in keys}
    data['image_id'] = np.concatenate([np.full(len(p[f'{pop}__label']), i, np.int32) for i, p in enumerate(parts)])
    data['image_count'] = len(parts)
    sets = [set(p[f'{pop}__scorers'].tolist()) for p in parts]
    scorers = sorted(set.intersection(*sets))
    unavailable = sorted(set.union(*sets) - set(scorers))
    scores = {s: np.concatenate([p[f'{pop}__score__{s}'] for p in parts]) for s in scorers}
    hist = ({s: np.concatenate([p[f'{pop}__hist__{s}'] for p in parts]) for s in scorers} if pop != 'v2' else {})
    return data, scores, hist, unavailable


def saved_reference(pop, ds, scorer):
    if pop == 'u5':
        path = u5_root(ds) / 'evaluation' / split(ds) / 'summary.json'
        record = read_json(path)['scorers'][scorer]['classification'] if path.exists() else None
    elif pop == 'p6b':
        path = OUT / 'exp6/verifier' / scorer / ('eval_' + ds + '.json')
        record = read_json(path)['recall']['overall'] if path.exists() else None
    else:
        return None
    return dict(path=str(path), auc=record['auc'], ap=record['ap']) if record else dict(path=str(path), status='missing')


def curve(hist):
    if not len(hist):
        return np.array([[np.inf, 0, 0, 0, 0, 0]], np.float64)
    px = hist[np.argsort(-hist[:, 0], kind='stable')]
    ends = np.r_[np.flatnonzero(np.diff(px[:, 0])), len(px)-1]
    return np.vstack([[np.inf, 0, 0, 0, 0, 0], np.column_stack([px[ends, 0], np.cumsum(px[:, 1:], axis=0)[ends]])])


def f1(tp, fp, fn):
    denominator = 2*tp + fp + fn
    return np.divide(2*tp, denominator, out=np.zeros_like(np.asarray(denominator, dtype=float)), where=denominator != 0)


def pixel_result(base, added):
    tp, fp, fn, fn12, fn3, fn5 = map(float, base)
    dtp, dfp, d12, d3, d5 = map(float, added)
    total = tp + fn
    return dict(added_tp=int(dtp), added_fp=int(dfp), added_cell12=int(d12), added_cell3=int(d3), added_cell5=int(d5),
                f1=float(100*f1(tp+dtp, fp+dfp, fn-dtp)),
                delta_f1_pp=float(100*(f1(tp+dtp, fp+dfp, fn-dtp)-f1(tp, fp, fn))),
                miss_pct=100*(fn-dtp)/total, delta_miss_pp=-100*dtp/total,
                miss12_pct=100*(fn12-d12)/total, delta_miss12_pp=-100*d12/total,
                miss3_pct=100*(fn3-d3)/total, delta_miss3_pp=-100*d3/total)


def thresholds(contexts):
    rows = []
    for val, test in PAIRS:
        for pop in ('u5', 'p6b'):
            vctx, tctx = contexts.get((val, pop)), contexts.get((test, pop))
            if not vctx and not tctx:
                continue
            scorers = sorted(set(vctx['hist'] if vctx else ()) | set(tctx['hist'] if tctx else ()))
            for scorer in scorers:
                row = dict(population=pop, dataset='SECOND' if val == 'second_cv' else 'SYSU', scorer=scorer,
                           validation_split=val, test_split=test)
                if not vctx or scorer not in vctx['hist']:
                    rows.append(dict(row, status='validation_unavailable', threshold=None))
                    continue
                c = curve(vctx['hist'][scorer])
                tp, fp, fn = vctx['baseline'][:3]
                delta = 100*(f1(tp+c[:, 1], fp+c[:, 2], fn-c[:, 1])-f1(tp, fp, fn))
                ok = np.flatnonzero(delta >= -.3)
                best = ok[np.argmax(c[ok, 1])]  # 同召回取最高阈值，与 batch37 maxrec 一致。
                threshold = c[best, 0]
                row.update(threshold=float(threshold) if np.isfinite(threshold) else None,
                           accept_none=not np.isfinite(threshold),
                           validation=pixel_result(vctx['baseline'], c[best, 1:]), status='ok')
                if tctx and scorer in tctx['hist']:
                    tc = curve(tctx['hist'][scorer])
                    selected = tc[np.flatnonzero(tc[:, 0] >= threshold)[-1], 1:]
                    row['test'] = pixel_result(tctx['baseline'], selected)
                else:
                    row.update(test=None, status='test_unavailable')
                rows.append(row)
    return rows


def unique_records(rows):
    return list({json.dumps(r, sort_keys=True, ensure_ascii=False): r for r in rows}.values())


def run_merge(args):
    summary = dict(version=VERSION, partial=bool(args.limit), limit=args.limit, seed=args.seed,
                   bootstrap_replicates=args.bootstrap, ci95='percentile 2.5/97.5',
                   bootstrap_p1_p2='resample images uniformly with replacement; preserve all units in each image',
                   bootstrap_p3='draw n pieces with replacement, probability proportional to weight; use draw multiplicity, not weight squared',
                   route='r < 0.10 => fill; k counts 8-connected components of >=5 pixels in D & unit',
                   positive_groups='cell12/cell3 positives from either route; negatives restricted to fill/refine respectively',
                   scorer_inputs=dict(none='V1 metadata', sam_vith='image features + metadata',
                                      dinov3='image features + metadata', remoteclip='image features + metadata',
                                      v2a='images', v2c='images'),
                   missing_scores=[], datasets={}, thresholds=[])
    contexts = {}
    for ds in selected_datasets(args):
        if not populations(args, ds):
            continue
        parts = load_parts(args, ds)
        audits = [json.loads(str(p['audit'])) for p in parts]
        base = np.sum([p['baseline'] for p in parts], axis=0)
        manifest_base = read_json(base_dir(ds) / 'manifest.json')['baseline']
        if not args.limit and base[:3].tolist() != [manifest_base[k] for k in ('tp', 'fp', 'fn')]:
            raise ValueError(f'{ds}: merged pixel baseline differs from batch30 manifest')
        dsrow = dict(images=len(parts), baseline_counts=dict(zip(('tp', 'fp', 'fn', 'fn12', 'fn3', 'fn5'), map(int, base))),
                     baseline=pixel_result(base, np.zeros(5)), manifest_baseline=manifest_base,
                     baseline_source=str(base_dir(ds) / 'manifest.json'), populations={},
                     checks=dict(detector_mismatch_pixels=sum(a['checks'].get('p6b', {}).get('detector_mismatch_pixels', 0) for a in audits),
                                 detector_mismatch_images=sum(a['checks'].get('p6b', {}).get('detector_mismatch_pixels', 0) > 0 for a in audits),
                                 u5_metadata={}),
                     timing=dict(mean_image_seconds=float(np.mean([a['seconds'] for a in audits])),
                                 max_image_seconds=max(a['seconds'] for a in audits),
                                 peak_rss_mib=max(a['peak_rss_mib'] for a in audits)))
        for part, audit in zip(parts, audits):
            summary['missing_scores'] += audit['missing_scores']
            check = audit['checks'].get('u5', {})
            if split(ds) == 'val' and check:
                key = check['feature_status']
                stats = dsrow['checks']['u5_metadata'].setdefault(key, dict(images=0, compared_elements=0))
                stats['images'] += 1
                stats['compared_elements'] += check.get('compared_elements', 0)
                if key != 'exact':
                    stats.setdefault('details', []).append(dict(image=str(part['image']), **check))
        for pop in populations(args, ds):
            data, scores, hist, unavailable = assemble(parts, pop)
            if pop == 'v2' and not args.limit:
                expected = 3000 if split(ds) == 'val' else 6000
                if len(data['label']) != expected:
                    raise ValueError(f'{ds}: V2 merged sample has {len(data["label"])} pieces, expected {expected}')
            poprow = dict(units=len(data['label']), ignored_labels=int((data['label'] < 0).sum()),
                          fill_units=int(data['fill'].sum()), refine_units=int((~data['fill']).sum()),
                          positive_cell_counts={str(c): int(((data['label'] == 1) & (data['cell'] == c)).sum()) for c in (0, 1, 2, 3, 5)},
                          scorers={}, metadata={}, reproduction={})
            for scorer in unavailable:
                summary['missing_scores'].append(dict(population=pop, dataset=ds, scorers=[scorer],
                                                     reason='incomplete image coverage; omitted for the whole split'))
            # P3 验证没有 V2 逐切块分数时，只报测试；不将仅 V1 验证对照冒充完整 P3。
            if pop == 'v2' and split(ds) == 'val' and not any(s.startswith('v2') for s in scores):
                poprow['status'] = 'validation_per_piece_v2_scores_missing; report_test_only'
                scores = {}
            for scorer, values in scores.items():
                print(f'merge {ds}/{pop}/{scorer}', flush=True)
                poprow['scorers'][scorer] = score_groups(data, values, args, weighted=pop == 'v2')
                reference = saved_reference(pop, ds, scorer)
                if reference:
                    reference['status'] = 'partial_not_comparable' if args.limit else 'missing' if 'auc' not in reference else 'checked'
                    actual = poprow['scorers'][scorer]['all']
                    reference['reproduced_auc'], reference['reproduced_ap'] = actual['auc'], actual['ap']
                    if not args.limit and 'auc' in reference and actual['auc'] is not None:
                        reference.update(auc_difference=actual['auc']-reference['auc'], ap_difference=actual['ap']-reference['ap'])
                        reference['matches'] = bool(np.isclose(actual['auc'], reference['auc'], atol=1e-10, rtol=0)
                                                    and np.isclose(actual['ap'], reference['ap'], atol=1e-10, rtol=0))
                        if not reference['matches']:
                            reference['status'] = 'mismatch_review_required'
                    poprow['reproduction'][scorer] = reference
            if pop != 'v2':
                for i, key in enumerate(META + (('source_count',) if pop == 'u5' else ('split',))):
                    print(f'merge {ds}/{pop}/metadata/{key}', flush=True)
                    poprow['metadata'][key] = score_groups(data, data['meta'][:, i], args, metadata=True)
                contexts[(ds, pop)] = dict(baseline=base, hist=hist)
            dsrow['populations'][pop] = poprow
        summary['datasets'][ds] = dsrow
    summary['missing_scores'] = unique_records(summary['missing_scores'])
    summary['thresholds'] = thresholds(contexts)
    suffix = '' if args.population == 'all' else f'_{args.population}'   # 按总体并行 merge 时互不覆盖
    write_json(args.out / f'summary{suffix}.json', summary)
    write_tables(args.out / f'tables{suffix}.md', summary)
    print(f'Saved {args.out / f"summary{suffix}.json"} and {args.out / f"tables{suffix}.md"}', flush=True)


def format_metric(row, key):
    value = row.get(key)
    if value is None:
        return '—（' + row['status'] + '）'
    ci = row.get(key + '_ci95')
    return f'{value:.3f}' + (f' [{ci[0]:.3f}, {ci[1]:.3f}]' if ci else '（CI 不可用）')


def write_tables(path, summary):
    lines = ['# 按格拆开的判断器与元数据统计', '',
             f'范围：{"极小冒烟，不能作实验结论" if summary["partial"] else "完整所选数据集"}。'
             f'种子 {summary["seed"]}；{summary["bootstrap_replicates"]} 次自助法；95% 百分位区间。', '',
             'P1/P2 按图整群重抽；P3 点估计按 weight 加权，自助法按 weight 比例抽切块，抽样次数作为权重。'
             '正例不足 30 或没有负例时仅报计数。元数据保留原始方向，AUC < 0.5 表示反向。', '',
             'c/d 组的正例按格选取（不限去向），负例分别取补漏/精修。U5 的 -1 标签不进入分类指标，'
             '补漏像素场使用全部补漏 U5 单元；P2 只使用有 0/1 标签的 recall 切块。', '',
             'none 为 V1 元数据版；sam_vith/dinov3/remoteclip 为图像特征加元数据；V2a/V2c 为图像判断器。', '']
    for ds, result in summary['datasets'].items():
        checks = result['checks']
        lines += [f'## {ds}（{result["images"]} 图）', '',
                  f'P2 历史掩码与 batch30 不一致：{checks["detector_mismatch_pixels"]} 像素，'
                  f'{checks["detector_mismatch_images"]} 图。统计基线使用 batch30。', '',
                  'U5 验证元数据核对：' + json.dumps(checks['u5_metadata'], ensure_ascii=False) + '。', '']
        for pop, row in result['populations'].items():
            lines += [f'### {pop}', '', f'单元 {row["units"]}；补漏 {row["fill_units"]}；精修 {row["refine_units"]}；'
                      f'忽略标签 {row["ignored_labels"]}。', '',
                      '| 判断器 | 分组 | 正例 | 负例 | 正例权重和 | 负例权重和 | AUC [95% CI] | AP [95% CI] |',
                      '|---|---|---:|---:|---:|---:|---|---|']
            for scorer, groups in row['scorers'].items():
                for group, m in groups.items():
                    lines.append(f'| {scorer} | {GROUPS[group]} | {m["positive"]} | {m["negative"]} | '
                                 f'{m["positive_weight"]:.1f} | {m["negative_weight"]:.1f} | '
                                 f'{format_metric(m, "auc")} | {format_metric(m, "ap")} |')
            if row.get('status'):
                lines += ['', row['status']]
            if row['reproduction']:
                lines += ['', '| 总体复现 | 已保存 AUC | 本次 AUC | 已保存 AP | 本次 AP | 核对状态 |',
                          '|---|---:|---:|---:|---:|---|']
                for scorer, ref in row['reproduction'].items():
                    vals = ['—' if ref.get(k) is None else f'{ref[k]:.3f}' for k in ('auc', 'reproduced_auc', 'ap', 'reproduced_ap')]
                    lines.append('| ' + ' | '.join([scorer, *vals, ref['status']]) + ' |')
            if row['metadata']:
                lines += ['', '| 元数据 | 分组 | 正例 | 负例 | 原始 AUC [95% CI] |', '|---|---|---:|---:|---|']
                for feature, groups in row['metadata'].items():
                    for group, m in groups.items():
                        lines.append(f'| {feature} | {GROUPS[group]} | {m["positive"]} | {m["negative"]} | {format_metric(m, "auc")} |')
            lines.append('')
    lines += ['## 验证选阈值，测试评一次', '',
              '仅补漏单元；重叠像素取最大分数；ΔF1 ≥ −0.3 个百分点下选最高像素召回。'
              '漏检率分母均为全部 GT 像素，变化量单位为百分点；负值表示漏检减少。', '',
              '| 总体 | 数据集 | 判断器 | 阈值 | 部分 | ΔF1 | Δ漏检率 | Δ①+②漏检率 | Δ③漏检率 |',
              '|---|---|---|---|---|---:|---:|---:|---:|']
    for row in summary['thresholds']:
        threshold = '不接收' if row.get('accept_none') else str(row.get('threshold', '—'))
        for portion in ('validation', 'test'):
            if row.get(portion):
                v = row[portion]
                lines.append('| ' + ' | '.join([row['population'], row['dataset'], row['scorer'], threshold, portion,
                             *(f'{v[k]:.1f}' for k in ('delta_f1_pp', 'delta_miss_pp', 'delta_miss12_pp', 'delta_miss3_pp'))]) + ' |')
        if row['status'] != 'ok':
            lines.append(f'| {row["population"]} | {row["dataset"]} | {row["scorer"]} | {row["status"]} | — | — | — | — | — |')
    lines += ['', '## 缺失的已保存分数', '']
    if not summary['missing_scores']:
        lines += ['本次选择范围未发现缺失。']
    for row in summary['missing_scores']:
        lines.append('- ' + json.dumps(row, ensure_ascii=False))
    tmp = path.with_suffix('.tmp.md')
    tmp.write_text('\n'.join(lines) + '\n')
    tmp.replace(path)


def selected_datasets(args):
    return DATASETS if args.dataset == 'all' else (args.dataset,)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('units', 'merge'))
    parser.add_argument('--dataset', choices=('all',) + DATASETS, default='all')
    parser.add_argument('--population', choices=('all', 'u5', 'p6b', 'v2'), default='all')
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--limit', type=int, default=0, help='取各数据集前 N 图后再按图分片；0 为完整集')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--bootstrap', type=int, default=BOOTSTRAPS, help='正式固定 2000；极小冒烟可缩减')
    args = parser.parse_args()
    args.out = args.out.resolve()
    if not args.out.is_relative_to(HERE) or args.out == HERE:
        parser.error('--out 必须是 batch40 内的专用子目录')
    if args.limit < 0 or args.shards < 1 or not 0 <= args.shard < args.shards or args.bootstrap < 1:
        parser.error('invalid limit/shard/bootstrap')
    if args.bootstrap != BOOTSTRAPS and not 0 < args.limit <= 2:
        parser.error('正式统计必须使用 2000 次自助法；缩减只允许 --limit 1/2')
    if args.stage == 'merge' and (args.shard != 0 or args.shards != 1):
        parser.error('merge 读取全部逐图分片，不接受分片参数')
    if args.population in ('p6b', 'v2') and args.dataset.startswith('sysu'):
        parser.error('P2/P3 只适用于 SECOND')
    if args.stage == 'units':
        for ds in selected_datasets(args):
            run_units(args, ds)
    else:
        run_merge(args)


if __name__ == '__main__':
    main()
