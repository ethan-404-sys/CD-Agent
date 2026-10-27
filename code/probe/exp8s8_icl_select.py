"""确定性示例选择、负例审核定稿、按图隔离与分层样本。"""
from exp8s8_icl_common import *
from exp6k_select import PSEUDO_TYPES as _K6_TYPES
PSEUDO_TYPES = _K6_TYPES + ('非建筑地物变化',)  # LEVIR/WHU 只标建筑：道路、植被、裸地的真实变化按数据集口径属于无变化
import csv
import random


def balanced(rows, n, used, counts, cap, rng, fields, previous=(), priority=None):
    pool = list(rows)
    rng.shuffle(pool)
    hist = {f: Counter(str(r.get(f, '')) for r in previous) for f in fields}
    selected = []
    while len(selected) < n:
        choices = [r for r in pool if key(r) not in used and counts[r['image']] < cap]
        if not choices:
            break
        row = min(choices, key=lambda r: (priority(r) if priority else ()) +
                  tuple(hist[f][str(r.get(f, ''))] for f in fields))
        selected.append(row)
        used.add(key(row))
        counts[row['image']] += 1
        for f in fields:
            hist[f][str(row.get(f, ''))] += 1
    return selected


def choose(rows, cap, positive_target=32, negative_target=32):
    used, counts, rng = set(), Counter(), random.Random(SEED)
    selected = []
    def pick(group, n, fields):
        picked = balanced(group, n, used, counts, cap, rng, fields, selected)
        selected.extend(picked)
    pos = [r for r in rows if r['label']]
    neg = [r for r in rows if not r['label']]
    pick([r for r in pos if r['majority']==2], round(positive_target*.75), ['change_kind', 'area_bin', 'source'])
    pick(pos, positive_target-sum(r['label'] for r in selected), ['change_kind', 'area_bin', 'source'])
    pick([r for r in neg if r['hard_demo']], int(np.ceil(negative_target*.75)), ['area_bin', 'source'])
    pick([r for r in neg if not r['hard_demo']], negative_target-sum(not r['label'] for r in selected), ['area_bin', 'source'])
    pick(neg, negative_target-sum(not r['label'] for r in selected), ['area_bin', 'source'])
    return selected, used, counts


def levir_positive_target(n):
    return 8 if n >= 8 else (n + 1) // 2


def choose_levir(rows, negatives, cap, negative_target, preserve_positive_images=True):
    positive = [r for r in rows if r['label']]
    positive_counts = Counter(r['image'] for r in positive)
    negative_counts = Counter(r['image'] for r in rows if not r['label'])
    used, counts, rng = set(), Counter(), random.Random(SEED)
    selected = balanced(positive, levir_positive_target(len(positive)), used, counts, cap, rng,
                        ['change_kind', 'area_bin', 'source'],
                        priority=lambda r: (counts[r['image']] > 0, positive_counts[r['image']],
                                            -negative_counts[r['image']]))
    if preserve_positive_images:
        negatives = [r for r in negatives if counts[r['image']] or not positive_counts[r['image']]]
    # 新增示例图会排除整图正例；负例优先复用示例图或使用无正例图。
    def priority(row):
        name = row['image']
        return (0 if counts[name] else positive_counts[name],
                0 if counts[name] else 1, -negative_counts[name])
    negative = balanced([r for r in negatives if r['hard_demo']], (negative_target + 1)//2,
                        used, counts, cap, rng, ['pseudo_type', 'area_bin', 'source'], priority=priority)
    negative += balanced(negatives, negative_target-len(negative), used, counts, cap, rng,
                         ['pseudo_type', 'area_bin', 'source'], negative, priority=priority)
    return selected + negative, used, counts


def levir_selection_details(rows, demos):
    images = {r['image'] for r in demos}
    dev = [r for r in rows if r['image'] not in images]
    return dict(
        selection_policy='LEVIR正例目标8个；总正例不足8时取一半向上取整。优先每图一个正例、'
                         '图中正例少且负例候选多；负例优先已有示例图或无正例图。'
                         '保留正例图时同图先限2，数量或难负不足才放宽4；仍不足才使用额外正例图。'
                         '示例图之外全部val图进入开发集。',
        selection_reason='ICL输入上限16、正负各至多8；缩减正例示例池，尽量保留开发集正例，'
                         '避免全部17个正例进入示例池后无法用② AP选方案。负例池仍保留32个供选择。',
        dev_counts=dict(candidates=len(dev), positive=sum(r['label']==1 for r in dev),
                        positive_cell2=sum(r['label']==1 and r['majority']==2 for r in dev),
                        positive_other=sum(r['label']==1 and r['majority']!=2 for r in dev),
                        negative=sum(r['label']==0 for r in dev)))


def split_document(a, demos, finalized):
    source_split = 'val' if a.dataset == 'levir' else 'test'
    images = scope_names(a.dataset, source_split, a.limit)
    demo_images = sorted({r['image'] for r in demos})
    excluded = set(demo_images)
    path = a.out / a.dataset / 'icl_split.json'
    doc = dict(dataset=a.dataset, limit=a.limit, finalized=finalized, demo_images=demo_images,
        excluded_images=sorted(excluded), neighbor_images=sorted(excluded-set(demo_images)),
        remaining_images=[n for n in images if n not in excluded],
        coordinate_source=None, coordinate_evidence=str(HERE/'consult/ANSWER_defs.md'),
        spatial_note='可能有相邻切块泄漏' if a.dataset=='whu' else '示例图与开发图互斥')
    rank = a.out / a.dataset / 'rank_test.json'
    if a.dataset == 'whu' and rank.exists() and read(rank)['excluded_images'] != doc['excluded_images']:
        raise ValueError('WHU 已有评估范围与新示例图不同，不能覆盖已评测试结果')
    write(path, doc)


def select(a):
    previous = a.out / a.dataset / 'selection.json'
    if previous.exists() and read(previous)['status']=='finalized':
        raise ValueError('已有定稿示例，不能重选覆盖')
    split = 'val' if a.dataset == 'levir' else 'test'
    rows = [sample_row(a.out, r, 'demo') for r in table_rows(a, split)]
    for row in rows:
        row['split_note'] = demo_note(a.dataset)
        row['review_state'] = 'auto_positive' if row['label'] else 'pending_annotation'
        if row['label']:
            row.update(rationale=row['change_kind'] or '有变化', rationale_source='auto')
    target = min(32, max(1, a.limit//2)) if a.limit else 32
    positive_target = levir_positive_target(sum(r['label'] for r in rows)) if a.dataset=='levir' else target
    nneg = min(48, 2*a.limit) if a.limit else 48
    cap = 2
    core, used, counts = (choose_levir(rows, [r for r in rows if not r['label']], cap, target)
                         if a.dataset=='levir' else choose(rows, cap, target, target))
    if a.dataset == 'levir' and (sum(r['label'] for r in core)<positive_target
                               or sum(not r['label'] for r in core)<min(target, sum(not r['label'] for r in rows))
                               or sum(not r['label'] and r['hard_demo'] for r in core) < min(int(np.ceil(target/2)),
                                   sum(not r['label'] and r['hard_demo'] for r in rows))):
        cap = 4
        core, used, counts = choose_levir(rows, [r for r in rows if not r['label']], cap, target)
    extra = []
    rng = random.Random(SEED)
    hard_needed = min(max(0, nneg-sum(not r['label'] for r in core)),
                      max(0, int(np.ceil(.75*nneg))-sum(not r['label'] and r['hard_demo'] for r in core)))
    # 48个待看图候选本身不占正式示例名额；最终选择仍受每图上限约束。
    annotation_counts = Counter()
    extra += balanced([r for r in rows if not r['label'] and r['hard_demo']], hard_needed,
                      used, annotation_counts, nneg, rng, ['area_bin', 'source'])
    extra += balanced([r for r in rows if not r['label']], max(0, nneg-sum(not r['label'] for r in core)-len(extra)),
                      used, annotation_counts, nneg, rng, ['area_bin', 'source'])
    shortlist = core + extra
    for number, row in enumerate(shortlist, 1):
        row['number'] = number
    write(manifest_path(a, 'shortlist'), shortlist)
    write(manifest_path(a, 'demos'), core)
    split_document(a, core, False)
    report = dict(limit=a.limit, requested=dict(positive=positive_target, negative=target, annotation_negative=nneg),
        selected=dict(positive=sum(r['label'] for r in core), negative=sum(not r['label'] for r in core)),
        candidate_positive=sum(r['label'] for r in rows), cap=cap,
        max_per_image=max(counts.values(), default=0),
        demo_hard_negatives=sum(not r['label'] and r['hard_demo'] for r in core),
        annotation_negatives=sum(not r['label'] for r in shortlist),
        annotation_hard_negatives=sum(not r['label'] and r['hard_demo'] for r in shortlist),
        positive_eligibility='所有冻结label=1候选；不额外要求precision≥0.7或面积≥150，以保留稀少正例',
        status='pending_negative_annotations')
    if a.dataset=='levir':
        report.update(levir_selection_details(rows, core))
    write(a.out / a.dataset / 'selection.json', report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


def finalize(a):
    candidates = read(manifest_path(a, 'shortlist'))
    positive = [r for r in candidates if r['label']]
    by_key = {key(r): r for r in candidates if not r['label']}
    approved, seen = [], set()
    with (a.annotations or a.out / f'annotations_{a.dataset}.csv').open(encoding='utf-8-sig', newline='') as f:
        for note in csv.DictReader(f):
            k = note['image'], note['cid']
            if k in seen or k not in by_key or int(note['label']) != 0:
                raise ValueError(f'负例审核表键或标签错误: {k}')
            seen.add(k)
            doubt = note['gt_doubt'].strip().lower()
            if doubt not in ('true', 'false'):
                raise ValueError(f'负例尚未看图标注: {k}')
            if doubt == 'true':
                if not note['doubt_reason'].strip():
                    raise ValueError('真值可疑需填写原因')
                continue
            if note['pseudo_type'] not in PSEUDO_TYPES or not note['rationale'].strip():
                raise ValueError('负例需填写规定类型和中文理由')
            approved.append(dict(by_key[k], pseudo_type=note['pseudo_type'], rationale=note['rationale'].strip(),
                                 gt_doubt=False, rationale_source='human', review_state='annotated'))
    if seen != set(by_key):
        raise ValueError('负例审核表缺项')
    report = read(a.out / a.dataset / 'selection.json')
    cap, target = report['cap'], report['requested']['negative']
    if a.dataset=='levir':
        rows = [sample_row(a.out, r, 'demo') for r in table_rows(a, 'val')]
        for row in rows:
            if row['label']:
                row.update(split_note=demo_note(a.dataset), review_state='auto_positive',
                           rationale=row['change_kind'] or '有变化', rationale_source='auto')
        for cap, preserve in ((2, True), (4, True), (4, False)):
            demos, used, counts = choose_levir(rows, approved, cap, target, preserve)
            positive = [r for r in demos if r['label']]
            negative = [r for r in demos if not r['label']]
            if (len(positive)==levir_positive_target(sum(r['label'] for r in rows))
                    and len(negative)==target and sum(r['hard_demo'] for r in negative)>=(target+1)//2):
                break
        report['requested']['positive'] = levir_positive_target(sum(r['label'] for r in rows))
        report.update(levir_selection_details(rows, demos), cap=cap,
                      candidate_positive=sum(r['label'] for r in rows),
                      selected=dict(positive=len(positive), negative=len(negative)),
                      max_per_image=max(counts.values(), default=0),
                      demo_hard_negatives=sum(r['hard_demo'] for r in negative))
    else:
        used, counts = {key(r) for r in positive}, Counter(r['image'] for r in positive)
        rng = random.Random(SEED)
        negative = balanced([r for r in approved if r['hard_demo']], int(np.ceil(target/2)), used, counts, cap,
                            rng, ['pseudo_type', 'area_bin', 'source'])
        negative += balanced(approved, target-len(negative), used, counts, cap, rng,
                             ['pseudo_type', 'area_bin', 'source'], negative)
    if not a.limit and (len(negative) != target or sum(r['hard_demo'] for r in negative) < int(np.ceil(target/2))):
        raise ValueError('审核后负例数量或难负比例不足，需补充真实看图候选')
    demos = positive + negative
    split_document(a, demos, True)
    write(manifest_path(a, 'demos'), demos)
    report.update(status='finalized', finalized_positive=len(positive), finalized_negative=len(negative),
                  finalized_hard_negatives=sum(r['hard_demo'] for r in negative),
                  finalized_max_per_image=max(counts.values(), default=0))
    write(a.out / a.dataset / 'selection.json', report)


def sample(a):
    splitdoc = read(a.out / a.dataset / 'icl_split.json')
    if splitdoc['limit'] != a.limit:
        raise ValueError('ICL示例划分范围不匹配')
    if not a.limit and not splitdoc['finalized']:
        raise ValueError('先完成负例标注与finalize，再生成正式开发/测试样本')
    excluded = set(splitdoc['excluded_images'])
    scopes = [('val', 'dev')] if a.dataset=='levir' else []
    scopes += [('test', 'test')]
    for split, kind in scopes:
        source = table_rows(a, split)
        rows = [r for r in source if (a.dataset=='levir' and split=='test') or r['image'] not in excluded]
        selected, populations = [], {}
        rng = random.Random(SEED)
        for stratum in STRATA:
            group = [r for r in rows if r['stratum']==stratum]
            n = len(group) if kind=='dev' else min(375, len(group), a.limit or 375)
            weight = 1.0 if kind=='dev' else len(group)/n if n else None
            populations[stratum] = dict(population=len(group), selected=n, weight=weight)
            for row in (group if kind=='dev' else rng.sample(group, n)):
                selected.append(dict(sample_row(a.out, row), weight=weight))
        selected.sort(key=lambda r: (r['image'], r['unit_id']))
        write(manifest_path(a, kind), selected)
        images = {}
        for row in selected:
            images.setdefault(row['image'], dict(cids=[], all_candidates=row['all_candidates_in_image'],
                              eligible_candidates=row['eligible_candidates_in_image']))['cids'].append(row['cid'])
        write(a.out / f'exp8s8_{kind}_{a.dataset}_images.json', images)
        remaining = scope_names(a.dataset, split, a.limit)
        if not (a.dataset=='levir' and split=='test'):
            remaining = [n for n in remaining if n not in excluded]
        write(a.out / a.dataset / f'{kind}_sampling.json', dict(dataset=a.dataset, split=split, limit=a.limit,
              strata=populations, seed=SEED, remaining_images=remaining, eligible_candidates=len(rows),
              sampled_candidates=len(selected), demo_images=splitdoc['demo_images'], finalized=splitdoc['finalized']))
    collections = {k: read(manifest_path(a, k)) for k in ('demos', 'dev', 'test') if manifest_path(a, k).exists()}
    validate_sets(collections)
