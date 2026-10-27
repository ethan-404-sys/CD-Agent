"""确定性候选均衡选择、人工审核后定稿和分层抽样。"""
from exp6k_common import *
from collections import Counter
import random

PSEUDO_TYPES = ('季节或物候', '光照或阴影', '色调或成像差异', '同一地物外观变化',
                '临时物体', '配准错位', '无明显差异', '类型不明')


def auto_positive(row):
    if row['label'] != 1 or row['precision'] < .7 or row['area'] < 150:
        raise ValueError('自动正例需要 label=1、precision≥0.7、area≥150')
    if row['dataset'] == 'second':
        kind = row['change_kind']
        a, _, b = kind.partition(' → ')
        rationale = f'{kind}（同类内部变化，如拆除重建）' if a and a == b else kind
    else:
        rationale = f"有变化，面积档 {row['area_bin']}"
    return dict(row, rationale=rationale, rationale_source='auto')


def balanced(rows, n, used, counts, rng, fields, previous=()):
    """类别轮流优先，其余字段累计频次最少者优先，随机种子仅用于破同分。"""
    pool = list(rows)
    rng.shuffle(pool)
    selected = []
    hist = {f: Counter(str(r.get(f, '')) for r in previous) for f in fields}
    while len(selected) < n:
        choices = [r for r in pool if key(r) not in used and counts[r['image']] < 2]
        if not choices:
            break
        def score(r):
            values = [hist[f][str(r.get(f, ''))] for f in fields]
            return (values[0], sum(values[1:])) if values else (0, 0)
        row = min(choices, key=score)
        selected.append(row)
        used.add(key(row))
        counts[row['image']] += 1
        for f in fields:
            hist[f][str(row.get(f, ''))] += 1
    return selected


def choose_pool(rows, npos, nneg, ratio, hard_ratio, used=None, counts=None):
    used = set() if used is None else used
    counts = Counter() if counts is None else counts
    rng = random.Random(SEED)
    selected = []
    positive = [r for r in rows if r['label'] == 1]
    negative = [r for r in rows if r['label'] == 0]
    fields = ['change_kind', 'area_bin', 'source'] if rows and rows[0]['dataset'] == 'second' else ['area_bin', 'source']
    def pick(candidates, n, fs=fields):
        result = balanced(candidates, n, used, counts, rng, fs,
                          [r for r in selected if candidates and r['label'] == candidates[0]['label']])
        selected.extend(result)
        return len(result)
    n2 = round(npos * ratio)
    pick([r for r in positive if r['majority'] == 2], n2)
    pick([r for r in positive if r['majority'] == 1], npos - n2)
    pick(positive, npos - sum(r['label'] for r in selected))
    neg_fields = ['area_bin', 'source']
    nhard = round(nneg * hard_ratio)
    pick([r for r in negative if r['hard_demo']], nhard, neg_fields)
    pick([r for r in negative if not r['hard_demo']], nneg - nhard, neg_fields)
    pick(negative, nneg - sum(r['label'] == 0 for r in selected), neg_fields)
    return selected


def shortlist(a):
    rows = [sample_row(a.out, r) for r in table_rows(a, 'train')
            if r['area'] >= 150 and (r['label'] == 0 or r['precision'] >= .7)]
    rows = [auto_positive(r) if r['label'] else r for r in rows]
    used, counts = set(), Counter()
    npos, nneg = (min(32, a.limit), min(48, a.limit)) if a.limit else (32, 48)
    primary = choose_pool(rows, npos, nneg, .75, .75, used, counts)
    # 两张候选清单合在一起也保持每图至多两个，后续任取子集均满足。
    nextra = min(160, a.limit) if a.limit else 160
    extra = choose_pool(rows, nextra, nextra, .7, .75, used, counts)
    for row in extra:
        if row['label'] == 0:
            row.update(rationale='无变化', pseudo_type='', rationale_source='auto')
    for number, row in enumerate(primary + extra, 1):
        row['number'] = number
    report = {name: {'target': target,
                     'actual': dict(Counter(str(r['label']) for r in items)),
                     'hard_negatives': sum(r['label'] == 0 and r['hard_demo'] for r in items),
                     'max_per_image': max(Counter(r['image'] for r in items).values(), default=0)}
              for name, target, items in [('shortlist', {'1': npos, '0': nneg}, primary),
                                          ('extra', {'1': nextra, '0': nextra}, extra)]}
    write(a.out / a.dataset / 'shortlist_selection.json', report)
    if not a.limit and (report['shortlist']['actual'] != {'1': 32, '0': 48} or
                        report['shortlist']['hard_negatives'] < 36 or
                        report['extra']['actual'] != {'1': 160, '0': 160}):
        raise ValueError('候选不足以满足正式配额，详见 shortlist_selection.json')
    write(manifest_path(a, 'shortlist'), primary)
    write(manifest_path(a, 'extra'), extra)
    print(json.dumps(report, ensure_ascii=False), flush=True)


def sample(a):
    for split, kind, quota in [('val', 'dev', 150), ('test', 'test', 375)]:
        rows = table_rows(a, split)
        rng = random.Random(SEED)
        selected = []
        populations = {}
        for stratum in STRATA:
            group = [r for r in rows if r['stratum'] == stratum]
            n = min(quota, len(group), a.limit or quota)
            populations[stratum] = dict(population=len(group), selected=n, weight=len(group)/n if n else None)
            for row in rng.sample(group, n):
                item = sample_row(a.out, row)
                item['weight'] = len(group)/n
                selected.append(item)
        selected.sort(key=lambda r: (r['image'], r['unit_id']))
        grouped = {}
        for row in selected:
            group = grouped.setdefault(row['image'], dict(cids=[], all_candidates=row['all_candidates_in_image'],
                                      eligible_candidates=row['eligible_candidates_in_image']))
            group['cids'].append(row['cid'])
        write(manifest_path(a, kind), selected)
        write(a.out / f'exp6k_{kind}_{a.dataset}_images.json', grouped)
        write(a.out / a.dataset / f'{kind}_sampling.json', dict(seed=SEED, limit=a.limit, strata=populations))
        print(f'{a.dataset}/{kind}: {len(selected)}', flush=True)


def finalize(a):
    candidates = read(manifest_path(a, 'shortlist'))
    positive = [auto_positive(r) for r in candidates if r['label'] == 1]
    if len(positive) != 32:
        raise ValueError(f'自动正例需要 32 个，当前 {len(positive)}')
    by_key = {key(r): r for r in candidates if r['label'] == 0}
    annotations = a.annotations or a.out / f'annotations_{a.dataset}.csv'
    kept, seen = [], set()
    with annotations.open(encoding='utf-8-sig', newline='') as f:
        for note in csv.DictReader(f):
            k = note['image'], note['cid']
            if k in seen or k not in by_key:
                raise ValueError(f'审核表候选重复或不存在: {k}')
            seen.add(k)
            row = dict(by_key[k])
            if int(note['label']) != row['label']:
                raise ValueError(f'审核表标签不匹配: {k}')
            doubt = note['gt_doubt'].strip().lower()
            if doubt not in ('true', 'false'):
                raise ValueError(f'负例尚未完成审核，gt_doubt 需 true/false: {k}')
            if doubt == 'true':
                if not note['doubt_reason'].strip():
                    raise ValueError(f'真值可疑需填写 doubt_reason: {k}')
                continue
            if not note['rationale'].strip() or note['pseudo_type'].strip() not in PSEUDO_TYPES:
                raise ValueError(f'负例需要中文理由和规定的 pseudo_type: {k}')
            row.update(rationale=note['rationale'].strip(), pseudo_type=note['pseudo_type'].strip(),
                       gt_doubt=False, doubt_reason=note['doubt_reason'].strip(), rationale_source='human')
            kept.append(row)
    if seen != set(by_key):
        raise ValueError('审核表缺少负例候选')
    used, counts = {key(r) for r in positive}, Counter(r['image'] for r in positive)
    rng = random.Random(SEED)
    fields = ['pseudo_type', 'area_bin', 'source']
    negative = balanced([r for r in kept if r['hard_demo']], 16, used, counts, rng, fields)
    if len(negative) < 16:
        raise ValueError('审核通过的难负例不足 16 个')
    negative += balanced(kept, 16, used, counts, rng, fields, previous=negative)
    if len(negative) != 32:
        raise ValueError(f'排除真值可疑后负例不足 32 个: {len(negative)}')
    base = positive + negative
    extra = read(manifest_path(a, 'extra'))
    used, counts = {key(r) for r in base}, Counter(r['image'] for r in base)
    extension = choose_pool(extra, 96, 96, .7, .75, used, counts)
    if Counter(r['label'] for r in extension) != Counter({0: 96, 1: 96}):
        raise ValueError('扩展候选不足以构成 256 池；检查 shortlist_selection.json')
    extension = [auto_positive(r) if r['label'] else
                 dict(r, rationale='无变化', pseudo_type='', rationale_source='auto') for r in extension]
    write(manifest_path(a, 'demos'), base)
    write(manifest_path(a, 'demos256'), base + extension)
    print(f'{a.dataset}: 64 / 256 示例池已生成', flush=True)
