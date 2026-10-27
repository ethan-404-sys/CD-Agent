"""回读真实输出，验证掩码/标签/冻结方案/按图隔离/抽样权重与数据库。"""
from exp8s8_icl_common import *
import sqlite3
import time
from sklearn.metrics import roc_auc_score, average_precision_score


def verify(a):
    total = 0
    checks = []
    for ds, split in SCOPES:
        a.dataset, a.split = ds, split
        z = load_units(a)
        for i, name in enumerate(z['images']):
            d = load(ds, split, str(name))
            expected = {u['id']: u for u in s6.units(d, frozen(a))}
            js = image_rows(z, i)
            if set(z['id'][js]) != set(expected):
                raise ValueError('冻结过滤和单元表ID不一致')
            for j in js:
                u = unpack(z, j)
                x0, y0, x1, y1 = u['bbox']
                mask = u['mask']
                if mask.sum() != z['novel'][j] or (d['base'][y0:y1,x0:x1] & mask).any():
                    raise ValueError('packed新增掩码面积或D互斥检查失败')
                precision = float(d['gt'][y0:y1,x0:x1][mask].mean())
                label = 1 if precision >= .5 else 0 if precision < .1 else -1
                if label != z['label'][j] or not np.isclose(precision,z['precision'][j],atol=1e-12):
                    raise ValueError('packed掩码重算标签或精度不符')
                ref = expected[u['id']]
                rx0, ry0, rx1, ry1 = ref['bbox']
                actual = np.zeros_like(ref['mask'])
                actual[y0-ry0:y1-ry0,x0-rx0:x1-rx0] = mask
                if not np.array_equal(actual,ref['mask']) or not np.isclose(z['dscore'][j],ref['dscore'],atol=1e-12):
                    raise ValueError('紧致掩码或D分数不符')
            total += len(js)
        cells = read(a.out/ds/f'cells_{split}.json')
        if sum(cells[f'{g}_pixels'] for g in CELLS) != cells['fn_pixels']:
            raise ValueError('四格没有精确分割FN')
        rank = read(a.out/ds/f'rank_{split}.json')
        if rank['scheme'] != frozen(a) or rank['limit'] != a.limit:
            raise ValueError('评估与冻结方案范围不符')
        included = np.isin(z['images'][z['image_id']],rank['evaluated_images'])
        for method, score in dict(D=z['dscore'],oracle=z['precision']).items():
            take = included & (z['label']>=0)
            y, values = z['label'][take],score[take]
            if len(np.unique(y))==2:
                row = rank['ranking'][method]['all']
                if not np.isclose(row['auc'],roc_auc_score(y,values)) or not np.isclose(row['ap'],average_precision_score(y,values)):
                    raise ValueError('排序指标与独立sklearn实现不一致')
            hits = [rank['budget'][method][str(k)]['hits2'] for k in KS]
            if hits != sorted(hits):
                raise ValueError('预算对象命中随k增大而减少')
        if rank['budget']['D']['all'] != rank['budget']['oracle']['all']:
            raise ValueError('全部预算与排序无关，两方法结果应相同')
        if ds=='whu':
            splitdoc = read(a.out/ds/'icl_split.json')
            if rank['excluded_images'] != splitdoc['excluded_images']:
                raise ValueError('WHU评估排除图不匹配')
        checks.append(f'{ds}/{split}: masks, labels, cells, AUC/AP, budgets')
    for ds in ('levir','whu'):
        a.dataset, a.split = ds, 'val' if ds=='levir' else 'test'
        kinds = ('demos','dev','test') if ds=='levir' else ('demos','test')
        collections = {k:read(manifest_path(a,k)) for k in kinds}
        validate_sets(collections,files=True)
        for kind in kinds:
            if kind=='demos': continue
            doc = read(a.out/ds/f'{kind}_sampling.json')
            for stratum in STRATA:
                selected = [r for r in collections[kind] if r['stratum']==stratum]
                row = doc['strata'][stratum]
                if len(selected)!=row['selected']:
                    raise ValueError('抽样数不符')
                if selected and not np.isclose(sum(r['weight'] for r in selected),row['population']):
                    raise ValueError('层权重和不等于层总体数')
            if kind=='dev' and (doc['sampled_candidates'] != doc['eligible_candidates'] or any(r['weight']!=1 for r in collections[kind])):
                raise ValueError('开发集必须全量、weight=1')
        for rows in collections.values():
            for row in rows:
                for k in ('t1','t2'):
                    with Image.open(row['inputs'][k]) as im:
                        if im.size != (384,384): raise ValueError('A近景尺寸错误')
                if row['role']=='demo':
                    for k in ('full_t1','full_t2','mask'):
                        with Image.open(row['inputs'][k]) as im:
                            if max(im.size)>1024: raise ValueError('B整图超过1024')
        receipt = read(a.out/ds/'database_receipt.json')
        db = sqlite3.connect(f"file:{receipt['path']}?mode=ro",uri=True)
        try:
            for kind in kinds:
                count = db.execute('SELECT COUNT(*) FROM sample_set_item WHERE set_id=(SELECT id FROM sample_set WHERE name=?)',
                                   (f'exp8s8_{kind}_{ds}',)).fetchone()[0]
                if count!=len(collections[kind]): raise ValueError('库成员数不符')
            for lang in ('en','zh'):
                prompt = (HERE/'prompts'/f'{ds}_sys_v1_{lang}.txt').read_text().strip()
                if not db.execute('SELECT 1 FROM prompt_version WHERE name=? AND text=?',(f'{ds}_sys_v1_{lang}',prompt)).fetchone():
                    raise ValueError('提示词未正确读入')
        finally:
            db.close()
        checks.append(f'{ds}: image isolation, weights, render sizes, database, prompts')
    write(a.out/'verification.json',dict(limit=a.limit,partial=bool(a.limit),status='passed',units_checked=total,checks=checks,
          negative_annotations='pending' if a.limit else 'finalized'))
    print(f'verified {total} real units',flush=True)


if __name__=='__main__':
    a = setup(parser(__doc__,('run',)))
    started = time.monotonic()
    verify(a)
    finish(a,'verify',started)
