"""SYSU paths, frozen splits and process-local adapters for batch22."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
STAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(STAGE.parent / 'batch21'))
import exp8_batch21_common as b21

rt, data, b20 = b21.rt, b21.data, b21.common20
sys.path.insert(0, str(data.PROBE))
import exp6b_common as old
from exp6b_train import weighted_bce

verifier = b21.load_module('_batch24_verifier', STAGE.parent / 'batch19/exp8_verifier.py')
OUT = data.OUT / 'exp8_batch24'
BATCH19 = data.DEFAULT_OUT
BATCH21 = data.OUT / 'exp8_batch21'
U0_TEST = data.OUT / 'exp6'
SOURCES = ('B', 'G0.05')
RAW_DATASET = dict(train='sysu_val', val='sysu_val', test='sysu')
POOL_DATASET = dict(train='pool_train', val='pool_val', test='sysu')
EXPECTED = dict(train=3200, val=800, test=4000)
LOW_QUANTILES = (.10, .25, .50)
HIGH_QUANTILES = (.75, .90, .95)
SECONDS_PER_OBJECT = .44
LIMITATIONS = [data.LIMITATION,
              'U5 倍数、清洗规则、操作阈值和主方案在同一 800 张验证图上选择，验证集被多次使用。',
              'U0 固定沿用历史 6a 的 972 像素 SLIC 尺度，该尺度源于历史测试统计。',
              'SYSU 只有二值变化标签；未使用语义真值。']


def configure_mode(kind):
    global POOL_KIND, EXTRA_DIM, TRAINED, SCORERS, FEATURE_NAMES
    if kind not in ('u5', 'u0'):
        raise ValueError(kind)
    POOL_KIND = kind
    EXTRA_DIM = 4 if kind == 'u5' else 0
    TRAINED = ('full', 'clean') if kind == 'u5' else ('u0',)
    SCORERS = (*TRAINED, 'detector') if kind == 'u5' else TRAINED
    FEATURE_NAMES = (['det_mean', 'det_max', 'det_min', 'det_p90', 'log_area']
                     + [f'source_{s}' for s in old.SOURCES])
    if kind == 'u5':
        FEATURE_NAMES += ['source_count', 'combination_B', 'combination_G0.05', 'combination_B+G0.05']


configure_mode('u5')


def names(split, limit=0):
    images = b21.names(POOL_DATASET[split])
    if len(images) != EXPECTED[split]:
        raise ValueError(f'{split}: expected {EXPECTED[split]} images')
    return images[:limit or None]


@lru_cache(maxsize=2)
def pool_settings(batch19):
    adapter = SimpleNamespace(batch19=Path(batch19), out=OUT, dataset='pool_train', limit=0)
    b21.configure(adapter)
    train, val = verifier.verifier_split(batch19)
    if train != names('train') or val != names('val'):
        raise ValueError('6b split differs from SYSU original-image split')
    return adapter.freeze


def frozen_pool(args):
    return pool_settings(args.batch19.resolve())


def unit(args):
    return 'U0' if POOL_KIND == 'u0' else f'U5_x{frozen_pool(args)["multiplier"]}'


def read_units(split, name, args):
    if POOL_KIND == 'u0':
        import exp8_u0
        return exp8_u0.read_units(split, name, args)
    adapter = SimpleNamespace(dataset=POOL_DATASET[split], batch19=args.batch19,
                              freeze=frozen_pool(args), limit=0)
    row = b21.read_pool(adapter, name)
    return b21.checked_units(adapter, name, unit(args), row)


def unit_ids(split, name, args, count):
    if POOL_KIND == 'u0':
        import exp8_u0
        return exp8_u0.image_arrays(split, name, args)[2]
    return rt.np.arange(count)


def raw_inputs(split, name, args):
    base = rt.prediction(RAW_DATASET[split], name, args.batch19)
    gt = rt.common.load_label(RAW_DATASET[split], name)
    if base.shape != gt.shape:
        raise ValueError(f'{name}: baseline/GT shape mismatch')
    return base, gt


def detector_scores(meta):
    return dict(detector=meta[:, 0].copy()) if POOL_KIND == 'u5' else {}


def arguments(parser):
    parser.add_argument('--pool', choices=('u5', 'u0'), default=POOL_KIND)
    parser.add_argument('--out', type=lambda p: Path(p).resolve() / POOL_KIND,
                        default=OUT / POOL_KIND, help='Root; u5/u0 subdirectory is appended')
    parser.add_argument('--batch19', type=Path, default=BATCH19)
    parser.add_argument('--batch21', type=Path, default=BATCH21)
    parser.add_argument('--u0-test', type=Path, default=U0_TEST, help='Historical exp6 root')
    # Existing batch22 extract uses args.batch18 for the detector prediction root.
    parser.set_defaults(batch18=BATCH19)
    parser.add_argument('--limit', type=int, default=0, help='First N train/val images; never test')


def protocol(args):
    if args.limit < 0 or args.pool != POOL_KIND:
        raise ValueError('Invalid limit or pool mode')
    if args.limit and args.out.parent.resolve() == OUT.resolve():
        raise ValueError('Smoke requires a separate output root')
    args.batch18 = args.batch19
    return dict(revision='batch24-v1', dataset='sysu', pool=POOL_KIND, unit=unit(args),
                sources=list(SOURCES), features=FEATURE_NAMES, limit=args.limit, partial=bool(args.limit),
                batch19=str(args.batch19.resolve()), batch21=str(args.batch21.resolve()),
                u0_test=str(args.u0_test.resolve()), pool_freeze=frozen_pool(args),
                train_images=names('train', args.limit), val_images=names('val', args.limit),
                limitations=LIMITATIONS, checkpoint_selection='validation_AP, same as batch22/6b',
                operating_selection=['pixel_f1', 'pixel_iou'])


def joint_path(root, limit):
    return root / ('smoke_calibration.json' if limit else 'frozen.json')


def test_gate(args):
    root = args.out.parent
    frozen = rt.read_json(joint_path(root, 0))
    for kind in ('u5', 'u0'):
        if frozen['units'][kind] != rt.read_json(root / kind / 'frozen.json'):
            raise ValueError('Joint frozen comparison changed')
    summary = rt.read_json(root / 'evaluation/val/summary.json')
    if (summary['scheme_id'] != frozen['scheme_id'] or summary['partial']
            or summary['images'] != names('val')):
        raise ValueError('Complete joint validation report before test access')
