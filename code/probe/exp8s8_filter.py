"""六个过滤候选及 F0 基准；只在 LEVIR val 冻结。"""
from exp8s8_common import *
import time


def scaled_units(d, scheme, gsd):
    factor = (.5/gsd)**2
    if scheme == 'F0':
        return s6.units(d, scheme), 0
    threshold = int(np.ceil((100 if scheme == 'F2' else int(scheme[3:]))*factor))
    if scheme != 'F2':
        return s6.units(d, f'F3A{threshold}'), threshold
    result = []
    for u in d['candidates']:
        mask = ndi.binary_opening(u['mask'], structure=s5.EIGHT)
        if mask.sum() >= threshold:
            x0, y0, x1, y1 = u['bbox']
            truth = int((mask & d['gt'][y0:y1, x0:x1]).sum())
            result.append(dict(u, mask=mask, novel=int(mask.sum()), positive=bool(2*truth >= mask.sum())))
    return result, threshold


def counts(d, kept):
    union = np.zeros_like(d['gt'])
    for u in kept:
        s5.add_mask(union, u)
    far = [k for k, v in s6.targets2(d).items() if v is None or v > 5]
    hits, total = s6.object_hits(d, union, far)
    return dict(s5.coverage(d, union), images=1, candidates=len(kept),
                positives=sum(u['positive'] for u in kept), hits2_far=hits, objects2_far=total)


def run(a):
    scheme = frozen(a) if a.dataset == 'whu' else None
    for name in names(a, True):
        d = load(a.dataset, a.split, name)
        rows = {s: counts(d, s6.units(d, s)) for s in SCHEMES}
        sensitivity = {}
        if a.dataset == 'whu':
            # 原论文 0.2m；常用发布描述 0.3m。本地 PNG 无地理分辨率元数据，两行均为假设。
            for gsd in (.2, .3):
                kept, threshold = scaled_units(d, scheme, gsd)
                sensitivity[str(gsd)] = dict(counts=counts(d, kept), area_threshold=threshold,
                                             assumed_gsd=gsd, base_scheme=scheme)
        write(part_path(a, 'filter_parts', name), envelope(a, image=name, schemes=rows, sensitivity=sensitivity))
        print(f'filter {a.dataset}/{a.split}/{name}', flush=True)


def summary(rows):
    r = s5.sum_rows(rows)
    for g in (1, 2):
        r[f'object_recall{g}'] = fraction(r[f'hits{g}'], r[f'objects{g}'])
        r[f'pixel_recall{g}'] = fraction(r[f'covered{g}'], r[f'pixels{g}'])
    r['object_recall2_far'] = fraction(r['hits2_far'], r['objects2_far'])
    r['candidates_per_image'] = r['candidates']/r['images']
    return r


def merge(a):
    parts = read_parts(a, 'filter_parts')
    rows = {s: summary([r['schemes'][s] for r in parts]) for s in SCHEMES}
    sensitivity = {g: dict(summary([r['sensitivity'][g]['counts'] for r in parts]),
                          **{k: v for k, v in parts[0]['sensitivity'][g].items() if k != 'counts'})
                   for g in parts[0]['sensitivity']}
    write(a.out / a.dataset / f'filter_{a.split}.json', envelope(a, schemes=rows, sensitivity=sensitivity))


def select(a):
    if (a.dataset, a.split) != ('levir', 'val'):
        raise ValueError('只能用 LEVIR val 选择方案')
    doc = read(a.out / 'levir/filter_val.json')
    if doc['limit'] != a.limit:
        raise ValueError('过滤范围不一致')
    rows = doc['schemes']
    base = rows['F0']['object_recall2']
    if base is None:
        if not a.limit:
            raise ValueError('验证集没有②对象，无法选择方案')
        chosen, decisions = 'F0', {}
        status = 'smoke_no_cell2_use_F0'
    else:
        decisions = {s: dict(loss=base-r['object_recall2'], candidates=r['candidates'],
                            eligible=base-r['object_recall2'] <= .10+1e-12) for s, r in rows.items()}
        chosen = min((s for s in SCHEMES[1:] if decisions[s]['eligible']),
                     key=lambda s: (decisions[s]['candidates'], decisions[s]['loss']), default='F0')
        status = 'frozen'
    result = dict(selection_dataset='levir/val', limit=a.limit, partial=bool(a.limit), scheme=chosen,
                  decisions=decisions, status=status,
                  rule='②对象覆盖相对F0损失≤10个百分点，每图单元最少，同数取损失小；无合格过滤则保留F0')
    path = a.out / 'frozen.json'
    if path.exists() and read(path) != result:
        raise ValueError('已有冻结方案不同，请使用新的输出目录')
    write(path, result)
    print(f'LEViR val frozen: {chosen}', flush=True)


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run', 'merge', 'select', 'all')))
    if a.stage == 'all' and a.shards != 1:
        raise ValueError('all 不接受多分片')
    started = time.monotonic()
    if a.stage in ('run', 'all'): run(a)
    if a.stage in ('merge', 'all'): merge(a)
    if a.stage == 'select': select(a)
    finish(a, 'filter-'+a.stage, started)
