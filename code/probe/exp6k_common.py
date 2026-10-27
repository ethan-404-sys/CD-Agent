"""exp6k 的只读缓存接口、文件格式和共享元数据。"""
from __future__ import annotations
import ast
import csv
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'wyh/CD-Agent/code/probe'
CACHE = HERE.parent / 'batch48/run'
SEED = 20261027
STRATA = ('pos2', 'pos_other', 'hard_neg', 'rand_neg')
sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
sys.path.insert(0, str(PROBE))
sys.path.insert(0, str(HERE.parent / 'batch43'))
import numpy as np
from PIL import Image
import exp8s3_common as upstream
from common import DATASETS, load_rgb, load_label

# 原始编码从数据目录读取；第 6 类遵照 TASK 的显示名称，另存原标签。
ZH = {'non-change': '无变化（未提供地类）', 'low vegetation': '低矮植被',
      'N.V.G surface': '非植被地表', 'tree': '树木', 'water': '水体',
      'building': '建筑', 'playground': '运动场'}


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE) or path.is_relative_to(HERE / 'consult'):
        raise ValueError(f'输出必须在 batch49 内且不能写 consult/: {path}')
    return path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def write_csv(path, rows, fields=None):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else ['dataset', 'split', 'image', 'cid']
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False, allow_nan=False) for k in fields})
    tmp.replace(path)


def read_csv(path):
    with Path(path).open(encoding='utf-8', newline='') as f:
        return [{k: json.loads(v) for k, v in row.items()} for row in csv.DictReader(f)]


def inventory(dataset, split, masks=False):
    keys = ['id', 'image_id', 'images', 'shapes', 'bbox', 'label', 'precision', 'majority',
            'novel', 'dscore', 'm1score']
    if masks:
        keys += ['mask_data', 'mask_offsets']
    with np.load(CACHE / dataset / 'units' / f'{split}.npz', allow_pickle=False) as z:
        if str(z['dataset']) != dataset or str(z['split']) != split or int(z['limit']) != 0:
            raise ValueError('需要 batch48 完整冻结缓存')
        return {k: z[k] for k in keys}


def unpack(z, j):
    x0, y0, x1, y1 = map(int, z['bbox'][j])
    lo, hi = z['mask_offsets'][j:j+2]
    return np.unpackbits(z['mask_data'][lo:hi], count=(x1-x0)*(y1-y0)).reshape(y1-y0, x1-x0).astype(bool)


def image_indices(z, i):
    lo, hi = np.searchsorted(z['image_id'], [i, i + 1])
    return range(int(lo), int(hi))


def source_details(dataset, split, name):
    data = read(upstream.pool_dir(dataset, split) / 'images' / (Path(name).stem + '.json'))
    if data['image'] != name:
        raise ValueError('候选来源图名不一致')
    return {int(r['unit_id']): r for r in data['units']['U5_x4']['details']}


def rgb_pair(dataset, split, name):
    return [load_rgb(upstream.raw_dataset(dataset, split), name, view) for view in ('t1', 't2')]


def semantics(name):
    root = DATASETS['second']['root']
    return [np.asarray(Image.open(root / folder / name)) for folder in ('slabel1', 'slabel2')]


def semantic_spec():
    root = DATASETS['second']['root']
    codes, colors = read(root / 'cls_gray.json'), read(root / 'cls_rgb.json')
    if set(codes) != set(ZH) or sorted(codes.values()) != list(range(7)):
        raise ValueError('SECOND 类别配置发生变化')
    names = {v: ZH[k] for k, v in codes.items()}
    original = {v: k for k, v in codes.items()}
    palette = np.zeros((7, 3), np.uint8)
    for k, v in codes.items():
        palette[v] = colors[k]
    return names, original, palette


def distribution(values):
    names, original, _ = semantic_spec()
    counts = np.bincount(values.ravel(), minlength=7)
    if len(counts) != 7:
        raise ValueError('SECOND 标签超出已知编码')
    return [dict(code=int(i), name=names[int(i)], original=original[int(i)],
                 fraction=float(counts[i] / counts.sum()))
            for i in np.argsort(-counts, kind='stable') if counts[i]]


def describe(hist):
    return '、'.join(f"{r['name']} {r['fraction']:.0%}" for r in hist[:3])


def area_bin(n):
    return '<300' if n < 300 else '300–1000' if n < 1000 else '1000–3000' if n < 3000 else '≥3000'


def inputs_for(out, row):
    stem = f"{Path(row['image']).stem}_{row['unit_id']}"
    root = local(out / row['dataset'] / 'render' / row['split'])
    keys = ['t1', 't2', 'full_t1', 'full_t2', 'thumb']
    if row['split'] == 'train':
        keys += ['mask']
        if row['dataset'] == 'second':
            keys += ['sem1', 'sem2']
    return {key: str(root / f'{stem}_{key}.jpg') for key in keys}


def sample_row(out, row):
    result = dict(row, rationale='', pseudo_type='', weight=1.0)
    result['inputs'] = inputs_for(out, row)
    # val/test 的真值语义只留在离线 table，查询清单不携带。
    if row['split'] != 'train':
        for key in list(result):
            if key.startswith('semantic_'):
                del result[key]
        result['semantic_text'] = ''
        result['change_kind'] = ''
    return result


def table_path(a, split=None):
    return a.out / a.dataset / f'table_{split or a.split}.csv'


def table_rows(a, split=None):
    path = table_path(a, split)
    meta = read(path.with_suffix('.json'))
    if meta['limit'] != a.limit:
        raise ValueError('table 的 --limit 与当前命令不一致；冒烟与正式目录必须分开')
    return read_csv(path)


def manifest_path(a, kind):
    return a.out / f'exp6k_{kind}_{a.dataset}.json'


def key(row):
    return row['image'], row['cid']


def load_crop_functions():
    # 直接执行 exp6j 的三个纯函数，避免其 common 模块把缓存目录设到 batch45。
    path = HERE.parent / 'batch45/exp6j_render.py'
    tree = ast.parse(path.read_text())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name in ('crop_box', 'edge', 'render_pair')]
    if len(selected) != 3:
        raise ValueError('exp6j 渲染函数接口发生变化')
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['crop_box'], namespace['render_pair']
