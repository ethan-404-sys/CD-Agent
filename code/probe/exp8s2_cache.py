"""只加载上游已核实的纯缓存函数；不执行 batch39 的模型依赖导入链。"""
from __future__ import annotations

import ast
import inspect
import itertools
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
HERE = Path(__file__).resolve().parent
STAGING = HERE.parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'Chang-Agent/probe'
sys.path.insert(0, str(STAGING / 'batch40'))
import exp8s2_judge as judge
import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw
import common
import objects
import exp4_candidates as e4

OUT = common.OUT
EIGHT = np.ones((3, 3), bool)
read_json, write_json = judge.read_json, judge.write_json


def source_namespace(path, names, **dependencies):
    """按名称编译原 AST 定义，保留原文件行号；不执行文件级 import/启动代码。"""
    tree = ast.parse(path.read_text(), filename=str(path))
    selected, found = [], set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defines = {node.name}
        elif isinstance(node, ast.Assign):
            defines = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        else:
            continue
        if defines & set(names):
            selected.append(node)
            found |= defines
    if set(names) - found:
        raise ValueError(f'{path}: missing source definitions {set(names) - found}')
    namespace = dict(__name__=path.stem + '_cache', np=np, ndimage=ndimage, Path=Path,
                     read_json=read_json, write_json=write_json, **dependencies)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


def load_objects(path):
    masks, shape = judge.load_units(path.with_name(path.name + '.npz'))
    metadata = read_json(path.with_name(path.name + '.json'))
    if len(masks) != len(metadata):
        raise ValueError(f'{path}: mask/metadata count mismatch')
    return [{**m, **v} for m, v in zip(masks, metadata)], shape


@lru_cache(maxsize=1)
def route_api():
    """复用 batch39 full_masks/rk/missed_components/best_candidates/render 及其纯函数依赖。"""
    sys.path.insert(0, str(STAGING / 'batch19'))
    import exp8_data
    exp8_data.register(common)
    legacy = source_namespace(STAGING / 'batch17/exp8_common.py', ['crop_mask'])
    rt = source_namespace(STAGING / 'batch18/exp8_runtime.py', ['make_object', 'sam3_path'],
                          legacy=legacy, OUT=OUT)
    rt.components = source_namespace(PROBE / 'exp6a_pieces.py', ['components'], label=objects.label).components
    rt.e4 = e4

    def sam_path(ds, name):
        root = OUT / ('exp8_batch19' if ds.startswith('sysu') else 'exp8_batch18')
        return exp8_data.sam3_path(name, root) if ds == 'sysu_val' else rt.sam3_path(ds, name, root)

    shared = source_namespace(STAGING / 'batch30/exp8s_common.py',
                              ['SOURCES', 'LEVELS', 'DEFAULTS', 'level_key', 'scores', 'h1_path'], ROOT=OUT)
    overlap = source_namespace(STAGING / 'batch17/exp8_dedup_viz.py', ['overlap']).overlap
    s34 = source_namespace(STAGING / 'batch32/exp8s_step34.py',
                           ['OUT', 'BASE', 'VALIDATION', 'KS', 'pixels', 'direct_groups', 'merge_group', 'annotate'],
                           overlap=overlap)
    s34.BASE.update(levir=OUT / 'exp8s_batch30', whu=OUT / 'exp8s_batch30')

    def image_list(ds):
        if ds in judge.DATASETS:
            return judge.image_list(ds)
        names = read_json(s34.BASE[ds] / 'interim/exp8s' / ds / 'manifest.json')['config']['images']
        if names != common.names(ds):
            raise ValueError(f'{ds}: image inventory differs')
        return names

    s34.image_list = image_list
    s5 = source_namespace(STAGING / 'batch36/exp8s_step5.py',
                          ['CELLS', 'GRID', 'COMBOS', 'DEFAULT_COMBO', 'Image'], s34=s34, OUT=OUT,
                          SOURCES=shared.SOURCES, DEFAULTS=shared.DEFAULTS, itertools=itertools,
                          load_objects=load_objects, level_key=shared.level_key)
    sources = source_namespace(STAGING / 'batch30/exp8s_sources.py', ['c_builder', 'instances'],
                               ast=ast, inspect=inspect, lru_cache=lru_cache,
                               exp4_process=e4.process, upsample=e4.upsample, crop=rt.make_object)
    s6 = source_namespace(STAGING / 'batch37/exp8s_step6.py',
                          ['raw_rows', 'union', 'Image6', 'items'], s34=s34, s5=s5, rt=rt,
                          SOURCES=shared.SOURCES, load_objects=load_objects, level_key=shared.level_key,
                          crop=rt.make_object, instances=sources.instances, sam_path=sam_path, h1_path=shared.h1_path)
    s6.CELLS, s6.crop, s6.sam_path = s5.CELLS, rt.make_object, sam_path
    touches = source_namespace(STAGING / 'batch34/exp8s_cells.py', ['touches']).touches
    b38 = source_namespace(STAGING / 'batch38/exp8s_cell_examples.py',
                           ['SIDE', 'K3', 'COLORS', 'STYLE', 'components', 'upscale', 'contour', 'blend', 'crop_box'],
                           Image=Image)
    b38.touches = touches
    return source_namespace(STAGING / 'batch39/exp8s2_route.py',
                             ['DATASETS', 'SPLIT', 'GROUPS', 'GROUP_NAMES', 'ROUTES', 'ROUTE_NAMES',
                              'MIN_PIECE', 'EIGHT', 'SEED', 'CAND_COLOR', 'rule_name', 'route',
                              'raw_rows', 'full_masks', 'rk', 'missed_components', 'best_candidates',
                              'Context', 'render'],
                             s34=s34, s5=s5, s6=s6, rt=rt, b38=b38, common=common,
                             gt_label=objects.gt_label, scores=shared.scores, Image=Image, ImageDraw=ImageDraw)


def local_output(path):
    path = Path(path).resolve()
    if path == HERE or not path.is_relative_to(HERE):
        raise ValueError('--out 必须是 batch41 内的专用子目录')
    return path


def split_parts(obj, base, dilated):
    """返回 c′、≥20 的独立块与审计；连通域按 scipy 行优先标签顺序展开。"""
    x0, y0, x1, y1 = obj['bbox']
    mask = obj['mask']
    novel = mask & ~base[y0:y1, x0:x1]
    labels, n = ndimage.label(novel, EIGHT)
    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    touching = np.zeros(n + 1, bool)
    touching[np.unique(labels[dilated[y0:y1, x0:x1]])] = True
    isolated = ~touching
    isolated[0] = False
    remaining = mask & ~isolated[labels]

    def crop(local, dx=0, dy=0):
        ys, xs = np.nonzero(local)
        if not len(xs):
            return None
        a, b, c, d = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        return dict(bbox=[x0+dx+a, y0+dy+b, x0+dx+c, y0+dy+d],
                    mask=local[b:d, a:c].copy(), area=int(len(xs)))

    detached = []
    for ident, sl in enumerate(ndimage.find_objects(labels), 1):
        if isolated[ident] and sizes[ident] >= 20:
            detached.append(crop(labels[sl] == ident, sl[1].start, sl[0].start))
    kept = crop(remaining)
    audit = dict(detached_count=len(detached), remaining_empty=kept is None,
                 dropped_components=int((isolated & (sizes < 20)).sum()),
                 dropped_px=int(sizes[isolated & (sizes < 20)].sum()),
                 detached_px=sum(o['area'] for o in detached), novel_px=int(novel.sum()))
    if (0 if kept is None else kept['area']) + audit['detached_px'] + audit['dropped_px'] != obj['area']:
        raise ValueError('split area conservation failed')
    return kept, detached, audit
