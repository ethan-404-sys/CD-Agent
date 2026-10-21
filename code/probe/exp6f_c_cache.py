"""Pre-crop 6f-C uint8 shards on CPU; exp5-env, torch 2.5.1/torchvision 0.20.1.

Estimate: 2–4 GiB RAM per process, 1 CPU core; request 6 GiB with filesystem cache.
No GPU. Time and disk estimates are in README_batch7.md.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from exp6_pool_io import DETECTOR
from exp6b_common import Pool, SEED, SOURCES, save_npz, write_json
from exp6f_c_common import BYTES_PER_CROP, crop_batches, crop_config, fit_split, selected_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(DETECTOR), required=True)
    parser.add_argument("--out", type=Path, required=True, help="One directory per dataset/split/config")
    parser.add_argument("--split", choices=("fit", "all"), default="fit")
    parser.add_argument("--per_image", type=int, default=256, help="Train target cap; 0=all, all positives kept")
    parser.add_argument("--probability", choices=("raw", "image_rank"), default="raw")
    parser.add_argument("--context", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--limit_images", type=int, default=0, help="Smoke: first N images of each fit split")
    args = parser.parse_args()
    if (args.shards < 1 or not 0 <= args.shard < args.shards
            or min(args.per_image, args.limit_images, args.seed) < 0
            or not np.isfinite(args.context) or args.context < 1):
        parser.error("Invalid shards, sampling count, seed or context")
    if args.split == "fit" and args.dataset != "second_cv":
        parser.error("fit is only for second_cv; use --split all for other pools")
    started = time.monotonic()
    pool = Pool(args.dataset)
    train, val = fit_split(pool) if args.split == "fit" else ([], [])
    train_set = set(train)
    images = pool.images
    if args.limit_images:
        images = (sorted(train[:args.limit_images] + val[:args.limit_images])
                  if args.split == "fit" else images[:args.limit_images])
    images = images[args.shard::args.shards]
    selections = {name: selected_rows(pool, name, args.split, train_set, args.per_image,
                                      args.seed) for name in images}
    rows = np.concatenate(list(selections.values())) if selections else np.empty(0, np.int64)
    config = crop_config(args.probability, args.context)
    selection = dict(split=args.split, per_image=args.per_image, seed=args.seed,
                     limit_images=args.limit_images, train_images=train, val_images=val)
    out = args.out / f"shard_{args.shard:05d}"
    out.mkdir(parents=True, exist_ok=False)
    counts = []
    for name, idx in selections.items():
        counts.append(dict(image=name, samples=len(idx), positive=int((pool.label[idx] == 1).sum()),
                           negative=int((pool.label[idx] == 0).sum()),
                           null=int((pool.label[idx] < 0).sum()),
                           pred=int((pool.source[idx] == SOURCES.index("pred")).sum())))
    estimate = dict(samples=len(rows), crop_bytes=len(rows) * BYTES_PER_CROP,
                    crop_gib=len(rows) * BYTES_PER_CROP / 2**30)
    print(estimate, flush=True)
    temporary = out / "crops.partial.npy"
    if len(rows):
        crops = np.lib.format.open_memmap(temporary, mode="w+", dtype=np.uint8,
                                         shape=(len(rows), 8, 64, 64))
        cursor = 0
        for indices, batch in crop_batches(pool, images, config, 256, selections):
            if not np.array_equal(indices, rows[cursor:cursor + len(indices)]):
                raise ValueError("Crop writer row order changed")
            crops[cursor:cursor + len(indices)] = batch
            cursor += len(indices)
        if cursor != len(rows):
            raise ValueError("Incomplete crop writer")
        crops.flush()
        del crops
    else:
        np.save(temporary, np.empty((0, 8, 64, 64), np.uint8))
    temporary.replace(out / "crops.npy")
    save_npz(out / "index.npz", row=rows, image_id=pool.image_id[rows],
             piece_id=pool.piece_id[rows])
    write_json(out / "manifest.json",
               dict(dataset=args.dataset, pool_rows=len(pool), pool_images=pool.images,
                    shard=args.shard, shards=args.shards, crop_config=config,
                    selection=selection, smoke=bool(args.limit_images), **estimate,
                    per_image=counts, seconds=time.monotonic() - started))
    print(f"completed {out} in {time.monotonic() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
