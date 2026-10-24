"""第 1b/1c 步：裁开候选并统计两组构成，route → merge → examples。"""
from __future__ import annotations

import argparse
import html
import json
import resource
import time
from pathlib import Path

from exp8s2_cache import (HERE, OUT, EIGHT, np, ndimage, Image, ImageDraw, common,
                         read_json, write_json, local_output, route_api, split_parts)
import exp8s2_step1c as step1c

DATASETS = ('second_cv', 'second', 'sysu_val', 'sysu', 'levir', 'whu')
BASELINE = OUT / 'exp8s2_batch39'
RULES = (.1, .2)
SEED = 20261024
REMAIN_COLOR, DETACHED_COLOR = (255, 140, 0), (180, 70, 255)


def baseline_rows(ds, names):
    wanted, found = set(names), {}
    for path in sorted((BASELINE / ds / 'route').glob('*.jsonl')):
        with path.open() as handle:
            for line in handle:
                row = json.loads(line)
                if row['image'] in wanted:
                    if row['image'] in found:
                        raise ValueError(f'{ds}/{row["image"]}: duplicate baseline row')
                    found[row['image']] = row
        if len(found) == len(wanted):
            break
    if set(found) != wanted:
        raise ValueError(f'{ds}: missing batch39 route records {wanted - set(found)}')
    return found


def candidates(api, ds, name, ctx, with_sources=False):
    img, prob, cell = ctx.image(name)
    chosen = img.candidates(api.s5.COMBOS[api.s5.DEFAULT_COMBO])
    fulls = api.full_masks(ds, name, img, prob, chosen)
    if with_sources:
        metadata = step1c.original_metadata(api, ds, name, img, prob, chosen)
        for full, meta in zip(fulls, metadata):
            full['step1c'] = meta
    dilated = ndimage.binary_dilation(img.base, EIGHT)
    new, audit = [], []
    for origin, full in enumerate(fulls):
        remaining, pieces, record = split_parts(full, img.base, dilated)
        record.update(origin=origin, original_area=full['area'], original_r=api.rk(full, img.base)[0],
                      remaining_r=None if remaining is None else api.rk(remaining, img.base)[0])
        audit.append(record)
        if remaining is not None:
            new.append(dict(remaining, origin=origin, detached=False))
        new.extend(dict(p, origin=origin, detached=True) for p in pieces)
    return img, cell, fulls, new, audit


def run_route(args, ds):
    api = route_api()
    items = api.s6.items(ds, args)
    old = baseline_rows(ds, [name for _, name in items]) if items else {}
    ctx = api.Context(ds)
    reference = step1c.baseline_reference(api, ds)
    dest = args.out / ds / 'route' / f'{args.shard:03d}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    with tmp.open('w') as handle:
        for index, name in items:
            start = time.perf_counter()
            img, cell, fulls, new, audit = candidates(api, ds, name, ctx, with_sources=True)
            labels, comps = api.missed_components(ds, name, img.gt, img.base, cell)
            before = [[round(r, 6), k] + o['bbox'] for o in fulls for r, k in [api.rk(o, img.base)]]
            oldset = old[name]['sets']['d1all']
            if comps != old[name]['comps'] or before != oldset['cand']:
                raise ValueError(f'{ds}/{name}: components or reconstructed candidates differ from batch39')
            ob, op = api.best_candidates(labels, len(comps), fulls)
            if ob.tolist() != oldset['best'] or op.tolist() != oldset['best_px']:
                raise ValueError(f'{ds}/{name}: original best candidates differ from batch39')
            best, best_px = api.best_candidates(labels, len(comps), new)
            cand = [dict(r=r, k=k, bbox=o['bbox'], origin=o['origin'], detached=o['detached'])
                    for o in new for r, k in [api.rk(o, img.base)]]
            extra = step1c.image_statistics(api, img, cell, labels, comps, fulls, new, cand, reference, RULES)
            row = dict(index=index, image=name, comps=comps, originals=audit,
                       sets=dict(d1all=dict(cand=cand, best=best.tolist(), best_px=best_px.tolist())),
                       step1c=extra,
                       baseline=oldset, seconds=time.perf_counter()-start,
                       peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            print(json.dumps(dict(dataset=ds, image=name, candidates_before=len(fulls), candidates_after=len(new),
                                  detached=sum(a['detached_count'] for a in audit), seconds=round(row['seconds'], 3),
                                  peak_rss_mib=round(row['peak_rss_mib'], 1)), ensure_ascii=False), flush=True)
            if ds == 'second':
                largest = max(audit, key=lambda a: a['original_area'], default=None)
                if largest:
                    print('SECOND 最大候选裁开记录：' + json.dumps(dict(image=name, **largest), ensure_ascii=False), flush=True)
    tmp.replace(dest)


def load_rows(args, ds):
    api = route_api()
    rows = []
    for path in sorted((args.out / ds / 'route').glob('*.jsonl')):
        with path.open() as handle:
            rows.extend(json.loads(line) for line in handle)
    rows.sort(key=lambda r: r['index'])
    names = api.s34.image_list(ds)[:args.limit or None]
    if [(r['index'], r['image']) for r in rows] != list(enumerate(names)):
        raise ValueError(f'{ds}: route image inventory mismatch; use a separate smoke directory')
    return rows


def summarize(rows, baseline=False):
    api = route_api()
    totals = {g: dict(comps=0, px=0) for g in api.GROUPS}
    for row in rows:
        for c in row['comps']:
            totals[c['group']]['comps'] += 1
            totals[c['group']]['px'] += c['px']
    result = dict(images=len(rows), components=totals, rules={})
    for tau in RULES:
        table = {g: {d: [0, 0] for d in api.ROUTES} for g in api.GROUPS}
        fills, counts = [], []
        for row in rows:
            s = row['baseline'] if baseline else row['sets']['d1all']
            fill = [(c[0] if baseline else c['r']) < tau for c in s['cand']]
            fills.append(sum(fill))
            counts.append(len(fill))
            for comp, b in zip(row['comps'], s['best']):
                dest = 'none' if b < 0 else 'fill' if fill[b] else 'refine'
                table[comp['group']][dest][0] += 1
                table[comp['group']][dest][1] += comp['px']
        result['rules'][api.rule_name(tau, None)] = dict(table=table, fill_per_image=float(np.mean(fills)),
                                                        refine_per_image=float(np.mean(np.subtract(counts, fills))))
    result['candidates_per_image'] = float(np.mean(counts))
    return result


def percentage(table, group, i):
    total = sum(v[i] for v in table[group].values())
    return 100 * table[group]['fill'][i] / total if total else None


def fmt(value, signed=False):
    return '—' if value is None else format(value, '+.3f' if signed else '.3f')


def run_merge(args, ds):
    api = route_api()
    rows = load_rows(args, ds)
    current = summarize(rows)
    saved = read_json(BASELINE / ds / 'summary.json')
    if args.limit:
        baseline = summarize(rows, baseline=True)
        reference = 'batch39 相同图的 route 记录；全量 summary 仅作为来源记录'
    else:
        baseline = dict(saved['sets']['d1all'], images=saved['images'], components=saved['components'])
        reference = 'batch39 summary.json'
    if current['components'] != baseline['components'] or current['images'] != baseline['images']:
        raise ValueError(f'{ds}: component/cell totals differ from step 1')
    originals = [a for row in rows for a in row['originals']]
    split_count = sum(a['detached_count'] > 0 for a in originals)
    split_stats = dict(original_candidates=len(originals), split_original_candidates=split_count,
                       split_original_fraction=split_count/len(originals) if originals else 0,
                       detached_per_image=sum(a['detached_count'] for a in originals)/len(rows),
                       empty_originals=sum(a['remaining_empty'] for a in originals),
                       dropped_components=sum(a['dropped_components'] for a in originals),
                       dropped_px=sum(a['dropped_px'] for a in originals))
    summary = dict(dataset=ds, partial=bool(args.limit), images=len(rows), components=current['components'],
                   component_total=sum(v['comps'] for v in current['components'].values()),
                   sets=dict(d1all={k: v for k, v in current.items() if k not in ('images', 'components')}),
                   baseline=baseline, baseline_source=str(BASELINE / ds / 'summary.json'),
                   comparison_scope=reference, checks=dict(component_totals_match=True, cell_totals_match=True),
                   split=split_stats, changes_over_2pp=[], cell3b_misrouting={},
                   timing=dict(mean_image_seconds=float(np.mean([r['seconds'] for r in rows])),
                               max_image_seconds=max(r['seconds'] for r in rows),
                               peak_rss_mib=max(r['peak_rss_mib'] for r in rows)))
    lines = [f'# {api.SPLIT[ds]}：第 1b 步与第 1 步比较', '',
             f'范围：{"前两图级冒烟，不能作为全量结论" if args.limit else "完整数据集"}，共 {len(rows)} 图。'
             f'基准：{reference}。候选集 d1all；不再去重。', '',
             '比例分母为该格全部漏检连通域数或像素（含未覆盖）；像素口径计连通域完整像素数。', '',
             '## 表 1a：各格去补漏比例（%）', '',
             '| 规则 | 格 | 口径 | 第 1 步 | 裁开后 | 差值（百分点） |', '|---|---|---|---:|---:|---:|']
    for rule, result in current['rules'].items():
        before = baseline['rules'][rule]
        result['fill_per_image_change'] = result['fill_per_image'] - before['fill_per_image']
        result['refine_per_image_change'] = result['refine_per_image'] - before['refine_per_image']
        for g in api.GROUPS:
            for i, unit in enumerate(('连通域数', '像素')):
                a, b = percentage(before['table'], g, i), percentage(result['table'], g, i)
                delta = None if a is None else b-a
                lines.append(f'| {rule} | {api.GROUP_NAMES[g]} | {unit} | {fmt(a)} | {fmt(b)} | {fmt(delta, True)} |')
                if delta is not None and abs(delta) > 2:
                    summary['changes_over_2pp'].append(dict(rule=rule, group=g, measure=unit,
                                                           before_pct=a, after_pct=b, delta_pp=delta))
        summary['cell3b_misrouting'][rule] = {unit: dict(percent=percentage(result['table'], '3b', i),
                    at_most_3pct=None if percentage(result['table'], '3b', i) is None else percentage(result['table'], '3b', i) <= 3)
                    for i, unit in enumerate(('components', 'pixels'))}
    lines += ['', '## 表 2：格 × 去向', '', '| 规则 | 格 | 去向 | 第 1 步连通域 | 裁开后连通域 | 第 1 步像素 | 裁开后像素 |',
              '|---|---|---|---:|---:|---:|---:|']
    for rule, result in current['rules'].items():
        for g in api.GROUPS:
            for d in api.ROUTES:
                a, b = baseline['rules'][rule]['table'][g][d], result['table'][g][d]
                lines.append(f'| {rule} | {api.GROUP_NAMES[g]} | {api.ROUTE_NAMES[d]} | {a[0]} | {b[0]} | {a[1]} | {b[1]} |')
    lines += ['', '## 每图候选及拆分', '', '| 规则 | 去向 | 第 1 步每图 | 裁开后每图 | 变化 |', '|---|---|---:|---:|---:|']
    for rule, result in current['rules'].items():
        for d in ('fill', 'refine'):
            key = d + '_per_image'
            lines.append(f'| {rule} | {api.ROUTE_NAMES[d]} | {baseline["rules"][rule][key]:.3f} | {result[key]:.3f} | {result[key+"_change"]:+.3f} |')
    lines += ['', f'每图候选总数：{baseline["candidates_per_image"]:.3f} → {current["candidates_per_image"]:.3f}。', '',
              f'被拆原候选：{split_count}/{len(originals)}（{100*split_stats["split_original_fraction"]:.3f}%）；'
              f'每图独立候选 {split_stats["detached_per_image"]:.3f}；c′ 为空 {split_stats["empty_originals"]} 个；'
              f'丢弃小块 {split_stats["dropped_components"]} 个 / {split_stats["dropped_px"]} 像素。', '',
              '“被拆”指至少产生一个 ≥20 像素独立候选；仅丢弃小块的原候选不计入被拆比例。', '',
              '## 去补漏变化超过 2 个百分点的格', '']
    lines += [f'- {v["rule"]} {api.GROUP_NAMES[v["group"]]}（{v["measure"]}）：'
              f'{v["before_pct"]:.3f}% → {v["after_pct"]:.3f}%（{v["delta_pp"]:+.3f} 个百分点）。'
              for v in summary['changes_over_2pp']] or ['无。']
    lines += ['', '## ③ ≥30% 误分到补漏', '']
    for rule, measures in summary['cell3b_misrouting'].items():
        for unit, v in measures.items():
            status = '无该格样本' if v['percent'] is None else '≤3%' if v['at_most_3pct'] else '>3%'
            lines.append(f'- {rule}，{"连通域数" if unit == "components" else "像素"}：{fmt(v["percent"])}%，{status}。')
    lines += ['', f'核对通过：漏检连通域共 {summary["component_total"]} 个，各格连通域数和像素数与相同范围第 1 步一致。']
    summary['step1c'] = step1c.summarize(api, ds, rows, bool(args.limit), RULES)
    lines += step1c.tables(summary['step1c'])
    write_json(args.out / ds / 'summary.json', summary)
    (args.out / ds / 'tables.md').write_text('\n'.join(lines) + '\n')
    print(f'{ds}: merge passed; {len(rows)} images, {summary["component_total"]} components', flush=True)


def render_example(api, entry, dest):
    """先原样调用 batch39 六栏 render，再追加修正后的局部和整图两栏。"""
    name = entry['image']
    crop = api.render('second', name, entry['component'], entry['before']['cand'], dest)
    img, cell, _, new, _ = candidates(api, 'second', name, api.Context('second'))
    selected = new[entry['after']['cand']]
    if selected['origin'] != entry['after']['origin']:
        raise ValueError('example candidate origin mismatch')
    remaining, detached = np.zeros_like(img.base), np.zeros_like(img.base)
    for obj in new:
        if obj['origin'] == selected['origin']:
            x0, y0, x1, y1 = obj['bbox']
            canvas = detached if obj['detached'] else remaining
            canvas[y0:y1, x0:x1] |= obj['mask']
    t2 = common.load_rgb('second', name, 't2')
    overlay = api.b38.blend(t2, img.base, api.b38.COLORS['base'], .45)
    labels, _ = api.missed_components('second', name, img.gt, img.base, cell)
    component = labels == labels[tuple(entry['component']['seed'])]
    overlay = api.b38.blend(overlay, component, api.b38.COLORS['comp'], .8)
    for mask, color in ((remaining, REMAIN_COLOR), (detached, DETACHED_COLOR)):
        overlay[mask & ~ndimage.binary_erosion(mask, EIGHT)] = color
    y0, x0, side, _ = crop
    S, gap = api.b38.SIDE, 4
    panels = [Image.fromarray(overlay[y0:y0+side, x0:x0+side]).resize((S, S), Image.Resampling.NEAREST),
              Image.fromarray(overlay).resize((S, S), Image.Resampling.BILINEAR)]
    scale = S / img.base.shape[0]
    ImageDraw.Draw(panels[1]).rectangle([x0*scale, y0*scale, (x0+side)*scale-1, (y0+side)*scale-1],
                                       outline=api.b38.COLORS['box'], width=2)
    with Image.open(dest) as original:
        output = Image.new('RGB', (8*S+7*gap, S+38), 'white')
        output.paste(original, (0, 38))
    for i, panel in enumerate(panels, 6):
        output.paste(panel, (i*(S+gap), 38))
    a, b = entry['before'], entry['after']
    ImageDraw.Draw(output).text((5, 4), f'{name} | before: r={a["r"]:.6f}, k={a["k"]}, {a["route"]} | '
                                f'after: r={b["r"]:.6f}, k={b["k"]}, {b["route"]} | origin={b["origin"]}', fill='black')
    ImageDraw.Draw(output).text((5, 20), "green: original winner | orange: remaining | purple: detached | red: missed component", fill='black')
    output.save(dest)
    return crop


def run_examples(args):
    api = route_api()
    rows = load_rows(args, 'second')
    pools = dict(changed=[], still_refine=[])
    for row in rows:
        old, new = row['baseline'], row['sets']['d1all']
        for i, comp in enumerate(row['comps']):
            if comp['group'] != '2':
                continue
            a, b = old['best'][i], new['best'][i]
            if b < 0:
                continue
            after = dict(new['cand'][b], cand=b, best_px=new['best_px'][i],
                         route='fill' if new['cand'][b]['r'] < .1 else 'refine')
            before = dict(cand=a, r=old['cand'][a][0], k=old['cand'][a][1], best_px=old['best_px'][i],
                          route='fill' if old['cand'][a][0] < .1 else 'refine') if a >= 0 else dict(cand=-1, route='none')
            group = 'changed' if before['route'] == 'refine' and after['route'] == 'fill' else (
                'still_refine' if after['route'] == 'refine' else None)
            if group:
                pools[group].append(dict(image=row['image'], component=comp, before=before, after=after))
    dest = args.out / 'examples'
    dest.mkdir(parents=True, exist_ok=True)
    report = dict(seed=SEED, partial=bool(args.limit), rule='R(0.1)', groups={})
    body = []
    rng = np.random.default_rng(SEED)
    titles = dict(changed='第 1 步②→精修，裁开后②→补漏', still_refine='裁开后仍②→精修')
    for group, pool in pools.items():
        chosen = [pool[int(i)] for i in sorted(rng.choice(len(pool), min(8, len(pool)), replace=False))]
        report['groups'][group] = dict(available=len(pool), requested=8, selected=len(chosen),
                                      shortage=max(0, 8-len(pool)), samples=chosen)
        body.append(f'<h2>{titles[group]}</h2><p>可选 {len(pool)} 个连通域，抽取 {len(chosen)} 个。'
                    + ('不足 8 个，展示全部可用例。' if len(pool) < 8 else '') + '</p>')
        for i, entry in enumerate(chosen):
            entry['file'] = f'{group}_{i}.png'
            entry['crop'] = render_example(api, entry, dest / entry['file'])
            a, b = entry['before'], entry['after']
            body.append(f'<div class="ex"><div class="meta"><b>{html.escape(entry["image"])}</b>'
                        f'第 1 步 r={a["r"]:.6f}, k={a["k"]}, {api.ROUTE_NAMES[a["route"]]}；'
                        f'修正后 r={b["r"]:.6f}, k={b["k"]}, {api.ROUTE_NAMES[b["route"]]}；'
                        f'新候选 {b["cand"]}，原候选 {b["origin"]}，独立候选：{b["detached"]}；'
                        f'覆盖连通域 {a["best_px"]} → {b["best_px"]} 像素。</div>'
                        '<div class="hdr">' + ''.join(f'<div>{t}</div>' for t in
                        ('T1', 'T2', '真值', '检测器预测', '原最佳候选', '原整图', '修正后候选', '修正后整图')) + '</div>'
                        f'<img loading="lazy" src="{entry["file"]}" alt="{titles[group]}"></div>')
    legend = ''.join(f'<span class="sw" style="background:rgb{color}"></span>{label}' for color, label in
                     ((api.CAND_COLOR, '第 1 步最佳候选'), (REMAIN_COLOR, 'c′ 剩余候选'),
                      (DETACHED_COLOR, '拆出的独立候选'), (api.b38.COLORS['comp'], '该漏检连通域'),
                      (api.b38.COLORS['base'], '检测器预测 D')))
    page = ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>第 1b 步 SECOND 审查页</title><style>' + api.b38.STYLE +
            '.hdr{grid-template-columns:repeat(8,1fr);max-width:none}img{max-width:none}</style><body>'
            '<h1>SECOND 测试：候选裁开后的②分流</h1>'
            f'<p>种子 {SEED}，按漏检连通域无放回抽样，两组各 8 例。'
            + ('本页为前两图级冒烟。' if args.limit else '') +
            'D 在整图上膨胀 1 像素；不贴 D⁺ 且至少 20 像素的块独立去补漏。R(0.1)：r&lt;0.1 去补漏。'
            '左六栏沿用第 1 步版式；右两栏画出新最佳候选所属原候选的 c′ 及全部独立块。'
            '新旧最佳候选的原始来源可能不同，序号和覆盖像素见每例说明。裁块范围与原审查页一致。</p><p>'
            + legend + '</p>' + ''.join(body) + '</body></html>')
    (dest / 'index.html').write_text(page)
    write_json(dest / 'samples.json', report)
    print(json.dumps({g: dict(available=len(p), selected=min(8, len(p))) for g, p in pools.items()}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('route', 'merge', 'examples'))
    parser.add_argument('--dataset', choices=DATASETS)
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out = local_output(args.out)
    if args.limit < 0 or args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error('invalid limit/shard/shards')
    if args.stage != 'route' and (args.shards != 1 or args.shard != 0):
        parser.error('merge/examples 读取全部分片，不接受分片参数')
    if args.stage != 'examples' and args.dataset is None:
        parser.error('route/merge 必须给 --dataset')
    if args.stage == 'examples':
        if args.dataset not in (None, 'second'):
            parser.error('examples 仅用于 SECOND 测试')
        run_examples(args)
    else:
        {'route': run_route, 'merge': run_merge}[args.stage](args, args.dataset)


if __name__ == '__main__':
    main()
