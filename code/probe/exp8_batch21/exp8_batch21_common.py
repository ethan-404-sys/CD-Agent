"""SYSU paths and splits for the unchanged batch19/batch20 implementations."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
STAGE = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Register batch19 first: batch20's absolute batch18 sys.path insertion must
# resolve these two names to the SYSU adapters already loaded in this process.
data = load_module('exp8_data', STAGE.parent / 'batch19/exp8_data.py')
rt = load_module('exp8_runtime', STAGE.parent / 'batch19/exp8_runtime.py')
pool19 = load_module('exp8_pool', STAGE.parent / 'batch19/exp8_pool.py')
common20 = load_module('exp8_batch20_common', STAGE.parent / 'batch20/exp8_batch20_common.py')
DEFAULT_OUT = data.OUT / 'exp8_batch21'
DEFAULT_AUDIT = data.WORKSPACE / 'wyh/CD-Agent/audit/exp8_sysu'
MAIN = common20.MAIN
FLAGS, LABELS, RULES = common20.FLAGS, common20.LABELS, common20.RULES
checked_units = common20.checked_units
oracle, ratio, score = common20.oracle, common20.ratio, common20.score
write_fixed = common20.write_fixed


def arguments(parser, datasets=('pool_train', 'pool_val')):
    parser.add_argument('--dataset', choices=datasets, required=True)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--batch19', type=Path, default=data.DEFAULT_OUT)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--shard', type=rt.shard_type, default=(0, 1))


def source(dataset):
    return 'sysu' if dataset == 'sysu' else 'sysu_val'


def names(dataset):
    if dataset == 'sysu':
        return rt.image_names('sysu')
    return data.image_names(scope=dataset)


def selected_names(args, sharded=True):
    if not sharded and args.shard != (0, 1):
        raise ValueError('Aggregation/definition/selection/audit requires --shard 0/1')
    images = rt.subset(names(args.dataset), args)
    if not images:
        raise ValueError('Empty image selection')
    return images


def configure(args):
    args.batch19 = args.batch19.resolve()
    args.out = args.out.resolve()
    record = rt.read_json(args.batch19 / 'frozen/sysu_val.json')
    geom = rt.load_geometry('sysu_val', args.batch19)
    if (record['partial'] or record['validation_dataset'] != 'sysu_val'
            or record['validation_scope'] != 'pool_val'
            or record['validation_images'] != names('pool_val')
            or record['split_source'] != str(data.SPLIT)
            or record['selection_metrics'] != ['pixel_f1', 'pixel_iou']
            or record['multiplier'] not in (4, 8, 16)
            or record['geometry'] != geom
            or record['slic_geometry'] != rt.load_scale(args.batch19, 'slic')
            or record['threshold'] != record['multiplier'] * geom['median_area']):
        raise ValueError('Need complete batch19 SYSU pool_val freeze matching split and geometry')
    args.freeze = record
    MAIN.clear()
    MAIN.update({key: f'U5_x{record["multiplier"]}' for key in ('pool_train', 'pool_val', 'sysu')})


def pool_dir(args):
    return args.batch19 / 'pool' / source(args.dataset)


def read_pool(args, name):
    row = rt.read_json(pool_dir(args) / 'images' / f'{Path(name).stem}.json')
    config = row['config']
    expected_split = 'test' if args.dataset == 'sysu' else args.dataset
    expected_status = 'frozen_validation' if args.dataset == 'sysu' else 'validation_candidates'
    expected_u5 = ([MAIN['sysu']] if args.dataset == 'sysu' else ['U5_x4', 'U5_x8', 'U5_x16'])
    expected_keys = [f'objects_{m}' for m in rt.METHODS] + ['U0', 'U4'] + expected_u5
    if (row['image'] != name or config['dataset'] != source(args.dataset)
            or row['pool_split'] != expected_split or config['partial']
            or config['status'] != expected_status or config['units'] != expected_keys
            or config['sources'] != ['B', 'G0.05']
            or config['geometry'] != args.freeze['geometry']
            or config['slic_geometry'] != args.freeze['slic_geometry']):
        raise ValueError(f'{name}: incompatible batch19 pool, split or frozen geometry')
    return row


def provenance(args):
    return dict(batch19=str(args.batch19), split_source=str(data.SPLIT), freeze=args.freeze,
                limitation=data.LIMITATION, sources=['B', 'G0.05'], labels='binary change only')


# Bind only this process; helpers such as checked_units resolve pool_dir here.
for _name in ('arguments', 'source', 'names', 'selected_names', 'pool_dir', 'read_pool'):
    setattr(common20, _name, globals()[_name])


def batch20_module(stem):
    return load_module(f'_batch21_reuse_{stem}', STAGE.parent / 'batch20' / f'{stem}.py')
