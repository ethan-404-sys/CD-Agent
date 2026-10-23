"""从缓存构造四来源；C 执行 exp4 原函数内的实例匹配代码段。"""
from __future__ import annotations

import ast
import inspect
from functools import lru_cache

from exp8s_common import (np, rt, sam_path, h1_path, crop, LEVELS, level_key,
                         runtime as rt_for, upstream as out_for)
from exp4_candidates import process as exp4_process, upsample


@lru_cache(maxsize=1)
def c_builder():
    # 上游仅有 process，没有可独立调用的 C 函数。抽出原 AST，不复制匹配算法。
    function = ast.parse(inspect.getsource(exp4_process)).body[0]
    start = next(i for i, n in enumerate(function.body)
                 if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'inst' for t in n.targets))
    stop = next(i for i, n in enumerate(function.body[start:], start)
                if isinstance(n, ast.For) and isinstance(n.iter, ast.Call)
                and isinstance(n.iter.func, ast.Attribute) and isinstance(n.iter.func.value, ast.Name)
                and n.iter.func.value.id == 'D_QUANTILES')
    code = ast.Module(body=function.body[start:stop], type_ignores=[])
    return compile(ast.fix_missing_locations(code), inspect.getsourcefile(exp4_process), 'exec')


def instances(z, shape):
    result = []

    def add(source, mask, score, extra=None):
        result.append((source, crop(mask), float(score), extra or {}))

    namespace = {'np': np, 'z': z, 'dist': upsample(z['dist'], shape), 'add': add}
    exec(c_builder(), namespace)
    return result


def c2_scores(dataset, name):
    prob = rt_for(dataset).probability(dataset, name, out_for(dataset))
    pred = prob > .5
    values = []
    with np.load(sam_path(dataset, name), allow_pickle=False) as z:
        for sub, obj, score, _ in instances(z, prob.shape):
            x0, y0, x1, y1 = obj['bbox']
            if (sub == 'C2' and obj['area'] >= rt.e4.MIN_CAND
                    and pred[y0:y1, x0:x1][obj['mask']].mean() <= rt.e4.MAX_PRED_FRAC):
                values.append(score)
    return values


def generate(dataset, name, prob, cut, h1_available):
    """返回每档新增像素候选和面积过滤计数，不使用真值生成候选。"""
    base = prob > .5
    output, dropped = {}, {}

    def retain(source, level, rows):
        key = level_key(source, level)
        objects, small, selected = [], 0, 0
        for original_id, (original, score, metadata) in enumerate(rows):
            selected += 1
            x0, y0, x1, y1 = original['bbox']
            residual = original['mask'] & ~base[y0:y1, x0:x1]
            if residual.sum() < 20:
                small += 1
                continue
            obj = crop(residual, source=source, sources=[source], level=level,
                       score=float(score), original_id=original_id,
                       members=[len(objects)], **metadata)
            a, b, c, d = obj['bbox']
            obj['bbox'] = [a+x0, b+y0, c+x0, d+y0]
            objects.append(obj)
        output[key] = objects
        dropped[key] = dict(selected_before_area_filter=selected, discarded_new_lt20=small,
                            retained_before_dedup=len(objects))

    with np.load(sam_path(dataset, name), allow_pickle=False) as z:
        a, b = z['cls_t1'], z['cls_t2']
        if a.shape != prob.shape or b.shape != prob.shape:
            raise ValueError(f'{dataset}/{name}: SAM3 shape mismatch')
        binary = ((a == 5) != (b == 5)) if dataset in ('levir', 'whu') else ((a > 0) & (b > 0) & (a != b))
        conf = z['conf_t1'].astype(np.float32) * z['conf_t2'].astype(np.float32)
        b_rows = [(crop(m), s, {}) for m, s in rt.components(binary, conf)]
        c_rows = [(m, s, dict(subsource=sub, **meta)) for sub, m, s, meta in instances(z, prob.shape)
                  if sub == 'C1' or cut is None or s >= cut]
        for source, rows in [('B', b_rows), ('C', c_rows)]:
            for level in LEVELS[source]:
                retain(source, level, ((m, s, meta) for m, s, meta in rows if m['area'] >= level))
    for threshold in LEVELS['G']:
        retain('G', threshold, ((crop(m), s, {}) for m, s in rt.components(prob > threshold, prob)))
    if h1_available:
        with np.load(h1_path(dataset, name), allow_pickle=False) as z:
            packed_masks, h1_scores = z['h1_masks'], z['h1_scores']
            if tuple(z['shape']) != prob.shape or len(packed_masks) != len(h1_scores):
                raise ValueError(f'{dataset}/{name}: H1 shape or count mismatch')
            order = np.argsort(-h1_scores, kind='stable')
            for k in LEVELS['H1']:
                ids = range(len(order)) if k is None else order[:k]
                retain('H1', k, ((crop(np.unpackbits(packed_masks[i], count=prob.size).reshape(prob.shape).astype(bool)),
                                  h1_scores[i], {}) for i in ids))
    return output, dropped
