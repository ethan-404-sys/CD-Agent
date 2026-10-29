"""真实冻结单元统计、嵌套训练采样、加权验证集与 exp6k 原规则渲染。"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict, deque
from contextlib import closing
import csv
import math
from pathlib import Path
import random

import exp6m_common as c
import numpy as np


def is_doubt(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', '是', '真值可疑', 'gt_doubt'}


def exclusions(dataset):
    """只读审核记录；reject 作为未通过审核排除，原因与明确 GT 可疑分开保留。"""
    result = defaultdict(set)
    with closing(c.readonly()) as db:
        query = '''SELECT s.image,s.cid,s.meta,r.status,r.note FROM sample s
                   LEFT JOIN sample_review r ON r.sample_id=s.id WHERE s.dataset=?'''
        for row in db.execute(query, (dataset,)):
            meta = c.json.loads(row['meta'] or '{}')
            unit = int(meta['unit_id']) if 'unit_id' in meta else int(row['cid'].rsplit(':', 1)[1])
            key = row['image'], unit
            if row['status'] == 'reject':
                result[key].add('sample_review:reject')
            if is_doubt(meta.get('gt_doubt')) or '真值可疑' in (row['note'] or ''):
                result[key].add('sample_review/meta:gt_doubt')
        query = '''SELECT s.image,s.cid,s.meta,i.review_status,i.review_note FROM sample s
                   JOIN sample_set_item i ON i.sample_id=s.id WHERE s.dataset=?'''
        for row in db.execute(query, (dataset,)):
            meta = c.json.loads(row['meta'] or '{}')
            unit = int(meta['unit_id']) if 'unit_id' in meta else int(row['cid'].rsplit(':', 1)[1])
            if row['review_status'] == 'reject':
                result[(row['image'], unit)].add('sample_set_item:reject')
            if '真值可疑' in (row['review_note'] or ''):
                result[(row['image'], unit)].add('sample_set_item:gt_doubt')
    for path in sorted((c.B49 / 'run').glob(f'annotations_{dataset}*.csv')):
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if is_doubt(row.get('gt_doubt')):
                    unit = int(row['unit_id']) if row.get('unit_id') else int(row['cid'].rsplit(':', 1)[1])
                    result[(row['image'], unit)].add(f'{path.name}:gt_doubt')
    for path in sorted((c.B49 / 'run').glob(f'exp6k_*_{dataset}.json')):
        data = c.read(path)
        if isinstance(data, list):
            for row in data:
                if is_doubt(row.get('gt_doubt')):
                    result[(row['image'], int(row['unit_id']))].add(f'{path.name}:gt_doubt')
    return result


def exclusion_report(excluded):
    return [dict(image=image, unit_id=unit, reasons=sorted(reasons))
            for (image, unit), reasons in sorted(excluded.items())]


def inventory(dataset, split):
    return c.upstream_data().inventory(dataset, split)


def eligible(z, excluded):
    names = z['images']
    return np.array([(str(names[int(i)]), int(unit)) not in excluded
                     for i, unit in zip(z['image_id'], z['id'], strict=True)])


def counts(z, keep=None):
    if keep is None:
        keep = np.ones(len(z['id']), bool)
    p, m = z['precision'][keep], z['majority'][keep]
    pos, neg = p >= .5, p < .1
    return dict(images=len(z['images']), images_with_units=len(np.unique(z['image_id'][keep])),
                units=len(p), pos=int(pos.sum()), neg=int(neg.sum()), mid=int((~pos & ~neg).sum()),
                **{f'pos{k}': int((pos & (m == k)).sum()) for k in (1, 2, 3, 5)},
                train_pos_ign3=int((pos & (m != 3)).sum()),
                novel_q=np.quantile(z['novel'][keep], [.25, .5, .75]).tolist() if len(p) else [])


def stats(a):
    raw, clean, removed = {}, {}, {}
    for dataset in ('second', 'sysu'):
        excluded = exclusions(dataset)
        removed[dataset] = exclusion_report(excluded)
        for split in ('train', 'val', 'test'):
            z = inventory(dataset, split)
            name = f'{dataset}_{split}'
            raw[name], clean[name] = counts(z), counts(z, eligible(z, excluded))
    reference = c.read(a.reference)
    differences = {k: dict(reference=reference.get(k), recomputed=v)
                   for k, v in raw.items() if reference.get(k) != v}
    c.write(a.out, dict(raw=raw, after_review_exclusion=clean, exclusions=removed,
                        reference=str(a.reference.resolve()), differences=differences,
                        sysu_pos2_below_1000=clean['sysu_train']['pos2'] < 1000))
    if differences:
        raise ValueError('复算与已有 counts.json 不同，详见统计输出')
    print('六个划分统计与 counts.json 完全一致')


def round_robin(indices, image_ids, rng):
    groups = defaultdict(list)
    for j in indices:
        groups[int(image_ids[j])].append(int(j))
    keys = sorted(groups)
    rng.shuffle(keys)
    for group in groups.values():
        rng.shuffle(group)
    pending = deque(keys)
    while pending:
        key = pending.popleft()
        yield groups[key].pop()
        if groups[key]:
            pending.append(key)


def hard_mask(z, good, per_image):
    neg = good & (z['precision'] < .1)
    hard = np.zeros(len(neg), bool)
    if per_image:
        for image_id in np.unique(z['image_id'][neg]):
            js = np.flatnonzero(neg & (z['image_id'] == image_id))
            # 稳定排序确定并列 D 的次序；每图 ceil(20%×负例数)。
            order = np.argsort(-z['dscore'][js], kind='stable')
            hard[js[order[:math.ceil(len(js) / 5)]]] = True
    elif neg.any():
        # exp6k 的验证/测试采用全划分 D 的 80% 分位数，保留并列。
        hard = neg & (z['dscore'] >= np.quantile(z['dscore'][neg], .8))
    return hard


def make_row(z, j, dataset, split, stratum, weight=1.):
    name, unit = str(z['images'][int(z['image_id'][j])]), int(z['id'][j])
    return dict(dataset=dataset, split=split, image=name, unit_id=unit,
                cid=f'{Path(name).stem}:{unit}', label=int(z['precision'][j] >= .5),
                precision=float(z['precision'][j]), majority=int(z['majority'][j]),
                bbox=z['bbox'][j].astype(int).tolist(), novel=int(z['novel'][j]), area=int(z['novel'][j]),
                det_mean=float(z['dscore'][j]), m1_score=float(z['m1score'][j]),
                stratum=stratum, weight=float(weight))


def sample(a):
    z = inventory(a.dataset, 'train')
    excluded = exclusions(a.dataset)
    good = eligible(z, excluded)
    positive = good & (z['precision'] >= .5)
    if a.pos_policy == 'ign3':
        positive &= z['majority'] != 3
    rng = random.Random(a.seed)
    used = Counter()
    chosen = []
    for j in round_robin(np.flatnonzero(positive), z['image_id'], rng):
        image = int(z['image_id'][j])
        if used[image] < a.per_image_cap:
            chosen.append(j)
            used[image] += 1
            if a.dataset == 'second' and a.max_pos and len(chosen) >= a.max_pos:
                break
    if not chosen:
        raise ValueError('没有可用正例')
    hard = hard_mask(z, good, per_image=True)
    neg = good & (z['precision'] < .1)
    streams = [iter(round_robin(np.flatnonzero(mask), z['image_id'], rng))
               for mask in (hard, neg & ~hard)]

    def take(which):
        for j in streams[which]:
            image = int(z['image_id'][j])
            if used[image] < a.per_image_cap:
                used[image] += 1
                return j
        return None

    # 所有比例生成同一条负例序列，再取前缀。每个前缀中难负例至少占一半。
    negatives, hard_n = [], 0
    while len(negatives) < 5 * len(chosen):
        prefer_hard = hard_n * 2 <= len(negatives)
        j = take(0 if prefer_hard else 1)
        if j is None and not prefer_hard:
            j = take(0)
        if j is None:
            break
        negatives.append(j)
        hard_n += int(hard[j])
    target = a.neg_ratio * len(chosen)
    selected_neg = negatives[:target]
    data = [make_row(z, j, a.dataset, 'train', 'pos2' if z['majority'][j] == 2 else 'pos_other')
            for j in chosen]
    data += [make_row(z, j, a.dataset, 'train', 'hard_neg' if hard[j] else 'rand_neg')
             for j in selected_neg]
    c.validate_manifest(data, a.dataset, 'train')
    c.write_rows(a.out, data)
    report = dict(dataset=a.dataset, seed=a.seed, pos_policy=a.pos_policy,
                  neg_ratio_requested=a.neg_ratio, per_image_cap=a.per_image_cap, max_pos=a.max_pos,
                  eligible_positives=int(positive.sum()), positives=len(chosen), negatives=len(selected_neg),
                  positive_dropped_by_cap_or_max_pos=int(positive.sum()) - len(chosen),
                  positive_classes=dict(Counter(str(z['majority'][j]) for j in chosen)),
                  hard_negatives=int(hard[selected_neg].sum()), requested_negatives=target,
                  negative_shortfall=target-len(selected_neg), ratio_achieved=len(selected_neg)/len(chosen),
                  ratio_satisfied=len(selected_neg) == target, counts=dict(Counter(r['stratum'] for r in data)),
                  max_per_image=max(Counter(r['image'] for r in data).values()),
                  exclusions=exclusion_report(excluded),
                  rule='先按图轮流保留正例至每图上限，SECOND 再限 max_pos；负例用剩余容量，三个比例为同一序列前缀；不足时报告实际比例，不降低正例数或放宽上限。')
    c.write(a.out.with_suffix('.stats.json'), report)
    print(c.json.dumps({k: report[k] for k in ('dataset', 'positives', 'negatives', 'ratio_achieved', 'negative_shortfall')}, ensure_ascii=False))


def valset(a):
    z = inventory(a.dataset, 'val')
    excluded = exclusions(a.dataset)
    good = eligible(z, excluded)
    pos = good & (z['precision'] >= .5)
    neg = good & (z['precision'] < .1)
    hard = hard_mask(z, good, per_image=False)
    strata = {'pos2': (pos & (z['majority'] == 2), None),
              'pos_other': (pos & (z['majority'] == 1), 300),
              'hard_neg': (hard, 1000), 'rand_neg': (neg & ~hard, 1000)}
    rng = random.Random(a.seed)
    data, summary = [], {}
    for name, (mask, maximum) in strata.items():
        population = np.flatnonzero(mask).tolist()
        n = min(len(population), maximum) if maximum is not None else len(population)
        selected = rng.sample(population, n)
        weight = len(population)/n if n else None
        summary[name] = dict(population=len(population), selected=n, weight=weight)
        data.extend(make_row(z, j, a.dataset, 'val', name, weight) for j in selected)
    c.validate_manifest(data, a.dataset, 'val')
    c.write_rows(a.out, data)
    c.write(a.out.with_suffix('.stats.json'), dict(dataset=a.dataset, seed=a.seed, strata=summary,
                primary='pos2 vs hard_neg/rand_neg; weight=population/selected',
                hard_rule='全验证划分负例 D ≥ 80% 分位数，与 exp6k 相同',
                exclusions=exclusion_report(excluded)))
    print(a.dataset, summary)


def render(a):
    source = c.rows(a.manifest)
    c.validate_manifest(source)
    out = c.local(a.out)
    if a.merge:
        collected = {}
        for i in range(a.shards):
            part = c.read(out.with_suffix(f'.part-{i:03d}.json'))
            if part['manifest'] != str(a.manifest.resolve()) or part['shards'] != a.shards:
                raise ValueError('渲染分片来源不符')
            for row in part['rows']:
                key = c.sample_key(row)
                if key in collected:
                    raise ValueError('重复分片样本')
                collected[key] = row
        if set(collected) != {c.sample_key(r) for r in source}:
            raise ValueError('分片未完整覆盖清单')
        data = [collected[c.sample_key(r)] for r in source]
        c.validate_manifest(data, images=True)
        c.write_rows(out, data)
        copy_sampling_stats(a.manifest, out)
        return
    upstream = c.upstream_data()
    crop_box, render_pair = upstream.load_crop_functions()
    groups = defaultdict(list)
    for row in source:
        groups[(row['dataset'], row['split'], row['image'])].append(row)
    tables, lookup, rendered = {}, {}, []
    written = skipped = 0
    for i, ((dataset, split, name), group) in enumerate(sorted(groups.items())):
        if i % a.shards != a.shard:
            continue
        key = dataset, split
        if key not in tables:
            z = upstream.inventory(dataset, split, masks=True)
            tables[key] = z
            lookup[key] = {(str(z['images'][int(im)]), int(unit)): j
                           for j, (im, unit) in enumerate(zip(z['image_id'], z['id'], strict=True))}
        z = tables[key]
        rgb = None
        for row in group:
            dest = c.local(a.render_root / dataset / split / Path(name).stem / str(row['unit_id']))
            paths = {k: str(dest / (k + '.jpg')) for k in ('t1', 't2')}
            if all(Path(p).exists() for p in paths.values()):
                skipped += 1
            else:
                if rgb is None:
                    rgb = upstream.rgb_pair(dataset, split, name)
                j = lookup[key][(name, row['unit_id'])]
                box = z['bbox'][j].astype(int).tolist()
                if box != row['bbox']:
                    raise ValueError('清单 bbox 与冻结缓存不同')
                mask = np.zeros(rgb[0].shape[:2], bool)
                x0, y0, x1, y1 = box
                mask[y0:y1, x0:x1] = upstream.unpack(z, j)
                pair = render_pair(rgb, mask, None, crop_box(box, mask.shape))
                dest.mkdir(parents=True, exist_ok=True)
                for k, im in zip(('t1', 't2'), pair, strict=True):
                    path = Path(paths[k])
                    temp = path.with_name(f'.{k}.{c.os.getpid()}.tmp')
                    im.save(temp, format='JPEG', quality=90, subsampling=0)
                    temp.replace(path)
                written += 1
            rendered.append(dict(row, inputs=paths))
    part = dict(manifest=str(a.manifest.resolve()), shards=a.shards, shard=a.shard,
                rows=rendered, written=written, skipped=skipped)
    c.write(out.with_suffix(f'.part-{a.shard:03d}.json'), part)
    if a.shards == 1:
        ordered = {c.sample_key(r): r for r in rendered}
        c.write_rows(out, [ordered[c.sample_key(r)] for r in source])
        copy_sampling_stats(a.manifest, out)
    print(f'render: written={written}, skipped={skipped}, shard={a.shard}/{a.shards}')


def copy_sampling_stats(source, out):
    path = source.with_suffix('.stats.json')
    if path.exists():
        c.write(out.with_suffix('.stats.json'), dict(c.read(path), source_manifest=str(source.resolve())))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('stats')
    s.add_argument('--out', type=Path, default=c.HERE / 'stats/recomputed.json')
    s.add_argument('--reference', type=Path, default=c.HERE / 'stats/counts.json')
    for command in ('sample', 'valset'):
        s = sub.add_parser(command)
        s.add_argument('--dataset', choices=['sysu', 'second'], required=True)
        s.add_argument('--out', type=Path, required=True)
        s.add_argument('--seed', type=int, default=c.SEED)
        if command == 'sample':
            s.add_argument('--pos-policy', choices=['ign3', 'all'], default='ign3')
            s.add_argument('--max-pos', type=int, default=15000)
            s.add_argument('--neg-ratio', type=int, choices=[1, 3, 5], default=3)
            s.add_argument('--per-image-cap', type=int, default=8)
    s = sub.add_parser('render')
    s.add_argument('--manifest', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--render-root', type=Path, default=c.HERE / 'run/render')
    s.add_argument('--shard', type=int, default=0)
    s.add_argument('--shards', type=int, default=1)
    s.add_argument('--merge', action='store_true')
    return p


def main():
    p = parser()
    a = p.parse_args()
    if a.command == 'sample' and (a.per_image_cap < 1 or a.max_pos < 0):
        p.error('per-image-cap 必须为正；max-pos 必须非负（0 表示不限）')
    if a.command == 'render' and not 0 <= a.shard < a.shards:
        p.error('分片参数无效')
    globals()[a.command](a)


if __name__ == '__main__':
    main()
