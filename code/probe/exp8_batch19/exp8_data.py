"""SYSU official validation inventory and frozen original-image split (stdlib only)."""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

sys.dont_write_bytecode = True
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'Chang-Agent/probe'
OUT = WORKSPACE / 'Chang-Agent/outputs/CD_1016_probe'
DEFAULT_OUT = OUT / 'exp8_batch19'
SPLIT = WORKSPACE / 'wyh/CD-Agent/interim/exp8/sysu_val/split.json'
LIMITATION = '官方 SChanger-small 权重是否使用官方 val 选优未知；按已使用记录。验证部分只有 32 个原图块（800 张相关小图）。'
U0_MEDIAN = 972
SAM3_ROOTS = tuple(OUT / path for path in (
    'exp4/sysu/sam3', 'exp4/sysu_val/sam3', 'exp4/sysu_train/sam3',
    'exp8_batch18/sam3/sysu_val'))


@lru_cache(maxsize=1)
def split():
    row = json.loads(SPLIT.read_text())
    expected = {f'train_{n}.png' for n in range(12000, 16000)}
    groups = []
    for key, count, blocks in [('train', 3200, 128), ('val', 800, 32)]:
        images = row[f'{key}_images']
        block_ids = row[f'{key}_blocks']
        if len(images) != count or len(set(images)) != count or len(set(block_ids)) != blocks:
            raise ValueError(f'{SPLIT}: invalid {key} counts')
        wanted = {f'train_{12000 + b*25 + j}.png' for b in block_ids for j in range(25)}
        if set(images) != wanted:
            raise ValueError(f'{SPLIT}: {key} does not preserve complete 25-image blocks')
        groups.append(set(images))
    if groups[0] & groups[1] or groups[0] | groups[1] != expected:
        raise ValueError(f'{SPLIT}: train/validation must partition official val')
    return row


def image_names(dataset='sysu_val', scope='all'):
    if dataset != 'sysu_val':
        raise ValueError('This inventory is only for sysu_val')
    row = split()
    if scope in ('pool_train', 'pool_val', 'verifier_train', 'verifier_val'):
        return sorted(row['train_images' if scope in ('pool_train', 'verifier_train') else 'val_images'])
    if scope != 'all':
        raise ValueError(scope)
    return sorted(row['train_images'] + row['val_images'])


def register(common):
    """Register exact 4,000 names in this process; retain the original test loader."""
    common.DATASETS['sysu_val'] = {**common.DATASETS['sysu'], 'prefix': 'train_'}
    original = common.names
    common.names = lambda dataset: image_names() if dataset == 'sysu_val' else original(dataset)


def manifest():
    row = split()
    return dict(dataset='sysu_val', all_images=image_names(),
                pool_train_images=image_names(scope='pool_train'),
                pool_val_images=image_names(scope='pool_val'),
                verifier_train_images=image_names(scope='pool_train'),
                verifier_val_images=image_names(scope='verifier_val'),
                train_blocks=row['train_blocks'], val_blocks=row['val_blocks'],
                split_source=str(SPLIT), rule=row['rule'], test_gt_used=False,
                detector='schanger_small', official_val_used_for_checkpoint_selection=True,
                checkpoint_selection_evidence='unknown; conservatively treated as used',
                limitation=LIMITATION)


def sam3_path(name, out):
    filename = Path(name).stem + '.npz'
    for root in SAM3_ROOTS:
        path = root / filename
        if path.is_file():
            return path
    return Path(out) / 'sam3/sysu_val' / filename
