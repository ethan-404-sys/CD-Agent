"""D、oracle 排序与精确新增并集预算；WHU 主评估排除示例图。"""
from exp8s8_common import *
from exp8s7_eval import ranking
import time


def evaluate(a):
    z = load_units(a)
    excluded = []
    if a.dataset == 'whu':
        splitdoc = read(a.out / 'whu/icl_split.json')
        if splitdoc['limit'] != a.limit:
            raise ValueError('ICL 排除图与评估范围不匹配')
        if not a.limit and not splitdoc['finalized']:
            raise ValueError('WHU 正式评估需先完成负例标注与 finalize，固定排除图')
        excluded = splitdoc['excluded_images']
    indices = [i for i, name in enumerate(z['images']) if name not in excluded]
    included = np.isin(z['image_id'], indices)
    remap = {old: new for new, old in enumerate(indices)}
    q = {key: z[key][included] for key in ('id', 'label', 'majority', 'precision', 'dscore')}
    q['image_id'] = np.array([remap[int(i)] for i in z['image_id'][included]], dtype=np.int32)
    q['images'] = z['images'][indices]
    scores = dict(D=q['dscore'], oracle=q['precision'])
    ranks = ranking(q, scores, a.bootstrap)
    budgets = {m: {str(k): dict(hits2=0, objects2=0, selected=0) for k in KS} for m in scores}
    per_image = []
    for i in indices:
        name = str(z['images'][i])
        d = geometry(a.dataset, a.split, name)
        rows = image_rows(z, i)
        units = [unpack(z, j) for j in rows]
        detail = dict(image=name, objects2=len(s6.targets2(d)), methods={})
        for method, values in dict(D=z['dscore'], oracle=z['precision']).items():
            order = np.lexsort((z['id'][rows], -values[rows]))
            union = np.zeros_like(d['gt'])
            last = 0
            detail['methods'][method] = {}
            for k in KS:
                stop = len(rows) if k == 'all' else min(k, len(rows))
                for j in order[last:stop]:
                    s5.add_mask(union, units[int(j)])
                hits, total = s6.object_hits(d, union, s6.targets2(d))
                for key, val in dict(hits2=hits, objects2=total, selected=stop).items():
                    budgets[method][str(k)][key] += val
                detail['methods'][method][str(k)] = hits
                last = stop
        per_image.append(detail)
    for group in budgets.values():
        for row in group.values():
            row.update(object_recall2=fraction(row['hits2'], row['objects2']),
                       selected_per_image=fraction(row['selected'], len(indices)))
    result = envelope(a, scheme=frozen(a), ranking=ranks, budget=budgets, images=len(indices),
                      evaluated_images=q['images'].tolist(), excluded_images=excluded, per_image=per_image,
                      hard_negative_rule='当前评估图中按D取负例前ceil(20%)；并列按image_id、id',
                      evaluation_scope='WHU排除示例图；未知相邻块，可能有相邻切块泄漏' if a.dataset == 'whu' else '完整划分',
                      bootstrap_seed=20261026, bootstrap_replicates=a.bootstrap)
    dest = a.out / a.dataset / f'rank_{a.split}.json'
    if dest.exists():
        raise FileExistsError(f'评估已存在，保留已冻结结果: {dest}')
    write(dest, result)


if __name__ == '__main__':
    p = parser(__doc__, ('run',))
    p.add_argument('--bootstrap', type=int, default=2000)
    a = setup(p)
    if a.bootstrap < 1 or (not a.limit and a.bootstrap != 2000):
        p.error('正式评估固定2000次整群自助法')
    started = time.monotonic()
    evaluate(a)
    finish(a, 'rank', started)
