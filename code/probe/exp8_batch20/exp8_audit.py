"""Frozen test-pool sampling and offline object-contour audit pages; no human labels."""
from __future__ import annotations

import argparse
import html
import json
import math
import random
from collections import Counter
from pathlib import Path

from exp8_batch20_common import (MAIN, arguments, checked_units, pool_dir, read_pool,
                                rt, selected_names, write_fixed)
from exp6a_pieces import crop_box
from exp6g_sample import proportional
from exp6g_page import HTML, Renderer, context_box, jpeg_bytes

STRATA = ('positive', 'negative', 'high_negative', 'middle')
SIZES = dict(second=200, levir=100, whu=100)


def sample(args):
    images = selected_names(args, sharded=False)
    # Test review is descriptive only after the development cleaning choice is frozen.
    if not args.limit:
        choice = rt.read_json(args.out / 'cleaning/selected.json')
        if choice['partial'] or choice['selection_metrics'] != ['pixel_f1', 'pixel_iou']:
            raise ValueError('Formal test audit requires completed SECOND cleaning selection')
    key = MAIN[args.dataset]
    config = dict(dataset=args.dataset, unit=key, seed=args.seed, images=images,
                  partial=bool(args.limit), batch18=str(args.batch18.resolve()),
                  high_negative='top ceil(10% of negative units) by mean detector probability inside mask; '
                                'ties: image name, then unit_id',
                  target_proportions={k: .25 for k in STRATA},
                  shortage='cap each equal quota by availability; Hamilton-proportional redistribution '
                           'over remaining stratum capacities',
                  requested_unique=20 if args.limit else SIZES[args.dataset],
                  requested_duplicates=(2 if args.limit else 20) if args.dataset == 'second' else 0)
    directory = args.out / 'audit' / f'exp8_{args.dataset}'
    path = directory / 'samples.json'
    if path.exists():
        existing = rt.read_json(path)
        if existing['config'] != config:
            raise ValueError('Audit IDs/configuration already exist; use a new output root')
        return existing, directory
    candidates = []
    for name in images:
        pool = read_pool(args, name)
        objects, shape, details = checked_units(args, name, key, pool)
        prob = rt.probability(args.dataset, name, args.batch18)
        if prob.shape != shape:
            raise ValueError(f'{name}: detector probability shape differs')
        for obj, detail in zip(objects, details):
            x0, y0, x1, y1 = obj['bbox']
            score = float(prob[y0:y1, x0:x1][obj['mask']].mean())
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f'{name}: invalid detector mean probability')
            candidates.append(dict(image=name, unit_id=detail['unit_id'], label=detail['label'],
                                   mean_probability=score, stratum=detail['label']))
    negatives = sorted((r for r in candidates if r['label'] == 'negative'),
                       key=lambda r: (-r['mean_probability'], r['image'], r['unit_id']))
    high_count = math.ceil(.1 * len(negatives))
    for row in negatives[:high_count]:
        row['stratum'] = 'high_negative'
    populations = {k: [r for r in candidates if r['stratum'] == k] for k in STRATA}
    counts = {k: len(v) for k, v in populations.items()}
    requested = config['requested_unique']
    if sum(counts.values()) < requested:
        raise ValueError(f'Only {sum(counts.values())} unique units, requested {requested}')
    equal = {k: requested // 4 for k in STRATA}
    allocation = {k: min(counts[k], equal[k]) for k in STRATA}
    missing = requested - sum(allocation.values())
    if missing:
        extra = proportional({k: counts[k] - allocation[k] for k in STRATA}, missing)
        allocation = {k: allocation[k] + extra[k] for k in STRATA}
    # Dataset-specific RNG also keeps browser storage IDs distinct between pages.
    seed = args.seed + {'second': 0, 'levir': 10000, 'whu': 20000}[args.dataset]
    rng = random.Random(seed)
    records = []
    for stratum in STRATA:
        for row in rng.sample(populations[stratum], allocation[stratum]):
            records.append(dict(row, id=f'{rng.getrandbits(128):032x}', dataset=args.dataset,
                                unit=key, is_duplicate=False, original_id=None))
    if len({(r['image'], r['unit_id']) for r in records}) != requested:
        raise ValueError('Independent audit objects must have unique image/unit IDs')
    by_image = {}
    for row in records:
        by_image.setdefault(row['image'], []).append(row)
    for name, chosen in by_image.items():
        pool = read_pool(args, name)
        objects, shape, details = checked_units(args, name, key, pool)
        for row in chosen:
            obj, detail = objects[row['unit_id']], details[row['unit_id']]
            row.update(bbox=obj['bbox'], area=obj['area'], gt_fraction=detail['gt_fraction'],
                       crop=crop_box(obj['bbox'], shape), piece_id=row['unit_id'])
    duplicates = rng.sample(records, config['requested_duplicates'])
    for original in duplicates:
        records.append(dict(original, id=f'{rng.getrandbits(128):032x}',
                            is_duplicate=True, original_id=original['id']))
    rng.shuffle(records)
    manifest = dict(config=config, original_count=requested, duplicate_count=len(duplicates),
                    populations=counts, sampled=allocation,
                    proportions={k: allocation[k] / requested for k in STRATA},
                    high_negative_boundary=negatives[high_count-1] if high_count else None,
                    samples=records,
                    purpose='frozen test-pool description only; nonexpert human answers are references',
                    ground_truth_in_page=False)
    write_fixed(path, manifest)
    return manifest, directory


def page_html(manifest):
    labels = dict(positive='正例', negative='普通负例', high_negative='高分负例', middle='中间标签')
    proportions = '、'.join(f'{labels[k]} {manifest["sampled"][k]} 个（{100*manifest["proportions"][k]:.1f}%）'
                            for k in STRATA)
    explanation = (f'<p>数据集：{html.escape(manifest["config"]["dataset"])}；'
                   f'单元：{html.escape(manifest["config"]["unit"])}。'
                   f'{manifest["original_count"]} 个独立对象，另加 {manifest["duplicate_count"]} 个重复项。'
                   f'独立对象分层：{proportions}，各层互斥。目标为各 25%；不足时按剩余层容量比例补足。</p>'
                   '<p>正例真值占比 ≥50%，负例 &lt;10%，中间标签为 [10%,50%)。'
                   '高分负例是负例中对象内检测器平均概率排名最高的前 10%（向上取整）；'
                   '同分按图名、单元编号排序，其余为普通负例。当前对象的标签、分数和重复身份不在页面显示。</p>'
                   '<p>本页仅描述冻结方案后的测试池；审核者的答案只作参考。'
                   '轮廓标出当前池单元；U5 的大对象可能已拆成多个单元。'
                   '轮廓内仅一部分发生真实变化时请选择“部分变化”。</p>')
    if manifest['config']['partial']:
        explanation += '<p><strong>20 张图冒烟页面，样本量缩减；不用于正式人工审核。</strong></p>'
    replacements = {
        '<h1>两期影像审核</h1>': '<h1>对象变化审核</h1>' + explanation,
        '<button data-choice="unclear">3 · 看不清</button>':
            '<button data-choice="partial">4 · 部分变化</button>\n<button data-choice="unclear">3 · 看不清</button>',
        'const options = {changed:': 'const options = {partial:"部分变化",changed:',
        'const mapping = {"1":': 'const mapping = {"4":"partial","1":',
        '快捷键：1 / 2 / 3 / 0': '快捷键：1 / 2 / 3 / 4 / 0',
        'exp6g-audit-': 'exp8-batch20-audit-',
        '__IDS__': json.dumps([r['id'] for r in manifest['samples']]),
    }
    result = HTML
    for old, new in replacements.items():
        if result.count(old) != 1:
            raise ValueError(f'6g HTML template changed at {old!r}; review adapter')
        result = result.replace(old, new)
    return result.encode('utf-8')


def render(args, manifest, directory):
    page = directory / 'label_audit.html'
    report_path = directory / 'page_report.json'
    crops = directory / 'crops'
    crops.mkdir(exist_ok=True)
    content = page_html(manifest)
    limit = 40_000_000
    budget = (limit - len(content) - (directory / 'samples.json').stat().st_size - 64_000) // (4*len(manifest['samples']))
    renderer = Renderer()
    sizes, qualities = [], Counter()
    for row in sorted(manifest['samples'], key=lambda r: (r['image'], r['id'])):
        key = (args.dataset, row['image'])
        if renderer.key != key:
            objects, shape = rt.load_units(pool_dir(args) / row['unit'] / f'{Path(row["image"]).stem}.npz')
            # Populate the existing renderer's cache with NEW object masks. Its render()
            # then draws these contours unchanged and never opens the legacy piece pool.
            renderer.rgb = [rt.common.load_rgb(args.dataset, row['image'], view) for view in ('t1', 't2')]
            renderer.masks = {i: (o['bbox'], o['mask']) for i, o in enumerate(objects)}
            renderer.key = key
            if renderer.rgb[0].shape[:2] != shape:
                raise ValueError(f'{row["image"]}: RGB/object shape mismatch')
        near = renderer.render(args.dataset, row)
        wide = renderer.render(args.dataset, dict(row, crop=context_box(row['bbox'], row['crop'], renderer.rgb[0].shape[:2])))
        for suffix, image in zip(('t1', 't2', 'context_t1', 'context_t2'), near + wide):
            data, quality = jpeg_bytes(image, budget, 85, 35)
            (crops / f'{row["id"]}_{suffix}.jpg').write_bytes(data)
            sizes.append(len(data))
            qualities[quality] += 1
    page.write_bytes(content)
    report = dict(status='generated_not_reviewed', originals=manifest['original_count'],
                  duplicates=manifest['duplicate_count'], images=len(sizes),
                  partial=manifest['config']['partial'], quality_counts=dict(qualities),
                  jpeg_bytes=sum(sizes), bundle_budget_bytes=limit,
                  choices=['changed', 'unchanged', 'partial', 'unclear', 'skip'],
                  renderer='exp6v2_data.Renderer with batch18 new object masks',
                  sampling='exp6g_sample.proportional; mutually exclusive strata',
                  page='exp6g_page.HTML/context_box/jpeg_bytes',
                  ground_truth_overlays=False, human_review_performed=False)
    rt.write_json(report_path, report)
    if sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) > limit:
        raise ValueError('Audit bundle exceeds 40 MB')
    print(page, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser, ('second', 'levir', 'whu'))
    parser.add_argument('--seed', type=int, default=20261022)
    args = parser.parse_args()
    manifest, directory = sample(args)
    render(args, manifest, directory)


if __name__ == '__main__':
    main()
