"""Reproduce three SECOND oracle gains, then decompose pixels and unit sensitivity."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from exp8_common import (OUT, add_counts, index_jsonl, load_label, load_pred, metrics,
                         names, np, parallel_map, read_json, read_spans, score_counts,
                         scores, write_json)
from common import load_classes
from exp6_pool_io import load_piece_masks
from objects import gt_label, match

METHODS = {'2b': 7.81, 'BG': 6.96, 'BGH1': 8.50}
CATEGORIES = ('新建建筑', '拆除', '改建(建筑→建筑)', '植被↔植被', '地表↔植被', '水体相关', '其他', '无类')
PIECES = OUT / 'exp6/pool/second/pieces.jsonl'
PROMPTS = OUT / 'exp2/second/prompts.jsonl'
RULES = dict(
    categories='Building priority: non5->5 new, 5->non5 demolition, 5->5 rebuilt; '
               'both in {1,3} vegetation (including same-class change); 2<->{1,3} surface/vegetation; '
               'remaining transitions containing 4 water; all remaining GT transitions other.',
    main_table='Unique added TP and remaining FN attributed by each pixel GT transition. '
               'All newly added FP go to 无类. Baseline FP are not newly added FP. '
               'Overlapping units counted once by their union.',
    sensitivity='Assign each accepted whole unit by its most frequent GT (t1,t2) pair, ties lexicographic; '
                'no GT overlap -> 无类. Remove/keep whole units of that class, then reconstruct union '
                'with baseline. Pixels covered by another class remain after removal. '
                'These unit classes differ from pixel classes in the main table; F1 changes are not additive.',
    two_b='Replay exp2_compose box variant: semantic-transition GT objects, best predicted-component '
          'IoU <0.1, prompted objects only; choose highest GT-IoU date in prompt view order, ties first.',
    five_c='Read saved exp6 local piece masks, kind=recall, sources B/G0.05[/H1], area>=10, '
           'recomputed whole-piece GT fraction >=0.5. Check stored geometry/purity, then full-test historical gains.',
    tolerance='0.01 percentage point for gains +7.81/+6.96/+8.50 and absolute F1 against historical JSON. '
              'Integer count differences are recorded (exp2 historically omitted tiny GT on unprompted images).',
    boundary='batch16 boundary = mask minus 3x3 erosion with outside=0; Euclidean tolerance 2; micro counts.',
)


def category(a, b):
    if a not in range(1, 7) or b not in range(1, 7):
        return 6
    if a == 5 and b == 5:
        return 2
    if b == 5:
        return 0
    if a == 5:
        return 1
    if a in (1, 3) and b in (1, 3):
        return 3
    if (a == 2 and b in (1, 3)) or (b == 2 and a in (1, 3)):
        return 4
    if a == 4 or b == 4:
        return 5
    return 6


def pixel_categories(gt, classes):
    c1, c2 = classes
    pair = c1.astype(np.int64)*16 + c2
    group = np.full(gt.shape, 7, np.uint8)
    for value in np.unique(pair[gt]):
        group[gt & (pair == value)] = category(int(value)//16, int(value)%16)
    return group, pair


def put_unit(target, local, box, gt, pair):
    x0, y0, x1, y1 = box
    region = np.s_[y0:y1, x0:x1]
    changed = local & gt[region]
    group = 7
    if changed.any():
        values, counts = np.unique(pair[region][changed], return_counts=True)
        dominant = int(values[np.argmax(counts)])
        group = category(dominant//16, dominant % 16)
    target[group][region] |= local


def reconstruct(job):
    name, piece_spans, prompt_spans = job
    gt = load_label('second', name)
    base = load_pred('second', 'mambascd_base', name)
    groups, pair = pixel_categories(gt, load_classes('second', name))
    additions = {key: np.zeros((len(CATEGORIES), *gt.shape), bool) for key in METHODS}
    stored = load_piece_masks('second', name)
    seen = set()
    for row in read_spans(PIECES, piece_spans):
        if row['piece_id'] in seen:
            raise ValueError(f'{name}: duplicate piece ID {row["piece_id"]}')
        seen.add(row['piece_id'])
        if row['kind'] != 'recall' or row['source'] not in ('B', 'G0.05', 'H1'):
            continue
        box, local = stored[row['piece_id']]
        if box != row['bbox'] or int(local.sum()) != row['area']:
            raise ValueError(f'{name}/{row["piece_id"]}: piece metadata/geometry mismatch')
        x0, y0, x1, y1 = box
        fraction = float(gt[y0:y1, x0:x1][local].mean())
        if abs(fraction-row['gt_frac']) > 1e-6:
            raise ValueError(f'{name}/{row["piece_id"]}: saved GT fraction differs from current labels')
        if int(local.sum()) < 10 or fraction < .5:
            continue
        put_unit(additions['BGH1'], local, box, gt, pair)
        if row['source'] in ('B', 'G0.05'):
            put_unit(additions['BG'], local, box, gt, pair)
    if seen != set(stored):
        raise ValueError(f'{name}: piece manifest and stored masks have different IDs')
    prompts = list(read_spans(PROMPTS, prompt_spans))
    if prompts:
        labels = gt_label('second', name, gt)
        gl, _, objects, _, _ = match(gt, base, labels)
        missed = {o['id'] for o in objects if o['class'] == 'miss'}
        with np.load(OUT / 'exp2/second/sam3' / (Path(name).stem + '.npz')) as z:
            for row in prompts:
                g = row['id']
                if g not in missed:
                    continue
                x0, y0, x1, y1 = row['crop']
                obj = gl == g
                best_iou, best = -1., None
                for view in row['views']:
                    local = z[f'{g}_{view}_box'].astype(bool)
                    inter = int((local & obj[y0:y1, x0:x1]).sum())
                    union = int(local.sum()) + int(obj.sum()) - inter
                    iou = inter / union if union else 0.
                    if iou > best_iou:
                        best_iou, best = iou, local
                if best is None:
                    raise ValueError(f'{name}/{g}: no stored SAM view')
                put_unit(additions['2b'], best, row['crop'], gt, pair)
    return gt, base, groups, additions


def pixel_job(job):
    gt, base, _, additions = reconstruct(job)
    result = {'baseline': metrics.pixel_counts(base, gt)}
    for key, units in additions.items():
        result[key] = metrics.pixel_counts(base | units.any(axis=0), gt)
    return dict(image=job[0], counts=result)


def detailed_job(job):
    gt, base, groups, additions = reconstruct(job)
    baseline = score_counts(base, gt)
    result = dict(image=job[0], baseline=baseline, methods={})
    result['baseline_full'] = metrics.image_metrics(base, gt, before=base)
    result['baseline_full']['image'] = job[0]
    for key, units in additions.items():
        union = units.any(axis=0)
        full = base | union
        extra = union & ~base
        main, sensitivity = {}, {}
        for i, label in enumerate(CATEGORIES):
            own = groups == i
            main[label] = dict(added_tp=int((extra & gt & own).sum()),
                               added_fp=int((extra & ~gt).sum()) if i == 7 else 0,
                               remaining_fn=int((~full & gt & own).sum()),
                               baseline_fn=int((~base & gt & own).sum()),
                               gt_pixels=int((gt & own).sum()))
            without = base | units[np.arange(len(CATEGORIES)) != i].any(axis=0)
            only = base | units[i]
            sensitivity[label] = dict(without=score_counts(without, gt), only=score_counts(only, gt))
        counts = score_counts(full, gt)
        if (sum(v['added_tp'] for v in main.values()) != counts['tp']-baseline['tp'] or
                sum(v['added_fp'] for v in main.values()) != counts['fp']-baseline['fp'] or
                sum(v['remaining_fn'] for v in main.values()) != counts['fn']):
            raise ValueError(f'{job[0]}/{key}: category pixel counts do not sum to totals')
        full_metrics = metrics.image_metrics(full, gt, before=base)
        full_metrics['image'] = job[0]
        result['methods'][key] = dict(counts=counts, main=main, sensitivity=sensitivity,
                                      full_evaluation=full_metrics)
    return result


def references():
    exp2 = read_json(OUT / 'exp2/second/metrics.json')['pixel']
    bg = read_json(OUT / 'exp5c/second/metrics_5c_B_G0.05.json')
    bgh = read_json(OUT / 'exp5c/second/metrics_5c_B_G0.05_H1.json')
    return {'2b': dict(original=exp2['mambascd_base|box|original'], result=exp2['mambascd_base|box|2b']),
            'BG': dict(original=bg['original'], result=bg['oracle_split_direct']),
            'BGH1': dict(original=bgh['original'], result=bgh['oracle_split_direct'])}


def historical_checks(totals, smoke):
    refs, checks = references(), {}
    baseline = metrics.pixel_f1(totals['baseline'])
    for key, expected in METHODS.items():
        value = metrics.pixel_f1(totals[key])
        gain = 100*(value-baseline)
        ref = refs[key]
        errors = dict(gain_pp=abs(gain-expected), f1_pp=abs(100*(value-ref['result']['f1'])),
                      baseline_pp=abs(100*(baseline-ref['original']['f1'])))
        checks[key] = dict(actual_f1=value, actual_delta_f1_pp=gain, expected_delta_f1_pp=expected,
                           reference=ref, errors=errors, status='smoke_not_comparable' if smoke else 'matched',
                           count_difference={k: totals[key][k]-ref['result'][k] for k in ('tp', 'fp', 'fn')})
        if not smoke and any(v > .01 for v in errors.values()):
            raise ValueError(f'{key}: historical check failed before category output: {checks[key]}')
    return checks


def markdown(report):
    lines = ['# exp8 第 6 步：SECOND 补漏类型拆分', '',
             '冒烟子集：未验证全测试集历史数字。' if report['smoke'] else '全测试集历史 F1 与增益核对通过（容差 0.01 个百分点）。', '',
             '主表按真实 GT 像素分类，新增 FP 全部记为“无类”。重叠像素只计一次。',
             '敏感性按单元的主导 GT 转移整块移除/保留；移除后仍被其他类单元覆盖的像素保留。',
             '植被类包括 1→1、3→3；建筑类别优先于水体类别。各类 ΔF1 不可相加。', '',
             '| 方法 | F1 (%) | ΔF1 (百分点) | BF2 (%) |', '| --- | ---: | ---: | ---: |']
    base = report['baseline']['scores']
    lines.append(f'| baseline | {100*base["f1"]:.4f} | 0 | {100*base["bf2"]:.4f} |')
    for key, row in report['methods'].items():
        s = row['scores']
        lines.append(f'| {key} | {100*s["f1"]:.4f} | {100*(s["f1"]-base["f1"]):+.4f} | {100*s["bf2"]:.4f} |')
    for key, row in report['methods'].items():
        lines += ['', f'## {key}', '', '| 类别 | 新增 TP | 新增 FP | 剩余 FN | 原始 FN | 新增 TP 占比 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: |']
        added_tp = sum(v['added_tp'] for v in row['main'].values())
        for label, c in row['main'].items():
            share = c['added_tp']/added_tp if added_tp else 0
            lines.append(f'| {label} | {c["added_tp"]} | {c["added_fp"]} | {c["remaining_fn"]} | {c["baseline_fn"]} | {100*share:.2f}% |')
        lines += ['', '变化列单位为百分点；移除变化相对完整 oracle，仅保留变化相对 baseline。', '',
                  '| 单元类 | 移除 F1 | 移除 BF2 | 移除 ΔF1 | 移除 ΔBF2 | 仅保留 F1 | 仅保留 BF2 | 仅保留 ΔF1 | 仅保留 ΔBF2 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for label, s in row['sensitivity'].items():
            values = [s['without']['scores']['f1'], s['without']['scores']['bf2'],
                      s['without']['delta_f1_vs_full'], s['without']['delta_bf2_vs_full'],
                      s['only']['scores']['f1'], s['only']['scores']['bf2'],
                      s['only']['delta_f1_vs_baseline'], s['only']['delta_bf2_vs_baseline']]
            lines.append('| '+label+' | '+' | '.join(f'{100*v:.4f}' for v in values)+' |')
    lines += ['', '完整口径、历史整数差异、各类 GT 像素数及 A–E 端到端指标见 summary.json；逐图计数见 per_image.jsonl。', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--smoke', action='store_true')
    args = p.parse_args()
    if args.workers < 1:
        p.error('--workers must be positive')
    if (args.out / 'summary.json').exists():
        raise FileExistsError(f'{args.out}: completed output exists; use a new --out')
    started = time.monotonic()
    images = names('second')
    if not args.smoke and len(images) != 1694:
        raise ValueError(f'Expected 1694 SECOND test images, found {len(images)}')
    if args.smoke:
        images = sorted(set(images[:2] + ['test_759.png', 'test_518.png', 'test_900.png']))
    piece_index = index_jsonl(PIECES, set(images))
    prompt_index = index_jsonl(PROMPTS, set(images))
    jobs = [(name, piece_index.get(name, []), prompt_index.get(name, [])) for name in images]
    totals = {key: {} for key in ('baseline', *METHODS)}
    pixel_rows = {}
    for row in parallel_map(pixel_job, jobs, args.workers):
        pixel_rows[row['image']] = row['counts']
        for key, counts in row['counts'].items():
            add_counts(totals[key], counts)
    checks = historical_checks(totals, args.smoke)
    # No class results are written until all three full-test checks have passed.
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / 'pixel_checks.json', dict(smoke=args.smoke, checks=checks, counts=totals))
    report = dict(smoke=args.smoke, images=images, rules=RULES, checks=checks,
                  baseline=dict(counts={}), methods={key: dict(counts={}, main={}, sensitivity={}) for key in METHODS})
    full_records = {key: [] for key in ('baseline', *METHODS)}
    temp = args.out / 'per_image.jsonl.tmp'
    import json
    with temp.open('w', encoding='utf-8') as handle:
        for n, row in enumerate(parallel_map(detailed_job, jobs, args.workers), 1):
            expected = pixel_rows[row['image']]
            for key, c in [('baseline', row['baseline'])] + [(k, r['counts']) for k, r in row['methods'].items()]:
                if any(c[field] != expected[key][field] for field in ('tp', 'fp', 'fn')):
                    raise ValueError(f'{row["image"]}/{key}: reconstruction changed between passes')
            add_counts(report['baseline']['counts'], row['baseline'])
            full_records['baseline'].append(row['baseline_full'])
            for key, values in row['methods'].items():
                dest = report['methods'][key]
                add_counts(dest['counts'], values['counts'])
                full_records[key].append(values['full_evaluation'])
                for label in CATEGORIES:
                    add_counts(dest['main'].setdefault(label, {}), values['main'][label])
                    for setting in ('without', 'only'):
                        add_counts(dest['sensitivity'].setdefault(label, {}).setdefault(setting, {}),
                                   values['sensitivity'][label][setting])
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
            if n % 50 == 0:
                print('step6 sensitivity', n, '/', len(images), flush=True)
    temp.replace(args.out / 'per_image.jsonl')
    report['baseline']['scores'] = scores(report['baseline']['counts'])
    report['baseline']['evaluation'] = metrics.aggregate(full_records['baseline'])
    base = report['baseline']['scores']
    for key, values in report['methods'].items():
        values['scores'] = scores(values['counts'])
        values['evaluation'] = metrics.aggregate(full_records[key])
        full = values['scores']
        for settings in values['sensitivity'].values():
            for setting, counts in list(settings.items()):
                score = scores(counts)
                settings[setting] = dict(counts=counts, scores=score,
                    delta_f1_vs_full=score['f1']-full['f1'], delta_bf2_vs_full=score['bf2']-full['bf2'],
                    delta_f1_vs_baseline=score['f1']-base['f1'], delta_bf2_vs_baseline=score['bf2']-base['bf2'])
    report['seconds'] = time.monotonic()-started
    write_json(args.out / 'summary.json', report)
    (args.out / 'report.md').write_text(markdown(report), encoding='utf-8')


if __name__ == '__main__':
    main()
