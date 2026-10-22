"""Dataset-specific paths and split adapters for batch22 SECOND."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
STAGE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'Chang-Agent/probe'
sys.path.insert(0, str(STAGE.parent / 'batch20'))
import exp8_batch20_common as b20

rt = b20.rt
sys.path.insert(0, str(PROBE))
import exp6b_common as old
from exp6b_train import weighted_bce

OUT = rt.OUT / 'exp8_batch22'
BATCH18 = rt.OUT / 'exp8_batch18'
BATCH20 = rt.OUT / 'exp8_batch20'
OLD_RUN = rt.OUT / 'exp6/verifier/none'
OLD_METRICS = rt.OUT / 'eval_metrics/summary.json'
UNIT = 'U5_x4'
SOURCES = ('B', 'G0.05', 'H1')
POOL_DATASET = dict(train='second_cv_train', val='second_cv', test='second')
RAW_DATASET = dict(train='second_cv', val='second_cv', test='second')
EXPECTED = dict(train=2671, val=297, test=1694)
LEGACY_THRESHOLD = 0.819681704044342
LEGACY_COMBINATION = 'B+G0.05+H1'
SCORERS = ('full', 'clean', 'detector')
LOW_QUANTILES = (.10, .25, .50)
HIGH_QUANTILES = (.75, .90, .95)
SECONDS_PER_OBJECT = .44
FEATURE_NAMES = (['det_mean', 'det_max', 'det_min', 'det_p90', 'log_area']
                 + [f'source_{s}' for s in old.SOURCES] + ['source_count']
                 + ['combination_' + '+'.join(s for i, s in enumerate(SOURCES) if code & (1 << i))
                    for code in range(1, 8)])


def names(split, limit=0):
    train, val = old.verifier_split()
    images = dict(train=train, val=val).get(split)
    if images is None:
        images = rt.image_names(RAW_DATASET[split])
    if len(images) != EXPECTED[split]:
        raise ValueError(f'{split}: expected {EXPECTED[split]} images')
    return images[:limit or None]


def pool_args(split, args):
    # Smoke uses a subset of completed real pools, not partial batch20 pools.
    return SimpleNamespace(dataset=POOL_DATASET[split], out=args.batch20,
                           batch18=args.batch18, limit=0)


def frozen_pool(args):
    return b20.frozen(pool_args('train', args))


def read_units(split, name, args):
    adapter = pool_args(split, args)
    row = b20.read_pool(adapter, name)
    objects, shape, details = b20.checked_units(adapter, name, UNIT, row)
    return objects, shape, details


def raw_inputs(split, name, args):
    ds = RAW_DATASET[split]
    base = rt.prediction(ds, name, args.batch18)
    gt = rt.common.load_label(ds, name)
    if base.shape != gt.shape:
        raise ValueError(f'{name}: baseline/GT shape mismatch')
    return base, gt


def arguments(parser):
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--batch18', type=Path, default=BATCH18)
    parser.add_argument('--batch20', type=Path, default=BATCH20)
    parser.add_argument('--limit', type=int, default=0, help='Smoke: first N train/val images; never test')


def protocol(args):
    if args.limit < 0:
        raise ValueError('--limit must be nonnegative')
    if args.limit and args.out.resolve() == OUT.resolve():
        raise ValueError('Smoke requires a separate --out directory')
    return dict(revision=1, unit=UNIT, sources=list(SOURCES), features=FEATURE_NAMES,
                batch18=str(args.batch18.resolve()), batch20=str(args.batch20.resolve()),
                limit=args.limit, partial=bool(args.limit), pool_freeze=frozen_pool(args),
                train_images=names('train', args.limit), val_images=names('val', args.limit))
