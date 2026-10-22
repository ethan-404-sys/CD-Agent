"""SYSU frozen-test audit: 200 unique units, 20 repeats, batch20/6g rendering."""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

# Import the local quality module before the legacy runtime adjusts sys.path.
from exp8_quality import check_selection
from exp8_batch21_common import (DEFAULT_AUDIT, MAIN, arguments, batch20_module,
                                 checked_units, configure, provenance, read_pool, rt,
                                 selected_names, write_fixed)

audit20 = batch20_module('exp8_audit')
STRATA = audit20.STRATA
original_page_html = audit20.page_html


def page_html(manifest):
    content = original_page_html(manifest).decode('utf-8')
    note = (f'<p>随机种子：{manifest["config"]["seed"]}；审核者为非专家，答案只作参考。'
            '“独立对象”指不同的（图像、单元编号），同图或同一父对象可有多个单元。</p>')
    return content.replace('<h1>对象变化审核</h1>', '<h1>对象变化审核</h1>' + note).replace(
        'exp8-batch20-audit-', 'exp8-batch21-sysu-audit-').encode('utf-8')


audit20.page_html = page_html


def sample(args):
    images = selected_names(args, sharded=False)
    choice = check_selection(args) if not args.limit else None
    key = MAIN['sysu']
    directory = args.audit_dir
    config = dict(dataset='sysu', unit=key, seed=args.seed, images=images,
                  partial=bool(args.limit), provenance=provenance(args),
                  cleaning_rule=choice['rule'] if choice else None,
                  high_negative='top ceil(10% of negative units) by mean detector probability inside mask; '
                                'ties: image name, then unit_id',
                  target_proportions={k: .25 for k in STRATA},
                  shortage='cap each equal quota by availability; Hamilton-proportional redistribution '
                           'over remaining stratum capacities',
                  requested_unique=20 if args.limit else 200,
                  requested_duplicates=2 if args.limit else 20)
    path = directory / 'samples.json'
    if path.exists():
        existing = rt.read_json(path)
        if existing['config'] != config:
            raise ValueError('Audit configuration differs; use a fresh --audit-dir')
        return existing, directory
    candidates = []
    for name in images:
        pool = read_pool(args, name)
        objects, shape, details = checked_units(args, name, key, pool)
        prob = rt.probability('sysu', name, args.batch19)
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
    allocation = {k: min(counts[k], requested // 4) for k in STRATA}
    missing = requested - sum(allocation.values())
    if missing:
        extra = audit20.proportional({k: counts[k] - allocation[k] for k in STRATA}, missing)
        allocation = {k: allocation[k] + extra[k] for k in STRATA}
    rng = random.Random(args.seed)
    records = []
    for stratum in STRATA:
        for row in rng.sample(populations[stratum], allocation[stratum]):
            records.append(dict(row, id=f'{rng.getrandbits(128):032x}', dataset='sysu',
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
                       crop=audit20.crop_box(obj['bbox'], shape), piece_id=row['unit_id'])
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser, ('sysu',))
    parser.add_argument('--seed', type=int, default=20261022)
    parser.add_argument('--audit-dir', type=Path)
    args = parser.parse_args()
    configure(args)
    if args.audit_dir is None:
        args.audit_dir = args.out / 'audit/exp8_sysu' if args.limit else DEFAULT_AUDIT
    args.audit_dir = args.audit_dir.resolve()
    if args.limit and args.audit_dir == DEFAULT_AUDIT:
        raise ValueError('Smoke audit must use a separate directory')
    manifest, directory = sample(args)
    audit20.render(args, manifest, directory)
    report_path = directory / 'page_report.json'
    report = rt.read_json(report_path)
    report['renderer'] = 'exp6v2_data.Renderer with batch19 frozen SYSU object masks'
    report['seed'] = args.seed
    report['reviewer'] = 'nonexpert; answers are references only'
    rt.write_json(report_path, report)


if __name__ == '__main__':
    main()
