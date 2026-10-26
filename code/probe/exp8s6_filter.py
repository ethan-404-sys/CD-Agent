"""实验 8 补充 6 第 1 部分：放宽碎片过滤（只读缓存，不跑模型、不训练）。

阶段：run（逐图统计，可分片）→ merge（按划分汇总）→ select（验证选方案，冻结）。
方案都作用于 batch43 的补漏组单元（1d 冻结规则，单元自身 r < 0.1）：
  F0           原样；
  F2           exp8s5 对照：新增像素 3×3 开运算后 ≥ 100；
  F3A<A>       去细条（新增像素最大内切圆半径 ≥ 2）后，新增面积 ≥ A，A ∈ {0,100,200,300,500,1000}；
  F3L300       去细条后，新增像素中最大 8 连通块 ≥ 300（额外方案：单元的新增像素常是多块碎片，
               总面积够大不代表有一块“看得清的大块”，按最大块判断更贴近“找大块”）。
② 对象覆盖口径同 exp8s5：按 GT 目标去重，D 与候选新增并集覆盖目标 ≥ 50% 算命中。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'batch46'))
from exp8s5_common import *  # noqa: E402,F401
import exp8s5_common as s5  # noqa: E402


HERE = Path(__file__).resolve().parent  # 星号导入会带进 batch46 的 HERE，这里改回 batch47


def _local(path):
    path = (HERE / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if HERE not in path.parents and path != HERE:
        raise ValueError('Output must be inside batch47')
    return path


s5.local = _local  # 输出写在 batch47，而不是 batch46

AREAS = (0, 100, 200, 300, 500, 1000)
SCHEMES = ('F0', 'F2') + tuple(f'F3A{a}' for a in AREAS) + ('F3L300',)
FAR = 5
LOSS_MAX = .10


def keep(u, scheme):
    """返回过滤后的新增掩码；None 表示整单元丢弃。"""
    mask = u['mask']
    if scheme == 'F0':
        return mask
    if scheme == 'F2':
        mask = ndi.binary_opening(mask, structure=EIGHT)
        return mask if mask.sum() >= 100 else None
    if ndi.distance_transform_edt(np.pad(mask, 1)).max() < 2:
        return None
    if scheme.startswith('F3A'):
        return mask if mask.sum() >= int(scheme[3:]) else None
    labels, n = ndi.label(mask, EIGHT)
    largest = np.bincount(labels.ravel())[1:].max() if n else 0
    return mask if largest >= 300 else None


def units(d, scheme):
    out = []
    for u in d['candidates']:
        mask = keep(u, scheme)
        if mask is None:
            continue
        x0, y0, x1, y1 = u['bbox']
        n = int(mask.sum())
        truth = int((mask & d['gt'][y0:y1, x0:x1]).sum())
        out.append(dict(u, mask=mask, novel=n, positive=bool(n and 2 * truth >= n)))
    return out


def targets2(d):
    """② GT 目标：id → 到 D 的最小棋盘距离（图内无 D 记为 None，按“远”计）。"""
    far = {}
    for r in d['rows']:
        if r['cell'] == 2:
            t = r['target']
            dist = r['distance']
            far[t] = dist if t not in far or far[t] is None or (dist is not None and dist < far[t]) else far[t]
    return far


def object_hits(d, union, ids):
    ga, gb, gl = d['ga'], d['gb'], d['gl']
    added = np.bincount(gl[union], minlength=len(ga))
    ids = np.asarray(sorted(ids), int)
    return int((2 * (gb[ids] + added[ids]) >= ga[ids]).sum()) if len(ids) else 0, len(ids)


def run(a):
    out = a.out / a.dataset / 'filter6' / a.split
    for name in names(a, True):
        d = s5.load(a.dataset, a.split, name)
        far = targets2(d)
        far_ids = [t for t, v in far.items() if v is None or v > FAR]
        res = {}
        for scheme in SCHEMES:
            kept = units(d, scheme)
            union = np.zeros_like(d['gt'])
            for u in kept:
                add_mask(union, u)
            row = dict(coverage(d, union), images=1, candidates=len(kept),
                       positives=sum(u['positive'] for u in kept))
            row['hits2_far'], row['objects2_far'] = object_hits(d, union, far_ids)
            res[scheme] = row
        write(out / (Path(name).stem + '.json'), envelope(a, name, schemes=res))


def summarize(rows):
    r = rates(sum_rows(rows))
    r['object_recall2_far'] = fraction(r['hits2_far'], r['objects2_far'])
    return r


def merge(a):
    folder = a.out / a.dataset / 'filter6' / a.split
    records = [read(folder / (Path(n).stem + '.json')) for n in names(a)]
    rows = {s: summarize([r['schemes'][s] for r in records]) for s in SCHEMES}
    write(a.out / a.dataset / f'filter6_{a.split}.json',
          dict(dataset=a.dataset, split=a.split, limit=a.limit, images=len(records), schemes=rows))


def select(a):
    rows = read(a.out / a.dataset / 'filter6_val.json')['schemes']
    base = rows['F0']['object_recall2']
    decisions = {s: dict(loss=base - r['object_recall2'], candidates_per_image=r['candidates_per_image'],
                         eligible=base - r['object_recall2'] <= LOSS_MAX + 1e-12) for s, r in rows.items()}
    chosen = min((s for s in SCHEMES if decisions[s]['eligible']),
                 key=lambda s: (rows[s]['candidates'], decisions[s]['loss']))
    write(a.out / a.dataset / 'frozen6.json',
          dict(dataset=a.dataset, scheme=chosen, rule='验证 ② 对象覆盖相对 F0 损失 ≤ 10 个百分点，每图单元最少；同数取损失小者',
               decisions=decisions))
    print(a.dataset, 'frozen', chosen, flush=True)


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run', 'merge', 'select')))
    {'run': run, 'merge': merge, 'select': select}[a.stage](a)
