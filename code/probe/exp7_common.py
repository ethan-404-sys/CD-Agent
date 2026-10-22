"""Shared confirmation, raw SAM cache and CPU composition for experiments 7/2c/4c."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from nd_compat import CONNECTIVITY, ndimage

from common import OUT, load_pred, names
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import Pool, SOURCES, verifier_split, write_json
from render import crop_box

ROOT = OUT / 'exp7'
COMBOS = {'BG': ('B', 'G0.05'), 'BGH1': ('B', 'G0.05', 'H1')}
THRESHOLD = 0.819681704044342
MERGES = ('replace', 'clip3', 'clip5', 'clip10', 'union')
PROMPTS = tuple(f'P{i}' for i in range(1, 8))
CONSERVATIVE = [
    '2c/4c 固定 SAM3/P2/t2；裁块边长 max(3×外接框长边,128)，裁至影像范围。',
    '同组合确认切块先取并集再按8连通域分组；原检测器像素始终保留，4c额外保留原去误检规则。',
    '外扩使用欧氏圆盘；P3正点按距离变换降序且间隔至少5像素，不足时使用剩余最远像素；负点按概率升序、同分按行列。',
    'P4 为 P2 加 ±10 低分辨率掩码提示；P6 用 P2 多掩码，按与确认区域的IoU选，不用真值。',
    'P7 对漏入和多出像素分别在最大8连通分歧区内取最深点，最多新增一正一负，并传回第一轮logits。',
    '高分期逐区域选择；同分选t2；两期耗时均计入。',
    'r2每区域/期/裁块编码一次，全部请求提示共享嵌入；P7两轮共用嵌入。SAM每图秒数 = 编码 + 该提示解码（含提示构造与选掩码）；推理秒/图另加CPU读取和合并。',
    '共享编码在NPZ的encode_seconds逐区域单列，seconds为该提示解码；各提示重复保存同一编码计时便于独立读取，多提示作业总时间只计一次编码。旧缓存无encode_seconds时seconds沿用编码加解码总时间。',
    'SAM与真值IoU：与确认区域有正像素重叠的二值真值目标之并集作参照，真值面积包含裁块外部分；无重叠时IoU为0并单列。',
    '面积桶按参照真值并集面积 <100、[100,1000]、>1000；无匹配另列。变化类型按参照真值中最多像素转移分类。',
    'SECOND转移优先级：5→5改建、非5→5新建、5→非5拆除、1↔3植被、2↔{1,3}地表植被、其余含4水体；其余/0类为其他或未知。',
    '选择只用297张验证图：相对P0 F1下降≤0.003，再按BF2降序、粗糙度升序、F1降序、配置名排序；同时记录BF2提高且粗糙度降低的标志。',
    '形态学2b按原规范处理最终整图，会改变原检测器像素；其余精修与软边界均只处理补漏。',
    'SECOND类别编号：1低矮植被、2非植被地表、3树木、4水体、5建筑、6运动场；最多像素转移并列时取编号最小者。',
    '导向滤波可用时固定t2 RGB/255、radius=4、epsilon=.01、输出>.5，再与确认区外扩5像素相交并入base；缺cv2.ximgproc则未做。',
    'P4采用双线性缩放；P7无分歧时仍执行第二轮；P3点数不足时只用现有像素，P5去掉框并保留P3点。',
    '每个模型/提示族仅冻结一个合格的期/合并方案，图中前三名同样由验证集确定；不足三种时留空。',
    '同一真值目标可被多个确认区域引用；SAM IoU为区域等权分布，不视为独立目标micro统计。',
]


def read_json(path):
    return json.loads(Path(path).read_text())


def exclusive_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def disk(radius):
    y, x = np.ogrid[-radius:radius+1, -radius:radius+1]
    return x*x + y*y <= radius*radius


def expand(mask, radius):
    return ndimage.binary_dilation(mask, structure=disk(radius))


def image_names(dataset):
    if dataset == 'second_cv':
        images = verifier_split()[1]
        if len(images) != 297:
            raise ValueError('Expected exactly 297 verifier validation images')
        return images
    return names(dataset)


def load_scores(directory, pool, expected_run=None):
    """Score-only reader from exp6f_common; avoids its scipy/skimage feature imports."""
    directory = Path(directory)
    path = directory / f'scores_{pool.dataset}.npy'
    indexed = directory / f'scores_{pool.dataset}.npz' if path.exists() else directory / pool.dataset / 'scores.npz'
    with np.load(indexed, allow_pickle=False) as z:
        idx = pool.feature_indices(z['image'], z['piece_id'])
        if len(idx) != len(pool) or str(z['dataset']) != pool.dataset:
            raise ValueError(f'{indexed}: incomplete pool or wrong dataset')
        if expected_run is not None and str(z['run_id']) != expected_run:
            raise ValueError(f'{indexed}: different run')
        values = np.load(path, allow_pickle=False) if path.exists() else z['score']
        if path.exists() and not np.array_equal(idx, np.arange(len(pool))):
            raise ValueError('NPY score sidecar must have exact Pool row order')
        if (values.shape != (len(pool),) or np.isinf(values).any()
                or np.any((values < 0) | (values > 1))):
            raise ValueError(f'{indexed}: invalid probabilities')
        scores = np.empty(len(pool), np.float32)
        scores[idx] = values
        selected = (pool.image_mask(verifier_split()[1]) if pool.dataset == 'second_cv'
                    else np.ones(len(pool), bool))
        if not np.isfinite(scores[selected]).all():
            raise ValueError(f'{indexed}: incomplete evaluation probabilities')
    return scores


class Confirmations:
    def __init__(self, dataset, setting, combo='BGH1'):
        self.dataset, self.setting, self.combo = dataset, setting, combo
        self.pool = Pool(dataset)
        self.images = image_names(dataset)
        self.scores = None
        self.threshold = None
        self.run_id = None
        if setting == 'V':
            directory = OUT / 'exp6/verifier/none'
            calibration = read_json(directory / 'thresholds_second_cv.json')
            self.threshold = calibration['recall']['B+G0.05+H1']['threshold']
            if self.threshold != THRESHOLD or combo != 'BGH1':
                raise ValueError('V requires the exact frozen V1 BGH1 threshold')
            self.run_id = calibration['run_id']
            self.scores = load_scores(directory, self.pool, self.run_id)

    def accepted(self, name):
        base = load_pred(self.dataset, DETECTOR[self.dataset], name)
        union = np.zeros_like(base)
        masks = load_piece_masks(self.dataset, name)
        sl = self.pool.slices[name]
        for i in range(sl.start, sl.stop):
            if SOURCES[self.pool.source[i]] not in COMBOS[self.combo]:
                continue
            accept = (self.pool.label[i] == 1 if self.setting == 'O'
                      else self.scores[i] >= self.threshold)
            if accept:
                (x0, y0, x1, y1), local = masks[int(self.pool.piece_id[i])]
                union[y0:y1, x0:x1] |= local
        return base, union


def regions(union):
    labels, _ = ndimage.label(union, CONNECTIVITY)
    for index, sl in enumerate(ndimage.find_objects(labels), 1):
        y, x = sl
        bbox = [x.start, y.start, x.stop, y.stop]
        crop = list(crop_box(bbox, union.shape, 3.0, 128))
        x0, y0, x1, y1 = crop
        local = labels[y0:y1, x0:x1] == index
        yield index, bbox, crop, local


def cache_dir(root, dataset, setting, combo, model, prompt, view):
    return Path(root) / 'raw' / dataset / setting / combo / model / prompt / view


def cache_config(context, model, prompt, view):
    return dict(version=2, dataset=context.dataset, setting=context.setting, combo=context.combo,
                model=model, prompt=prompt, view=view, threshold=context.threshold,
                run_id=context.run_id, crop_scale=3.0, crop_min=128, connectivity=8)


def save_raw(path, arrays):
    temporary = path.with_suffix('.tmp.npz')
    np.savez_compressed(temporary, **arrays)
    temporary.replace(path)


def load_raw(root, dataset, setting, combo, model, prompt, view, name):
    path = cache_dir(root, dataset, setting, combo, model, prompt, view) / (Path(name).stem + '.npz')
    with np.load(path, allow_pickle=False) as z:
        return {key: z[key] for key in z.files}


def selected_raw(root, dataset, setting, combo, model, prompt, view, name):
    views = ('t1', 't2') if view == 'best' else (view,)
    data = {v: load_raw(root, dataset, setting, combo, model, prompt, v, name) for v in views}
    ref = data[views[0]]
    if len(views) == 2:
        if not np.array_equal(data['t1']['crops'], data['t2']['crops']):
            raise ValueError('t1/t2 region crops differ')
        for i in range(len(ref['crops'])):
            if not np.array_equal(data['t1'][f'region_{i}'], data['t2'][f'region_{i}']):
                raise ValueError('t1/t2 confirmed regions differ')
    for i, crop in enumerate(ref['crops']):
        chosen = ('t1' if float(data['t1']['scores'][i]) > float(data['t2']['scores'][i]) else 't2') if view == 'best' else view
        yield dict(crop=crop, region=ref[f'region_{i}'], mask=data[chosen][f'mask_{i}'],
                   score=float(data[chosen]['scores'][i]), view=chosen,
                   seconds=sum(float(data[v]['seconds'][i]) +
                               (float(data[v]['encode_seconds'][i]) if 'encode_seconds' in data[v] else 0.0)
                               for v in views))


def compose(shape, items, merge):
    added = np.zeros(shape, bool)
    for item in items:
        x0, y0, x1, y1 = item['crop']
        region, mask = item['region'], item['mask']
        if merge.startswith('clip'):
            mask = mask & expand(region, int(merge[4:]))
        elif merge == 'union':
            mask = mask | region
        elif merge != 'replace':
            raise ValueError(merge)
        added[y0:y1, x0:x1] |= mask
    return added


def verify_regions(union, items):
    actual = np.zeros_like(union)
    for item in items:
        x0, y0, x1, y1 = item['crop']
        actual[y0:y1, x0:x1] |= item['region']
    if not np.array_equal(actual, union):
        raise ValueError('Raw cache confirmed region differs from the current pool')


def transition_name(pair):
    a, b = pair
    if a not in range(1, 7) or b not in range(1, 7):
        return '未知'
    if a == b == 5:
        return '改建'
    if b == 5:
        return '新建'
    if a == 5:
        return '拆除'
    if {a, b} == {1, 3}:
        return '植被互变'
    if a != b and {a, b} <= {1, 2, 3} and 2 in (a, b):
        return '地表与植被互变'
    if 4 in (a, b):
        return '水体'
    return '其他'


def sam_diagnostics(gt, items, classes=None):
    from metrics_cd import ratio
    labels, _ = ndimage.label(gt, CONNECTIVITY)
    rows = []
    for index, item in enumerate(items):
        x0, y0, x1, y1 = item['crop']
        ids = np.unique(labels[y0:y1, x0:x1][item['region']])
        target = np.isin(labels, ids[ids > 0])
        area = int(target.sum())
        local = target[y0:y1, x0:x1]
        inter = int((item['mask'] & local).sum())
        union = int(item['mask'].sum()) + area - inter
        kind, pair = '无匹配', None
        if area:
            kind = '未知'
            if classes is not None:
                pairs, counts = np.unique(np.stack([c[target] for c in classes], axis=1),
                                          axis=0, return_counts=True)
                pair = pairs[int(counts.argmax())].astype(int).tolist()
                kind = transition_name(pair)
        bucket = ('no_match' if area == 0 else 'lt100' if area < 100
                  else '100_to_1000' if area <= 1000 else 'gt1000')
        rows.append(dict(region=index, area=area, bucket=bucket, kind=kind, transition=pair,
                         iou=ratio(inter, union), intersection=inter, union=union,
                         score=item['score'], view=item['view'], seconds=item['seconds']))
    return rows


def diagnostic_summary(rows):
    from metrics_cd import distribution
    rows = list(rows)
    result = {'all': distribution(r['iou'] for r in rows)}
    for key, values in (('bucket', ('lt100', '100_to_1000', 'gt1000', 'no_match')),
                        ('kind', ('新建', '拆除', '改建', '植被互变', '地表与植被互变', '水体', '其他', '未知', '无匹配'))):
        result[key] = {value: distribution(r['iou'] for r in rows if r[key] == value) for value in values}
    return result
