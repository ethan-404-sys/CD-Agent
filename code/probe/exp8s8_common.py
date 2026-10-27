"""batch50 的路径、真实缓存读取及 batch46/47/48 适配；见 README_batch50.md。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'wyh/CD-Agent/code/probe'
sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
sys.path.insert(0, str(PROBE))
for batch in ('batch43', 'batch46', 'batch47', 'batch48', 'batch49'):
    sys.path.insert(0, str(HERE.parent / batch))
import numpy as np
from scipy import ndimage as ndi
from PIL import Image
from common import DATASETS, OUT, load_label, load_rgb, names as dataset_names
import exp8s5_common as s5
import exp8s6_filter as s6
import exp8s7_common as s7

# 旧模块在导入时设置过其他批次的画图目录，在画图库导入前固定到本目录。
os.environ['MPLCONFIGDIR'] = str(HERE / '.mplconfig')
DATASETS['levir_val'] = {**DATASETS['levir'], 'root': WORKSPACE / 'datasets/LEVIR-CD/val_original'}
B18 = OUT / 'exp8_batch18'
SCOPES = (('levir', 'val'), ('levir', 'test'), ('whu', 'test'))
EXPECTED = {('levir', 'val'): 64, ('levir', 'test'): 128, ('whu', 'test'): 690}
CELLS = (1, 2, 3, 5)
SCHEMES = ('F0', 'F2', 'F3A0', 'F3A100', 'F3A200', 'F3A300', 'F3A500')
KS = (1, 2, 3, 5, 10, 20, 'all')
SEED = 20261027
STRATA = ('pos2', 'pos_other', 'hard_neg', 'rand_neg')
read = s5.read
archive = s5.c.archive
fraction = s5.fraction
unpack = s7.unpack
image_rows = s7.image_rows


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE) or any(path.is_relative_to(HERE / x) for x in ('consult', 'prompts')):
        raise ValueError('产物必须在 batch50 内，不能写 consult/、prompts/')
    return path


def write(path, value):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def save(path, **arrays):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    with tmp.open('wb') as f:
        np.savez_compressed(f, **arrays)
    tmp.replace(path)


def raw_dataset(ds, split):
    if (ds, split) not in SCOPES:
        raise ValueError(f'未知划分 {ds}/{split}')
    return 'levir_val' if (ds, split) == ('levir', 'val') else ds


def pool_dir(ds, split):
    return B18 / 'pool' / raw_dataset(ds, split)


def pool_kind(ds):
    return 'U5_x16' if ds == 'levir' else 'objects_direct_pairs'


def prob_path(ds, split, name):
    root = B18 / 'preds' if split == 'val' else OUT / 'preds'
    return root / raw_dataset(ds, split) / 'schanger_base/prob' / (Path(name).stem + '.npy')


def scope_names(ds, split, limit=0):
    seq = dataset_names(raw_dataset(ds, split))
    if len(seq) != EXPECTED[ds, split]:
        raise ValueError(f'{ds}/{split}: 图数 {len(seq)} 与预期不符')
    return seq[:limit or None]


def names(a, sharded=False):
    seq = scope_names(a.dataset, a.split, a.limit)
    return seq[a.shard::a.shards] if sharded else seq


def raw_pixels(ds, split, name):
    prob = np.load(prob_path(ds, split, name), allow_pickle=False).astype(np.float32)
    gt = load_label(raw_dataset(ds, split), name)
    if prob.shape != gt.shape or not np.isfinite(prob).all():
        raise ValueError(f'{name}: 概率图尺寸或值错误')
    return gt, prob > .5, prob, np.zeros(gt.shape, np.uint8)


# geometry 自行重建四格；old 字段仅作旧缓存审计，本任务没有旧格缓存。
s5.pixels = raw_pixels
s5.c.raw_dataset = raw_dataset
s5.local = local
s6.HERE = HERE
geometry = s5.geometry


def source_details(ds, split, name):
    doc = read(pool_dir(ds, split) / 'images' / (Path(name).stem + '.json'))
    if doc['image'] != name:
        raise ValueError('候选来源图名不匹配')
    return {int(r['unit_id']): r for r in doc['units'][pool_kind(ds)]['details']}


def load(ds, split, name):
    d = geometry(ds, split, name)
    objects, shape = s5.c.b40.load_units(pool_dir(ds, split) / pool_kind(ds) / (Path(name).stem + '.npz'))
    if tuple(shape) != d['gt'].shape:
        raise ValueError('候选掩码尺寸不匹配')
    candidates, routes = [], []
    for ident, obj in enumerate(objects):
        x0, y0, x1, y1 = obj['bbox']
        sl = np.s_[y0:y1, x0:x1]
        mask = obj['mask']
        fill = bool(d['base'][sl][mask].mean() < .1)
        routes.append(fill)
        if fill:
            candidates.append(dict(id=ident, index=ident, bbox=obj['bbox'], original=mask,
                                   mask=mask & ~d['base'][sl], dscore=float(d['prob'][sl][mask].mean())))
    return dict(d, objects=objects, candidates=candidates, routes=routes)


def envelope(a, **values):
    return dict(dataset=a.dataset, split=a.split, limit=a.limit, partial=bool(a.limit), **values)


def part_path(a, stage, name, suffix='.json'):
    return a.out / a.dataset / stage / a.split / (Path(name).stem + suffix)


def read_parts(a, stage):
    result = []
    for name in names(a):
        row = read(part_path(a, stage, name))
        if any(row[k] != v for k, v in dict(dataset=a.dataset, split=a.split, limit=a.limit, image=name).items()):
            raise ValueError(f'{name}: 分片范围不一致')
        result.append(row)
    return result


def frozen(a):
    doc = read(a.out / 'frozen.json')
    if doc['limit'] != a.limit or doc['selection_dataset'] != 'levir/val':
        raise ValueError('冻结方案范围不一致')
    return doc['scheme']


def unit_path(a, split=None):
    return a.out / a.dataset / 'units' / f'{split or a.split}.npz'


def load_units(a, split=None):
    split = split or a.split
    z = archive(unit_path(a, split))
    if (int(z['limit']) != a.limit or str(z['scheme']) != frozen(a)
            or str(z['dataset']) != a.dataset or str(z['split']) != split
            or z['images'].tolist() != scope_names(a.dataset, split, a.limit)):
        raise ValueError('单元表与冻结方案、划分或图清单不一致')
    return z


def parser(doc, stages):
    p = argparse.ArgumentParser(description=doc)
    p.add_argument('stage', choices=stages)
    p.add_argument('--dataset', choices=('levir', 'whu'), default='levir')
    p.add_argument('--split', choices=('val', 'test'), default='val')
    p.add_argument('--out', type=Path, default=HERE / 'run')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard', type=int, default=0)
    return p


def setup(p):
    a = p.parse_args()
    a.out = local(a.out)
    raw_dataset(a.dataset, a.split)
    if a.limit < 0 or not 0 <= a.shard < a.shards:
        p.error('需要 limit >= 0, 0 <= shard < shards')
    if a.limit and a.out == HERE / 'run':
        p.error('冒烟必须使用独立 --out')
    if not os.environ.get('SLURM_JOB_ID') and not a.limit:
        p.error('正式计算必须通过 sbatch；登录节点仅 --limit 小冒烟')
    a.out.mkdir(parents=True, exist_ok=True)
    return a


def finish(a, stage, started):
    import time
    import resource
    if 'torch' in sys.modules:
        raise RuntimeError('本任务不允许加载 torch')
    row = envelope(a, stage=stage, seconds=time.monotonic()-started,
                   peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                   torch_loaded=False)
    write(a.out / a.dataset / 'timings' / f'{stage}-{a.split}-{a.shard}.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
