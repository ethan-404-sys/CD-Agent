"""第 1d 步：只读缓存，比较 32 个分流组合并按验证集冻结规则。"""
from __future__ import annotations

import argparse
import html
import json
import resource
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'batch41'))
from exp8s2_cache import (np, ndimage, EIGHT, Image, ImageDraw, common, OUT,
                         read_json, write_json, route_api, split_parts)
import exp8s2_split as step1b

DATASETS = ('second_cv', 'second', 'sysu_val', 'sysu', 'levir', 'whu')
SOURCES = ('B', 'G', 'H1', 'C')
SEED = 20261024
VERSION = 1
BASE41 = HERE.parent / 'batch41/route_run'


def configurations():
    return [dict(method=m, tau=t, A_max=a, prefilter_novel_lt=p)
            for m in ('base', 'a', 'b', 'ab')
            for a in ((5000, 20000, 50000) if m in ('b', 'ab') else (None,))
            for t in (.1, .2) for p in (None, 100)]


def rule_id(c):
    return f'{c["method"]}_A{c["A_max"] or 0}_t{c["tau"]:g}_n{c["prefilter_novel_lt"] or 0}'


CONFIGS = {rule_id(c): c for c in configurations()}


def local_output(value):
    path = Path(value).resolve()
    if path == HERE or not path.is_relative_to(HERE):
        raise ValueError('--out 必须是 batch42 内的专用子目录')
    return path


def ratio(n, d):
    return 100 * n / d if d else None


def fmt(x):
    return '—' if x is None else f'{x:.3f}'


def baseline41(ds, names):
    wanted, found = set(names), {}
    for path in sorted((BASE41 / ds / 'route').glob('*.jsonl')):
        with path.open() as f:
            for line in f:
                row = json.loads(line)
                if row['image'] in wanted:
                    if row['image'] in found:
                        raise ValueError(f'{ds}: duplicate batch41 image {row["image"]}')
                    found[row['image']] = row
        if len(found) == len(wanted):
            break
    if set(found) != wanted:
        raise ValueError(f'{ds}: 缺少 batch41 同图基线 {wanted-set(found)}')
    return found


def sam_partition(ds, name, shape):
    # 与 batch18/19 automatic_path 和 U5 split_objects 使用的 labels 相同。
    root = (OUT / 'exp7_batch16/automatic' if ds.startswith('second') else
            OUT / ('exp8_batch19/automatic' if ds == 'sysu_val' else 'exp8_batch18/automatic'))
    path = root / ds / (Path(name).stem + '.npz')
    audit = dict(path=str(path), available=path.is_file())
    if not audit['available']:
        audit['reason'] = '无该图 SAM 自动划分缓存；超限成员保持完整，只按成员拆分'
        return None, audit
    with np.load(path, allow_pickle=False) as z:
        labels = z['labels']
    if labels.shape != shape:
        raise ValueError(f'{path}: SAM 划分尺寸不符')
    return labels, audit


def crop_local(mask, bbox):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    a, b, c, d = int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)
    return dict(bbox=[bbox[0]+a, bbox[1]+b, bbox[0]+c, bbox[1]+d],
                mask=mask[b:d, a:c].copy(), area=int(len(xs)))


def annotate(obj, base, gt):
    x0, y0, x1, y1 = obj['bbox']
    novel = obj['mask'] & ~base[y0:y1, x0:x1]
    n = int(novel.sum())
    return dict(obj, r=(obj['area']-n)/obj['area'], novel_px=n,
                true_px=int((novel & gt[y0:y1, x0:x1]).sum()))


def prepare(api, ds, name, ctx):
    img, prob, cell = ctx.image(name)
    chosen = img.candidates(api.s5.COMBOS[api.s5.DEFAULT_COMBO])
    fulls = api.full_masks(ds, name, img, prob, chosen)
    needed = sorted({(s, lv) for c in chosen for s, lv, _ in c['orig']}, key=str)
    raw = api.raw_rows(ds, name, prob, needed)
    members, member_keys = [], {}
    by_origin = []
    for origin, (full, c) in enumerate(zip(fulls, chosen)):
        ids = []
        for s, lv, i in c['orig']:
            key = (s, lv, i)
            if key not in member_keys:
                member_keys[key] = len(members)
                members.append(dict(raw[(s, lv)][i], source=s, sources=[s],
                                    origins=[origin], members=[list(key)], split=False))
            else:
                raise ValueError('D1 成员重复归属')
            ids.append(member_keys[key])
        by_origin.append(ids)
        full.update(origins=[origin], members=[list(k) for k in c['orig']],
                    source=c['source'], sources=c['sources'], split=False)
    members = [annotate(o, img.base, img.gt) for o in members]
    fulls = [annotate(o, img.base, img.gt) for o in fulls]
    dilated = ndimage.binary_dilation(img.base, EIGHT)
    base_by_origin = []
    for full in fulls:
        remain, detached, _ = split_parts(full, img.base, dilated)
        meta = {k: full[k] for k in ('origins', 'members', 'source', 'sources')}
        objs = ([dict(remain, **meta, split=False)] if remain else [])
        objs += [dict(p, **meta, split=True) for p in detached]
        base_by_origin.append([annotate(o, img.base, img.gt) for o in objs])
    labels, comps = api.missed_components(ds, name, img.gt, img.base, cell)
    sam, sam_audit = sam_partition(ds, name, img.base.shape)
    return dict(img=img, cell=cell, fulls=fulls, members=members, by_origin=by_origin,
                base_by_origin=base_by_origin, labels=labels, comps=comps, sam=sam,
                sam_audit=sam_audit, pieces={})


def split_member(data, ident, maximum):
    key = (ident, maximum)
    if key in data['pieces']:
        return data['pieces'][key]
    obj = data['members'][ident]
    audit = dict(large_members=0, unsplit_without_sam=0, sam_blocks=0,
                 discarded_small_px=0, unlabeled_px=0, blocks_still_large=0)
    if obj['area'] <= maximum:
        result = [obj]
    elif data['sam'] is None:
        audit.update(large_members=1, unsplit_without_sam=1)
        result = [obj]
    else:
        audit['large_members'] = 1
        x0, y0, x1, y1 = obj['bbox']
        labels = data['sam'][y0:y1, x0:x1]
        meta = {k: obj[k] for k in ('origins', 'members', 'source', 'sources')}
        result = []
        # U5 对每个正标签求交，不再把同标签交集按连通域拆开。
        for label in np.unique(labels[obj['mask']]):
            mask = obj['mask'] & (labels == label)
            area = int(mask.sum())
            if label == 0:
                audit['unlabeled_px'] += area
            elif area < 20:
                audit['discarded_small_px'] += area
            else:
                part = dict(crop_local(mask, obj['bbox']), **meta, split=True, sam_label=int(label))
                result.append(annotate(part, data['img'].base, data['img'].gt))
        audit['sam_blocks'] = len(result)
        audit['blocks_still_large'] = sum(p['area'] > maximum for p in result)
        if sum(p['area'] for p in result) + audit['discarded_small_px'] + audit['unlabeled_px'] != obj['area']:
            raise ValueError('SAM 拆块像素计数不守恒')
    data['pieces'][key] = result, audit
    return result, audit


def d1_group(api, objs, base, gt):
    """沿用 exp8s 在新增掩码上匹配的两轮 D1；返回完整掩码并集。"""
    def merge_round(items, internal):
        proxies = []
        for o in items:
            x0, y0, x1, y1 = o['bbox']
            novel = o['mask'] & ~base[y0:y1, x0:x1]
            proxies.append(dict(o, mask=novel, area=int(novel.sum())))
        result = []
        for ids in api.s34.direct_groups(proxies, .5, internal=internal):
            if len(ids) == 1:
                result.append(items[ids[0]])
                continue
            selected = [items[i] for i in ids]
            sources = sorted({s for o in selected for s in o['sources']})
            full = dict(api.s6.union(selected), sources=sources, source='+'.join(sources),
                        origins=sorted({n for o in selected for n in o['origins']}),
                        members=[m for o in selected for m in o['members']],
                        split=any(o['split'] for o in selected))
            result.append(annotate(full, base, gt))
        return result
    internal = []
    for source in SOURCES:
        same = sorted((o for o in objs if o['source'] == source),
                      key=lambda o: (o['members'][0][2], o.get('sam_label', -1)))
        internal.extend(merge_round(same, True))
    return merge_round(internal, False)


def build_candidates(api, data, config):
    m, tau, maximum = config['method'], config['tau'], config['A_max']
    audit = dict(large_members=0, unsplit_without_sam=0, sam_blocks=0,
                 discarded_small_px=0, unlabeled_px=0, blocks_still_large=0)
    def pieces(ident):
        parts, counts = split_member(data, ident, maximum)
        for key, v in counts.items():
            audit[key] += v
        return parts
    if m == 'base':
        objs = [o for parts in data['base_by_origin'] for o in parts]
    elif m == 'b':
        objs = []
        for i, full in enumerate(data['fulls']):
            if full['area'] <= maximum:
                objs.extend(data['base_by_origin'][i])
            else:
                for ident in data['by_origin'][i]:
                    objs.extend(pieces(ident))
    else:
        objs = data['members'] if m == 'a' else [o for i in range(len(data['members'])) for o in pieces(i)]
    grouped = [dict(o, route='fill' if o['r'] < tau else 'refine') for o in objs]
    if m in ('a', 'ab'):
        grouped = [dict(o, route=dest) for dest in ('fill', 'refine')
                   for o in d1_group(api, [o for o in grouped if o['route'] == dest],
                                     data['img'].base, data['img'].gt)]
    # 去向继承成员/块分组，合并后的 r 仅用于展示，不二次分流。
    threshold = config['prefilter_novel_lt']
    kept = [o for o in grouped if not (threshold and o['route'] == 'fill' and o['novel_px'] < threshold)]
    audit['prefilter_removed'] = len(grouped)-len(kept)
    return kept, audit


def empty_table(api):
    return {g: {d: [0, 0] for d in api.ROUTES} for g in api.GROUPS}


def image_statistics(api, data, objs, audit):
    best, best_px = api.best_candidates(data['labels'], len(data['comps']), objs)
    destinations = ['none' if b < 0 else objs[b]['route'] for b in best]
    table = empty_table(api)
    for c, dest in zip(data['comps'], destinations):
        table[c['group']][dest][0] += 1
        table[c['group']][dest][1] += c['px']
    fill = np.zeros_like(data['img'].base)
    fill_count = zero = 0
    for o in objs:
        if o['route'] == 'fill':
            fill_count += 1
            zero += o['true_px'] == 0
            x0, y0, x1, y1 = o['bbox']
            fill[y0:y1, x0:x1] |= o['mask']
    covered = np.bincount(data['cell'][fill], minlength=6)
    return dict(table=table, covered12_px=int(covered[1]+covered[2]), covered2_px=int(covered[2]),
                fill_count=fill_count, refine_count=len(objs)-fill_count, zero_true_count=zero,
                best=best.tolist(), best_px=best_px.tolist(), destinations=destinations, audit=audit)


def check_baseline(api, data, saved, old):
    if data['comps'] != saved['comps'] or data['comps'] != old['comps']:
        raise ValueError('漏检连通域与 batch39/41 不一致')
    before = [[round(r, 6), k]+o['bbox'] for o in data['fulls'] for r, k in [api.rk(o, data['img'].base)]]
    best, px = api.best_candidates(data['labels'], len(data['comps']), data['fulls'])
    if (before != old['sets']['d1all']['cand'] or best.tolist() != old['sets']['d1all']['best']
            or px.tolist() != old['sets']['d1all']['best_px']):
        raise ValueError('完整候选与 batch39 不一致')
    objs, _ = build_candidates(api, data, dict(method='base', tau=.1, A_max=None, prefilter_novel_lt=None))
    baseline = saved['sets']['d1all']
    if len(objs) != len(baseline['cand']):
        raise ValueError('base 候选数与 batch41 不一致')
    for o, ref in zip(objs, baseline['cand']):
        r, k = api.rk(o, data['img'].base)
        if (r != ref['r'] or k != ref['k'] or o['bbox'] != ref['bbox']
                or o['origins'] != [ref['origin']] or o['split'] != ref['detached']):
            raise ValueError('base 候选与 batch41 不一致')
        if (o['novel_px'] != ref['step1c']['novel_px'] or o['true_px'] != ref['step1c']['true_px']):
            raise ValueError('base 新增/真值像素与 batch41 不一致')
    best, px = api.best_candidates(data['labels'], len(data['comps']), objs)
    if best.tolist() != baseline['best'] or px.tolist() != baseline['best_px']:
        raise ValueError('base 最佳候选与 batch41 不一致')


def run_route(args, ds):
    api = route_api()
    items = api.s6.items(ds, args)
    names = [name for _, name in items]
    saved = baseline41(ds, names) if names else {}
    old = step1b.baseline_rows(ds, names) if names else {}
    ctx = api.Context(ds)
    dest = args.out / ds / 'route' / f'{args.shard:03d}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    with tmp.open('w') as f:
        for index, name in items:
            start = time.perf_counter()
            data = prepare(api, ds, name, ctx)
            check_baseline(api, data, saved[name], old[name])
            results = {}
            # 同一组掩码只构造一次；过滤只改变补漏组，最佳候选在过滤后重算。
            for config in configurations()[::2]:
                objs, audit = build_candidates(api, data, config)
                results[rule_id(config)] = image_statistics(api, data, objs, audit)
                filtered = dict(config, prefilter_novel_lt=100)
                kept = [o for o in objs if o['route'] != 'fill' or o['novel_px'] >= 100]
                results[rule_id(filtered)] = image_statistics(api, data, kept,
                    dict(audit, prefilter_removed=len(objs)-len(kept)))
            for tau in (.1, .2):
                rule = api.rule_name(tau, None)
                result = results[rule_id(dict(method='base', tau=tau, A_max=None, prefilter_novel_lt=None))]
                if result['covered12_px'] != saved[name]['step1c']['rules'][rule]['coverage_before_px']:
                    raise ValueError('base 补漏并集覆盖与 batch41 不一致')
            row = dict(version=VERSION, index=index, image=name, comps=data['comps'], rules=results,
                       sam=data['sam_audit'], checks=dict(batch39=True, batch41=True),
                       seconds=time.perf_counter()-start,
                       peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
            print(json.dumps(dict(dataset=ds, image=name, seconds=round(row['seconds'], 3),
                       peak_rss_mib=round(row['peak_rss_mib'], 1), sam=row['sam']['available']), ensure_ascii=False), flush=True)
    tmp.replace(dest)
    if 'torch' in sys.modules:
        raise RuntimeError('缓存流程意外导入 torch')


def iter_rows(out, ds):
    for path in sorted((out / ds / 'route').glob('*.jsonl')):
        with path.open() as f:
            for line in f:
                row = json.loads(line)
                if row['version'] != VERSION or set(row['rules']) != set(CONFIGS):
                    raise ValueError(f'{path}: 分片版本或方案集合不符')
                yield row


def detail_tables(api, key, result):
    lines = [f'### {key}', '', '表 1a：各格去补漏比例（分母含未覆盖；像素计整个连通域）', '',
             '| 格 | 连通域 % | 像素 % |', '|---|---:|---:|']
    for g in api.GROUPS:
        t = result['table'][g]
        lines.append(f'| {api.GROUP_NAMES[g]} | {fmt(ratio(t["fill"][0], sum(v[0] for v in t.values())))} | '
                     f'{fmt(ratio(t["fill"][1], sum(v[1] for v in t.values())))} |')
    lines += ['', '表 2：格 × 去向', '', '| 格 | 去向 | 连通域数 | 漏检像素数 |', '|---|---|---:|---:|']
    for g in api.GROUPS:
        for d in api.ROUTES:
            c, p = result['table'][g][d]
            lines.append(f'| {api.GROUP_NAMES[g]} | {api.ROUTE_NAMES[d]} | {c} | {p} |')
    return lines+['']


SUMMARY_HEADER = ['| 数据集 | 方案 | ①+②覆盖 % | ②覆盖 % | ③≥30%误分连通域 % | 误分像素 % | 补漏/图 | 精修/图 | 补漏无真值 % |',
                  '|---|---|---:|---:|---:|---:|---:|---:|---:|']


def summary_line(ds, key, r):
    values = [r[k] for k in ('coverage12_pct', 'coverage2_pct', 'misroute3b_components_pct',
              'misroute3b_pixels_pct', 'fill_per_image', 'refine_per_image', 'zero_true_pct')]
    return f'| {ds} | {key} | '+' | '.join(map(fmt, values))+' |'


def run_merge(args, ds):
    api = route_api()
    names = api.s34.image_list(ds)[:args.limit or None]
    seen, times, memory, missing_sam = {}, [], [], []
    totals = {g: dict(comps=0, px=0) for g in api.GROUPS}
    results = {key: dict(config=c, table=empty_table(api), covered12_px=0, covered2_px=0,
                         fill_count=0, refine_count=0, zero_true_count=0, audit={}) for key, c in CONFIGS.items()}
    for row in iter_rows(args.out, ds):
        if row['index'] in seen:
            raise ValueError(f'{ds}: 重复图 {row["image"]}')
        seen[row['index']] = row['image']
        if not all(row['checks'].values()):
            raise ValueError('分片基线核对未通过')
        times.append(row['seconds'])
        memory.append(row['peak_rss_mib'])
        if not row['sam']['available']:
            missing_sam.append(row['image'])
        for c in row['comps']:
            totals[c['group']]['comps'] += 1
            totals[c['group']]['px'] += c['px']
        for key, r in row['rules'].items():
            s = results[key]
            for metric in ('covered12_px', 'covered2_px', 'fill_count', 'refine_count', 'zero_true_count'):
                s[metric] += r[metric]
            for g in api.GROUPS:
                for d in api.ROUTES:
                    for i in (0, 1):
                        s['table'][g][d][i] += r['table'][g][d][i]
            for k, v in r['audit'].items():
                s['audit'][k] = s['audit'].get(k, 0)+v
    if sorted(seen.items()) != list(enumerate(names)) or not names:
        raise ValueError(f'{ds}: 图清单不完整/重复/范围不符；冒烟使用独立目录和 --limit 2')
    n = len(names)
    for r in results.values():
        r.update(coverage12_pct=ratio(r['covered12_px'], totals['1']['px']+totals['2']['px']),
                 coverage2_pct=ratio(r['covered2_px'], totals['2']['px']),
                 misroute3b_components_pct=ratio(r['table']['3b']['fill'][0], totals['3b']['comps']),
                 misroute3b_pixels_pct=ratio(r['table']['3b']['fill'][1], totals['3b']['px']),
                 fill_per_image=r['fill_count']/n, refine_per_image=r['refine_count']/n,
                 zero_true_pct=ratio(r['zero_true_count'], r['fill_count']))
    if not args.limit:
        ref39 = read_json(step1b.BASELINE / ds / 'summary.json')
        ref41 = read_json(BASE41 / ds / 'summary.json')
        if totals != ref39['components'] or totals != ref41['components'] or n != ref41['images']:
            raise ValueError('全量漏检连通域数与 batch39/41 不一致')
        for tau in (.1, .2):
            r = results[rule_id(dict(method='base', tau=tau, A_max=None, prefilter_novel_lt=None))]
            ref = ref41['sets']['d1all']['rules'][api.rule_name(tau, None)]
            for key in ('table', 'fill_per_image', 'refine_per_image'):
                if r[key] != ref[key]:
                    raise ValueError(f'全量 base {key} 与 batch41 不一致')
    summary = dict(version=VERSION, dataset=ds, partial=bool(args.limit), images=n, components=totals,
                   component_total=sum(v['comps'] for v in totals.values()), rules=results,
                   checks=dict(batch39_per_image=True, batch41_per_image=True, full_summary=not bool(args.limit)),
                   sam=dict(missing_images=missing_sam, available_images=n-len(missing_sam),
                            missing_policy='按成员拆分，超限成员无 SAM 时保持完整'),
                   timing=dict(mean_image_seconds=float(np.mean(times)), max_image_seconds=max(times),
                               peak_rss_mib=max(memory)))
    lines = [f'# {api.SPLIT[ds]}：第 1d 步', '',
             f'{n} 图；'+('仅冒烟，不用于正式冻结。' if args.limit else '全量。'), '',
             '覆盖率按补漏候选并集与漏检像素的交集计算；误分率按最佳候选决定的整个连通域计数。',
             '无真值比例分母为全部最终补漏候选；预过滤仅删补漏组新增像素 <100 的候选。', '', *SUMMARY_HEADER]
    lines += [summary_line(api.SPLIT[ds], k, r) for k, r in results.items()]
    lines += ['', f'SAM 划分可用 {n-len(missing_sam)}/{n} 图；缺缓存时仅按成员拆分，超限成员保持完整。', '',
              '## 拆分审计（每种面积方案的 τ=0.1、无预过滤）', '',
              '| 方案 | 超限成员 | 缺 SAM 未拆成员 | SAM 块 | 丢弃 <20 像素 | 标签0像素 | 仍超限块 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for key, r in results.items():
        c = r['config']
        if c['method'] in ('b', 'ab') and c['tau'] == .1 and c['prefilter_novel_lt'] is None:
            a = r['audit']
            lines.append('| '+key+' | '+' | '.join(str(a[k]) for k in ('large_members', 'unsplit_without_sam',
                'sam_blocks', 'discarded_small_px', 'unlabeled_px', 'blocks_still_large'))+' |')
    for key, r in results.items():
        if r['config']['tau'] == .1:
            lines += detail_tables(api, key, r)
    write_json(args.out / ds / 'summary.json', summary)
    (args.out / ds / 'tables.md').write_text('\n'.join(lines)+'\n')
    print(f'{ds}: merge passed, {n} images, {summary["component_total"]} components', flush=True)


def choose_rule(validation):
    values = []
    for key, c in CONFIGS.items():
        metrics = {}
        for ds in ('second_cv', 'sysu_val'):
            r = validation[ds]['rules'][key]
            mis = r['misroute3b_pixels_pct']
            metrics[ds] = {k: r[k] for k in ('coverage12_pct', 'coverage2_pct', 'misroute3b_pixels_pct',
                          'misroute3b_components_pct', 'fill_per_image', 'refine_per_image', 'zero_true_pct')}
            metrics[ds]['satisfies_5pct'] = None if mis is None else mis <= 5
        eligible = metrics['second_cv']['satisfies_5pct'] is True and metrics['second_cv']['coverage12_pct'] is not None
        values.append(dict(id=key, **c, eligible=eligible, validation=metrics))
    eligible = [v for v in values if v['eligible']]
    if not eligible:
        return None, values
    maximum = max(v['validation']['second_cv']['coverage12_pct'] for v in eligible)
    tied = [v for v in eligible if maximum-v['validation']['second_cv']['coverage12_pct'] < .1]
    winner = min(tied, key=lambda v: (v['validation']['second_cv']['fill_per_image'],
                                     -v['validation']['second_cv']['coverage12_pct'], v['id']))
    return dict(winner, maximum_coverage12_pct=maximum, tie_tolerance_pp=.1,
                tie_candidates=[v['id'] for v in tied]), values


def run_select(args):
    api = route_api()
    validation = {ds: read_json(args.out / ds / 'summary.json') for ds in ('second_cv', 'sysu_val')}
    for ds, s in validation.items():
        if s['dataset'] != ds or s['version'] != VERSION or s['partial'] != bool(args.limit):
            raise ValueError('验证汇总版本/范围不一致')
    winner, values = choose_rule(validation)
    selection = dict(version=VERSION, partial=bool(args.limit), candidates=values,
                     selection_datasets=['second_cv', 'sysu_val'], test_used_for_selection=False,
                     policy='SECOND 验证③≥30%像素误分≤5%；最大①+②覆盖，差<0.1百分点取补漏/图更少；SYSU仅报告约束是否满足',
                     status='selected' if winner else 'no_eligible_rule')
    if winner:
        selection.update({k: winner[k] for k in ('id', 'method', 'tau', 'A_max', 'prefilter_novel_lt',
                         'validation', 'maximum_coverage12_pct', 'tie_tolerance_pp', 'tie_candidates')})
    filename = ('smoke_rule.json' if args.limit else 'frozen_rule.json') if winner else 'selection_failed.json'
    path = args.out / filename
    if path.exists() and read_json(path) != selection:
        raise ValueError(f'{path}: 已有不同冻结结果，请使用新的输出目录')
    write_json(path, selection)
    # 选择与落盘只依赖验证集；此后才读取已算好的测试汇总。
    summaries = dict(validation)
    for ds in ('second', 'sysu', 'levir', 'whu'):
        s = read_json(args.out / ds / 'summary.json')
        if s['dataset'] != ds or s['version'] != VERSION or s['partial'] != bool(args.limit):
            raise ValueError('测试汇总版本/范围不一致')
        summaries[ds] = s
    lines = ['# 第 1d 步：全部方案及冻结方案', '',
             '仅冒烟：不生成正式 frozen_rule.json。' if args.limit else '规则只用验证集选择；测试只汇总预先计算的同一冻结组合。', '',
             selection['policy']+'。零分母为“—”，不能证明约束成立。', '',
             '## 全部方案总表', '', *SUMMARY_HEADER]
    for ds in DATASETS:
        lines += [summary_line(api.SPLIT[ds], key, r) for key, r in summaries[ds]['rules'].items()]
    lines += ['', '## 验证约束', '', '| 方案 | SECOND ≤5% | SYSU ≤5% | 入选候选 |', '|---|---|---|---|']
    def status(v):
        return '无该格样本' if v is None else '是' if v else '否'
    for v in values:
        lines.append(f'| {v["id"]} | {status(v["validation"]["second_cv"]["satisfies_5pct"])} | '
                     f'{status(v["validation"]["sysu_val"]["satisfies_5pct"])} | {status(v["eligible"])} |')
    if winner:
        key = winner['id']
        lines += ['', '## 冻结方案测试一次', '', f'冻结参数：`{json.dumps(CONFIGS[key], ensure_ascii=False)}`。', '',
                  f'SECOND 验证①+②覆盖 {fmt(winner["validation"]["second_cv"]["coverage12_pct"])}%；'
                  f'合格组合最高覆盖 {fmt(winner["maximum_coverage12_pct"])}%；'
                  f'进入0.1百分点平局范围 {len(winner["tie_candidates"])} 个组合。', '', *SUMMARY_HEADER]
        lines += [summary_line(api.SPLIT[ds], key, summaries[ds]['rules'][key]) for ds in DATASETS[1:] if ds != 'sysu_val']
        for ds in DATASETS:
            lines += [f'## {api.SPLIT[ds]}：冻结方案详表', ''] + detail_tables(api, key, summaries[ds]['rules'][key])
    else:
        lines += ['', '无满足 SECOND 验证约束且有①+②分母的方案；未冻结，不选择违反约束的替代方案。']
    (args.out / 'tables.md').write_text('\n'.join(lines)+'\n')
    if not winner:
        raise ValueError('没有满足验证约束的方案；详见 tables.md 与选择记录')
    print(f'{filename}: {winner["id"]}', flush=True)


def component_choice(api, data, objs, ci):
    best, px = api.best_candidates(data['labels'], len(data['comps']), objs)
    b = int(best[ci])
    if b < 0:
        return dict(cand=-1, route='none', best_px=0, r=None, k=None)
    o = objs[b]
    return dict(cand=b, route=o['route'], best_px=int(px[ci]), r=o['r'],
                k=api.rk(o, data['img'].base)[1], origins=o['origins'], split=o['split'])


def render_example(api, data, entry, final, dest):
    before = entry['step1']
    crop = api.render('second', entry['image'], entry['component'], before['cand'], dest)
    image = data['img']
    selected = final[entry['after']['cand']]
    overlay = api.b38.blend(common.load_rgb('second', entry['image'], 't2'), image.base, api.b38.COLORS['base'], .45)
    seed = entry['component']['seed']
    component = data['labels'] == data['labels'][tuple(seed)]
    overlay = api.b38.blend(overlay, component, api.b38.COLORS['comp'], .8)
    for obj, color in [(o, (255, 140, 0)) for o in final if set(o['origins']) & set(selected['origins'])] + [(selected, (180, 70, 255))]:
        x0, y0, x1, y1 = obj['bbox']
        edge = obj['mask'] & ~ndimage.binary_erosion(obj['mask'], EIGHT)
        overlay[y0:y1, x0:x1][edge] = color
    y0, x0, side, _ = crop
    S, gap = api.b38.SIDE, 4
    panels = [Image.fromarray(overlay[y0:y0+side, x0:x0+side]).resize((S, S), Image.Resampling.NEAREST),
              Image.fromarray(overlay).resize((S, S), Image.Resampling.BILINEAR)]
    scale = S/image.base.shape[0]
    ImageDraw.Draw(panels[1]).rectangle([x0*scale, y0*scale, (x0+side)*scale-1, (y0+side)*scale-1],
                                       outline=api.b38.COLORS['box'], width=2)
    output = Image.new('RGB', (8*S+7*gap, S+40), 'white')
    with Image.open(dest) as original:
        output.paste(original, (0, 40))
    for i, panel in enumerate(panels, 6):
        output.paste(panel, (i*(S+gap), 40))
    b, a = entry['step1b'], entry['after']
    ImageDraw.Draw(output).text((5, 4), f'{entry["image"]} | step1: {before["route"]} r={before["r"]} | '
        f'step1b: {b["route"]} r={b["r"]} | step1d: {a["route"]} r={a["r"]:.6f}', fill='black')
    ImageDraw.Draw(output).text((5, 22), 'green: step1 winner | orange: related final blocks | purple: final winner | red: missed', fill='black')
    output.save(dest)
    return crop


def run_examples(args):
    api = route_api()
    frozen = read_json(args.out / ('smoke_rule.json' if args.limit else 'frozen_rule.json'))
    if frozen['status'] != 'selected' or frozen['partial'] != bool(args.limit):
        raise ValueError('没有相同范围的冻结规则')
    config = {k: frozen[k] for k in ('method', 'tau', 'A_max', 'prefilter_novel_lt')}
    key, pools = rule_id(config), dict(changed=[], still_refine=[])
    names = api.s34.image_list('second')[:args.limit or None]
    seen = {}
    basekey = rule_id(dict(method='base', tau=config['tau'], A_max=None, prefilter_novel_lt=None))
    for row in iter_rows(args.out, 'second'):
        if row['index'] in seen:
            raise ValueError('SECOND examples 重复图')
        seen[row['index']] = row['image']
        for ci, comp in enumerate(row['comps']):
            if comp['group'] != '2':
                continue
            old = row['rules'][basekey]['destinations'][ci]
            new = row['rules'][key]['destinations'][ci]
            group = 'changed' if old == 'refine' and new == 'fill' else 'still_refine' if old == new == 'refine' else None
            if group:
                pools[group].append(dict(image=row['image'], component=comp, ci=ci))
    if sorted(seen.items()) != list(enumerate(names)):
        raise ValueError('SECOND examples 图清单不完整')
    dest = args.out / 'examples'
    dest.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    report = dict(seed=SEED, partial=bool(args.limit), rule=config, comparison='同τ的第1b步、无预过滤；同时展示第1步', groups={})
    body = []
    titles = dict(changed='第1b步②去精修，冻结方案改去补漏', still_refine='第1b步②去精修，冻结方案仍去精修')
    ctx = api.Context('second')
    for group, pool in pools.items():
        pool.sort(key=lambda e: (e['image'], e['ci']))
        chosen = [pool[int(i)] for i in sorted(rng.choice(len(pool), min(8, len(pool)), replace=False))]
        report['groups'][group] = dict(available=len(pool), requested=8, selected=len(chosen),
                                      shortage=max(0, 8-len(pool)), samples=chosen)
        body.append(f'<h2>{titles[group]}</h2><p>可选 {len(pool)}，展示 {len(chosen)}。'+('不足8例，全部展示。' if len(pool)<8 else '')+'</p>')
        for i, entry in enumerate(chosen):
            data = prepare(api, 'second', entry['image'], ctx)
            final, _ = build_candidates(api, data, config)
            base, _ = build_candidates(api, data, CONFIGS[basekey])
            original = [dict(o, route='fill' if o['r']<config['tau'] else 'refine') for o in data['fulls']]
            ci = entry['ci']
            if data['comps'][ci] != entry['component']:
                raise ValueError('例图连通域不一致')
            entry.update(step1=component_choice(api, data, original, ci),
                         step1b=component_choice(api, data, base, ci), after=component_choice(api, data, final, ci))
            expected = 'fill' if group == 'changed' else 'refine'
            if entry['step1b']['route'] != 'refine' or entry['after']['route'] != expected:
                raise ValueError('例图重算去向不一致')
            entry['file'] = f'{group}_{i}.png'
            entry['crop'] = render_example(api, data, entry, final, dest/entry['file'])
            caption = '；'.join(f'{label} r={fmt(entry[k]["r"])}, {api.ROUTE_NAMES[entry[k]["route"]]}'
                               for k, label in (('step1', '第1步'), ('step1b', '第1b步'), ('after', '冻结方案')))
            body.append(f'<section><p>{html.escape(entry["image"])}：{caption}</p><div class="hdr">'+
                        ''.join(f'<div>{t}</div>' for t in ('T1', 'T2', '真值', '检测器', '第1步候选', '第1步整图', '最终候选/块', '最终整图'))+
                        f'</div><img loading="lazy" src="{entry["file"]}" alt="{html.escape(caption)}"></section>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>第1d步 SECOND 例图</title><style>'+api.b38.STYLE+'''
.hdr{display:grid;grid-template-columns:repeat(8,1fr)} section img{width:100%;height:auto}section{margin-bottom:24px}
</style><body><h1>SECOND 测试：冻结分流方案</h1>'''+f'<p>{html.escape(str(config))}；种子 {SEED}。'+('仅前两图冒烟。' if args.limit else '')+'''
绿色：第1步最佳候选；橙色：相关最终候选/块；紫色：最终最佳候选；红色：漏检连通域。
成员组内去重后的 r 为完整并集覆盖率，去向继承成员组；不会用合并 r 重新分流。</p>'''+''.join(body)+'</body></html>'
    (dest/'index.html').write_text(page)
    write_json(dest/'samples.json', report)
    print(json.dumps({k: {x:v[x] for x in ('available', 'selected', 'shortage')} for k,v in report['groups'].items()}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('route', 'merge', 'select', 'examples'))
    p.add_argument('--dataset', choices=DATASETS)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--out', type=local_output, required=True)
    args = p.parse_args()
    if args.limit < 0 or args.shards < 1 or not 0 <= args.shard < args.shards:
        p.error('要求 limit≥0 且 0≤shard<shards')
    if args.stage in ('route', 'merge') and args.dataset is None:
        p.error('route/merge 需要 --dataset')
    if args.stage != 'route' and (args.shard != 0 or args.shards != 1):
        p.error('仅 route 接受分片参数')
    if args.stage in ('route', 'merge'):
        {'route': run_route, 'merge': run_merge}[args.stage](args, args.dataset)
    else:
        {'select': run_select, 'examples': run_examples}[args.stage](args)
    if 'torch' in sys.modules:
        raise RuntimeError('缓存流程意外导入 torch')


if __name__ == '__main__':
    main()
