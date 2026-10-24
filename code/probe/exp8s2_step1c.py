"""第 1c 步的逐图计数、候选池汇总和中文表格；只读缓存。"""
from __future__ import annotations

import time
from exp8s2_cache import np, ndimage, EIGHT, read_json

VERSION = 1
SOURCES = ('B', 'G', 'H1', 'C')
BINS = ('zero', 'lt10', '10_to_half_f', 'half_f_to50', 'ge50')
BIN_NAMES = dict(zero='0', lt10='(0, 10%)', **{'10_to_half_f': '[10%, F/2)',
                 'half_f_to50': '[F/2, 50%)', 'ge50': '≥50%', 'no_new': '无新增（单独计数）'})
FILTERS = ('novel_lt50', 'novel_lt100', 'novel_lt200', 'b_area_lt500', 'h1_top10')
FILTER_NAMES = dict(novel_lt50='新增 <50', novel_lt100='新增 <100', novel_lt200='新增 <200',
                    b_area_lt500='仅 B：原区域 <500', h1_top10='仅 H1：原候选前10')
H1_METHOD = ('按裁开前仅含 H1 的 d1all 原候选排序；多个 H1 成员取缓存 h1_scores 最大值，'
             '降序、并列取原候选序号小者；每图前10，所有拆出块继承原候选去留。')


def baseline_reference(api, ds):
    path = api.s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json'
    manifest = read_json(path)
    return dict(path=str(path), counts=manifest['baseline'],
                f1=api.scores(manifest['baseline'])[0], images=len(manifest['config']['images']))


def original_metadata(api, ds, name, img, prob, chosen):
    # B 的完整面积来自原掩码重建，不能用去掉 D 后的候选面积代替。
    needed = sorted({(s, lv) for o in chosen for s, lv, _ in o['orig'] if s == 'B'}, key=str)
    b_rows = api.raw_rows(ds, name, prob, needed) if needed else {}
    h1_needed = any(s == 'H1' for o in chosen for s, _, _ in o['orig'])
    h1_scores = None
    if h1_needed:
        path = api.s6.h1_path(ds, name)
        if path.is_file():
            with np.load(path, allow_pickle=False) as z:
                if 'h1_scores' in z:
                    h1_scores = z['h1_scores'].copy()
    metadata = []
    for o in chosen:
        members = o['orig']
        sources = sorted({s for s, _, _ in members})
        if not set(sources) <= set(SOURCES):
            raise ValueError(f'{ds}/{name}: unknown candidate sources {sources}')
        b_areas = [b_rows[(s, lv)][i]['area'] for s, lv, i in members if s == 'B']
        scores = []
        for s, lv, i in members:
            if s == 'H1' and h1_scores is not None:
                raw_id = i if lv is None else int(np.argsort(-h1_scores, kind='stable')[:lv][i])
                scores.append(float(h1_scores[raw_id]))
        metadata.append(dict(sources=sources, members=[list(m) for m in members],
                             b_original_area=max(b_areas) if b_areas else None,
                             h1_score=max(scores) if scores else None, h1_rank=None))
    eligible = [i for i, m in enumerate(metadata) if m['sources'] == ['H1']]
    if all(metadata[i]['h1_score'] is not None for i in eligible):
        order = sorted(eligible, key=lambda i: (-metadata[i]['h1_score'], i))
        for rank, i in enumerate(order, 1):
            metadata[i]['h1_rank'] = rank
    return metadata


def precision_bin(n, tp, f):
    if n == 0:
        return 'no_new'
    p = tp / n
    if p == 0:
        return 'zero'
    if p < .1:
        return 'lt10'
    if p < f / 2:
        return '10_to_half_f'
    return 'half_f_to50' if p < .5 else 'ge50'


def counts(mask, gt):
    return dict(tp=int((mask & gt).sum()), fp=int((mask & ~gt).sum()), fn=int((~mask & gt).sum()))


def removal_counts(mask):
    return dict(pixels=int(mask.sum()), components=int(ndimage.label(mask, EIGHT)[1]))


def image_statistics(api, img, cell, labels, comps, fulls, new, cand, reference, rules):
    start = time.perf_counter()
    a = counts(img.base, img.gt)
    expected = read_json(img.path / 'image.json')['baseline']
    if a != expected:
        raise ValueError(f'{img.path}: A TP/FP/FN differs from batch30 image baseline')
    c12 = (img.gt & ~img.base) & np.isin(cell, (1, 2))
    total12 = int(c12.sum())
    if total12 != sum(c['px'] for c in comps if c['group'] in ('1', '2')):
        raise ValueError(f'{img.path}: cell 1+2 pixel totals differ')
    meta = [o['step1c'] for o in fulls]
    available = dict.fromkeys(FILTERS, True)
    available['b_area_lt500'] = all(m['b_original_area'] is not None for m in meta if m['sources'] == ['B'])
    available['h1_top10'] = all(m['h1_rank'] is not None for m in meta if m['sources'] == ['H1'])
    state = {tau: dict(fill=np.zeros_like(img.base), refine=np.zeros_like(img.base),
                      oracle=np.zeros_like(img.base), kept={k: np.zeros_like(img.base) for k in FILTERS if available[k]},
                      dropped=dict.fromkeys(FILTERS, 0)) for tau in rules}
    areas = np.asarray([0] + [c['px'] for c in comps], dtype=np.int64)
    for obj, record in zip(new, cand):
        m = meta[obj['origin']]
        x0, y0, x1, y1 = obj['bbox']
        sl = np.s_[y0:y1, x0:x1]
        novel = obj['mask'] & ~img.base[sl]
        n = int(novel.sum())
        true = novel & img.gt[sl]
        tp = int(true.sum())
        ids = np.unique(labels[sl][true])
        if len(ids) and ids[0] == 0:
            raise ValueError(f'{img.path}: true novel pixels missing component labels')
        missed_area = int(areas[ids].sum())
        votes = {str(c): int((cell[sl][true] == c).sum()) for c in (1, 2, 3, 5)}
        majority = min(votes, key=lambda c: (-votes[c], int(c))) if tp else None
        stat = dict(m, novel_px=n, true_px=tp, precision=tp/n if n else None,
                    bin=precision_bin(n, tp, reference['f1']), true_pixels_by_cell=votes,
                    majority_cell=majority, missed_union_px=missed_area, intersection_px=tp,
                    area_ratio=n/tp if tp else None,
                    iou=tp/(n+missed_area-tp) if missed_area else 0.)
        record['step1c'] = stat
        drop = dict(novel_lt50=n < 50, novel_lt100=n < 100, novel_lt200=n < 200,
                    b_area_lt500=m['sources'] == ['B'] and m['b_original_area'] is not None and m['b_original_area'] < 500,
                    h1_top10=m['sources'] == ['H1'] and m['h1_rank'] is not None and m['h1_rank'] > 10)
        for tau, s in state.items():
            if record['r'] < tau:
                s['fill'][sl] |= novel
                for key in s['kept']:
                    s['dropped'][key] += int(drop[key])
                    if not drop[key]:
                        s['kept'][key][sl] |= novel
            else:
                s['refine'][sl] |= novel
                if n and 2*tp >= n:
                    s['oracle'][sl] |= novel
    result = dict(version=VERSION, baseline_reference=reference, baseline_image_match=True,
                  pixels12=total12, rules={})
    for tau, s in state.items():
        before = int((s['fill'] & c12).sum())
        filters = {}
        for key in FILTERS:
            if not available[key]:
                filters[key] = dict(status='skipped', reason='原始 B 面积缺失' if key == 'b_area_lt500' else 'H1 缓存分数/排序缺失')
                continue
            after = int((s['kept'][key] & c12).sum())
            filters[key] = dict(status='ok', dropped_candidates=s['dropped'][key],
                                coverage_before_px=before, coverage_after_px=after, lost_pixels12=before-after)
        b = img.base | s['refine']
        result['rules'][api.rule_name(tau, None)] = dict(filters=filters,
            coverage_before_px=before, starts=dict(A=a, B=counts(b, img.gt), B_prime=counts(img.base | s['oracle'], img.gt)),
            removal=dict(all_false=removal_counts(b & ~img.gt), added_false=removal_counts(s['refine'] & ~img.gt)))
    result['statistics_seconds'] = time.perf_counter() - start
    return result


def fraction(n, d):
    return n/d if d else None


def quantiles(values):
    return dict(count=len(values), **dict(zip(('median', 'p75', 'p90'),
                map(float, np.percentile(values, [50, 75, 90])) if values else (None, None, None))))


def summarize(api, ds, rows, partial, rules):
    reference = baseline_reference(api, ds)
    for row in rows:
        s = row.get('step1c')
        if not s or s.get('version') != VERSION:
            raise ValueError(f'{ds}/{row["image"]}: missing/current step1c route required; rerun route')
        if s['baseline_reference'] != reference or not s['baseline_image_match']:
            raise ValueError(f'{ds}/{row["image"]}: inconsistent step1c baseline')
    total12 = sum(r['step1c']['pixels12'] for r in rows)
    nimages = len(rows)
    result = dict(version=VERSION, images=nimages, partial=partial, baseline=reference,
                  f_half=reference['f1']/2, low_f_adjustment=reference['f1']/2 < .1,
                  pixels12=total12, sources=list(SOURCES), h1_order=H1_METHOD,
                  fraction_denominator='补漏组全部候选（含无新增）；分档不含无新增，另报有新增分母占比',
                  rules={}, timing=dict(mean_statistics_seconds=float(np.mean([r['step1c']['statistics_seconds'] for r in rows]))))
    all_candidates = [c for row in rows for c in row['sets']['d1all']['cand']]
    for tau in rules:
        key = api.rule_name(tau, None)
        fills = [c['step1c'] for c in all_candidates if c['r'] < tau]
        refines = [c['step1c'] for c in all_candidates if c['r'] >= tau]
        no_new = sum(c['novel_px'] == 0 for c in fills)
        bins = {}
        for b in (*BINS, 'no_new'):
            pool = [c for c in fills if c['bin'] == b]
            bins[b] = dict(count=len(pool), per_image=len(pool)/nimages, fraction=fraction(len(pool), len(fills)),
                           fraction_with_new=fraction(len(pool), len(fills)-no_new) if b != 'no_new' else None,
                           sources={s: dict(count=sum(s in c['sources'] for c in pool),
                                            per_image=sum(s in c['sources'] for c in pool)/nimages) for s in SOURCES},
                           single_source=sum(len(c['sources']) == 1 for c in pool),
                           multi_source=sum(len(c['sources']) > 1 for c in pool))
        filters = {}
        before = sum(r['step1c']['rules'][key]['coverage_before_px'] for r in rows)
        for name in FILTERS:
            values = [r['step1c']['rules'][key]['filters'][name] for r in rows]
            missing = [r['image'] for r, v in zip(rows, values) if v['status'] != 'ok']
            if missing:
                filters[name] = dict(status='skipped', missing_images=missing,
                                     reason='该规则所需原始面积或 H1 排序缺失，不汇总部分结果')
                continue
            totals = {k: sum(v[k] for v in values) for k in
                      ('dropped_candidates', 'coverage_before_px', 'coverage_after_px', 'lost_pixels12')}
            filters[name] = dict(status='ok', **totals, dropped_per_image=totals['dropped_candidates']/nimages,
                                 coverage_before_fraction=fraction(before, total12),
                                 lost_fraction=fraction(totals['lost_pixels12'], total12))
        starts = {}
        for start in ('A', 'B', 'B_prime'):
            c = {metric: sum(r['step1c']['rules'][key]['starts'][start][metric] for r in rows)
                 for metric in ('tp', 'fp', 'fn')}
            f1, iou = api.scores(c)
            starts[start] = dict(**c, f1=f1, iou=iou,
                                 per_image={k: v/nimages for k, v in c.items()})
        a = {k: starts['A'][k] for k in ('tp', 'fp', 'fn')}
        if not partial and a != reference['counts']:
            raise ValueError(f'{ds}: A TP/FP/FN {a} differs from batch30 manifest {reference["counts"]}')
        removal = {}
        for name in ('all_false', 'added_false'):
            c = {metric: sum(r['step1c']['rules'][key]['removal'][name][metric] for r in rows)
                 for metric in ('pixels', 'components')}
            removal[name] = dict(**c, per_image={k: v/nimages for k, v in c.items()})
        result['rules'][key] = dict(fill=dict(count=len(fills), per_image=len(fills)/nimages, bins=bins,
            single_source=sum(len(c['sources']) == 1 for c in fills),
            multi_source=sum(len(c['sources']) > 1 for c in fills),
            positive_cells={g: dict(count=sum(c['bin'] == 'ge50' and c['majority_cell'] == g for c in fills),
                per_image=sum(c['bin'] == 'ge50' and c['majority_cell'] == g for c in fills)/nimages) for g in ('1', '2', '3', '5')},
            coverage_before_px=before, coverage_before_fraction=fraction(before, total12), filters=filters),
            refine=dict(count=len(refines), per_image=len(refines)/nimages,
                no_new=sum(c['novel_px'] == 0 for c in refines),
                zero_intersection=sum(c['intersection_px'] == 0 for c in refines),
                empty_m=sum(c['missed_union_px'] == 0 for c in refines),
                area_ratio=quantiles([c['area_ratio'] for c in refines if c['area_ratio'] is not None]),
                iou=quantiles([c['iou'] for c in refines])), starts=starts, removal=removal)
    result['checks'] = dict(image_baselines_match=True, manifest_baseline_match=None if partial else True,
        scope='前两图仅核对 batch30 逐图 image.json；全量 manifest 计数核对未执行' if partial else '全量 A 与 batch30 manifest 完全一致')
    return result


def fmt(value):
    return '—' if value is None else f'{value:.6f}'


def pct(value):
    return '—' if value is None else f'{100*value:.3f}%'


def tables(s):
    lines = ['', '# 第 1c 步：两组候选构成、预过滤与精修起点', '',
             f'全数据集检测器 F1={s["baseline"]["f1"]:.9f}，F/2={s["f_half"]:.9f}；①+② 漏检像素总数={s["pixels12"]}。',
             f'F 来自 `{s["baseline"]["path"]}`；统计范围 {s["images"]} 图。', '',
             '新增像素精度 p>F/2 时并入可提高 F1；等号不提高。' +
             ('F/2<10%，[10%, F/2) 为空，[F/2, 50%) 实际从10%起。' if s['low_f_adjustment'] else ''),
             '分档占比分母为全部补漏候选（含无新增），同时给出有新增候选分母；无新增单独列出。来源按含该来源多标签计数。',
             'B 原区域取所有 B 成员完整掩码面积的最大值；只过滤仅 B 来源。H1：' + H1_METHOD,
             '预过滤各自独立，像素损失按候选并集之差计算，分母为全部①+②漏检像素；零分母显示“—”。',
             '精修面积比排除零交集；IoU 包含 M 为空时的零值；分位数对整个候选池计算。B′ 为真值筛选的 oracle。',
             s['checks']['scope'] + '。']
    for rule, r in s['rules'].items():
        fill, refine = r['fill'], r['refine']
        lines += ['', f'## {rule}', '', '### 表 1c-1：补漏组精度分档', '',
                  '| 分档 | 候选数 | 每图 | 占全部补漏 | 占有新增补漏 | 单一来源数 | 多来源数 |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for name, b in fill['bins'].items():
            lines.append(f'| {BIN_NAMES[name]} | {b["count"]} | {fmt(b["per_image"])} | {pct(b["fraction"])} | {pct(b["fraction_with_new"])} | {b["single_source"]} | {b["multi_source"]} |')
        lines += ['', f'补漏合计 {fill["count"]}，每图 {fmt(fill["per_image"])}；单一来源 {fill["single_source"]}，多来源 {fill["multi_source"]}。', '',
                  '### 表 1c-2：分档 × 来源（每图候选数，多标签）', '',
                  '| 分档 | B | G | H1 | C |', '|---|---:|---:|---:|---:|']
        for name, b in fill['bins'].items():
            lines.append('| ' + BIN_NAMES[name] + ' | ' + ' | '.join(fmt(b['sources'][k]['per_image']) for k in SOURCES) + ' |')
        lines += ['', '### 表 1c-3：正例新增真值像素多数所在格', '',
                  '| 格 | 候选数 | 每图 |', '|---|---:|---:|']
        for g, b in fill['positive_cells'].items():
            lines.append(f'| {dict(zip(("1", "2", "3", "5"), ("①", "②", "③", "⑤")))[g]} | {b["count"]} | {fmt(b["per_image"])} |')
        lines += ['', '并列取较小格号。', '', '### 表 1c-4：独立预过滤', '',
                  f'过滤前覆盖①+②：{fill["coverage_before_px"]}/{s["pixels12"]}（{pct(fill["coverage_before_fraction"])}）。', '',
                  '| 规则 | 丢弃候选数 | 丢弃数/图 | 过滤前覆盖率 | 过滤后覆盖像素 | 损失像素 | 损失/全部①+② |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for name, f in fill['filters'].items():
            if f['status'] != 'ok':
                lines.append(f'| {FILTER_NAMES[name]} | 跳过：{f["reason"]} | — | — | — | — | — |')
            else:
                lines.append(f'| {FILTER_NAMES[name]} | {f["dropped_candidates"]} | {fmt(f["dropped_per_image"])} | {pct(f["coverage_before_fraction"])} | {f["coverage_after_px"]} | {f["lost_pixels12"]} | {pct(f["lost_fraction"])} |')
        lines += ['', '### 表 1c-5：精修组面积比与 IoU', '',
                  f'候选总数 {refine["count"]}，每图 {fmt(refine["per_image"])}；无新增 {refine["no_new"]}；'
                  f'面积比分母为0 {refine["zero_intersection"]}；M为空 {refine["empty_m"]}。', '',
                  '| 指标 | 分位数候选数 | 中位数 | P75 | P90 |', '|---|---:|---:|---:|---:|']
        for name, label in (('area_ratio', '新增面积 / 交集面积'), ('iou', '新增像素与 M 的 IoU')):
            q = refine[name]
            lines.append(f'| {label} | {q["count"]} | {fmt(q["median"])} | {fmt(q["p75"])} | {fmt(q["p90"])} |')
        lines += ['', '### 表 1c-6：起点对比（像素微平均）', '',
                  '| 起点 | F1 | IoU | TP | FP | FN | TP/图 | FP/图 | FN/图 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for name, c in r['starts'].items():
            label = 'B′' if name == 'B_prime' else name
            lines.append(f'| {label} | {fmt(c["f1"])} | {fmt(c["iou"])} | {c["tp"]} | {c["fp"]} | {c["fn"]} | ' +
                         ' | '.join(fmt(c['per_image'][k]) for k in ('tp', 'fp', 'fn')) + ' |')
        lines += ['', '| B 中需删除的误检 | 像素数 | 8连通域数 | 像素/图 | 连通域/图 |', '|---|---:|---:|---:|---:|']
        for name, label in (('all_false', '全部误检'), ('added_false', '候选新增误检')):
            c = r['removal'][name]
            lines.append(f'| {label} | {c["pixels"]} | {c["components"]} | {fmt(c["per_image"]["pixels"])} | {fmt(c["per_image"]["components"])} |')
    return lines
