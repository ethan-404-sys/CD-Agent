"""Reuse the exact SYSU test SAM3 extractor/parameters on official validation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import exp8_data as data

sys.dont_write_bytecode = True
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'Chang-Agent/probe'
sys.path.insert(0, str(PROBE))
import numpy as np
import common
import exp4_sam3_extract as extract


def shard_type(value):
    i, n = map(int, value.split('/'))
    if n < 1 or not 0 <= i < n:
        raise ValueError('Require 0 <= i < n for --shard i/n')
    return i, n


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=('sysu_val',), required=True)
    parser.add_argument('--out', type=Path, default=data.DEFAULT_OUT)
    parser.add_argument('--shard', type=shard_type, default=(0, 1))
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    data.register(common)
    extract.DETECTOR['sysu_val'] = 'schanger_small'
    images = data.image_names()
    images = images[:args.limit or None][args.shard[0]::args.shard[1]]
    output = args.out / 'sam3' / args.dataset
    missing = [n for n in images if not data.sam3_path(n, args.out).is_file()]
    if not missing:
        print('All requested SAM3 caches already exist; no model loaded', flush=True)
        return
    extract.names = lambda dataset: missing
    if args.dataset == 'sysu_val':
        extract.load_prob = lambda dataset, detector, name: np.load(
            args.out / 'preds/sysu_val' / detector / 'prob' / (Path(name).stem + '.npy'),
            allow_pickle=False).astype(np.float32)
    # The original extractor writes sam3_report.json to the cache's parent.
    # A shard-specific work directory avoids report collisions and retains partial work.
    work = args.out / 'sam3_work' / args.dataset / f'{args.shard[0]}of{args.shard[1]}' / 'sam3'
    old_argv = sys.argv
    sys.argv = [old_argv[0], '--dataset', args.dataset, '--output', str(work)]
    try:
        extract.main()
    finally:
        sys.argv = old_argv
    output.mkdir(parents=True, exist_ok=True)
    for name in missing:
        source = work / (Path(name).stem + '.npz')
        source.replace(output / source.name)


if __name__ == '__main__':
    main()
