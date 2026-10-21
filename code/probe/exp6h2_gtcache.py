"""6h-2 train-only GT sidecars and real-crop checks; exp5-env, CPU only.

Request 1 CPU / 6 GiB per existing fit shard, estimated 5–30 minutes/shard.
One uint8 channel per training crop (~2.82 GiB for 739826 crops); no RGB copy.
Uses the same common.load_label binary change definition as exp6a_pieces.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch.utils.data import Dataset

from common import load_label
from exp6_pool_io import load_piece_masks
from exp6b_common import Pool, save_npz, verifier_split, write_json
from exp6f_c_common import CropCache, SIZE, labeled_recall


def window(bbox, context):
    # Same half-open bbox, center rounding and minimum size as make_crop().
    x0, y0, x1, y1 = bbox
    side = max(SIZE, math.ceil(max(x1 - x0, y1 - y0) * context))
    return math.floor((x0 + x1 - side) / 2), math.floor((y0 + y1 - side) / 2), side


def resize_mask(mask):
    return np.asarray(Image.fromarray(mask).resize((SIZE, SIZE), Image.Resampling.NEAREST))


def unchanged_crop(changed, bbox, context):
    left, top, side = window(bbox, context)
    h, w = changed.shape
    x0, y0, x1, y1 = max(left, 0), max(top, 0), min(left + side, w), min(top + side, h)
    local = np.zeros((side, side), np.uint8)
    local[y0 - top:y1 - top, x0 - left:x1 - left] = (~changed[y0:y1, x0:x1]).astype(np.uint8) * 255
    return resize_mask(local)[None]


class TemporalCache(Dataset):
    """Train-only view, retaining source fit order and explicit Pool-row mapping."""

    def __init__(self, cache, directory, pool):
        self.cache = cache
        self.paths, positions, lengths = [], [], []
        train = labeled_recall(pool, verifier_split()[0])
        for i, source in enumerate(cache.paths):
            out = Path(directory) / source.parent.name
            manifest = json.loads((out / "manifest.json").read_text())
            original = json.loads((source.parent / "manifest.json").read_text())
            if manifest["source_manifest"] != original:
                raise ValueError(f"{out}: GT sidecar and fit manifest differ")
            expected = np.arange(cache.starts[i], cache.ends[i])
            expected = expected[train[cache.rows[expected]]]
            with np.load(out / "index.npz", allow_pickle=False) as z:
                if (not np.array_equal(z["fit_position"], expected)
                        or not np.array_equal(z["row"], cache.rows[expected])):
                    raise ValueError(f"{out}: GT sidecar order differs from fit training rows")
            path = out / "unchanged.npy"
            data = np.load(path, mmap_mode="r", allow_pickle=False)
            if data.dtype != np.uint8 or data.shape != (len(expected), 1, SIZE, SIZE):
                raise ValueError(f"{path}: wrong GT shape or dtype")
            self.paths.append(path)
            positions.append(expected)
            lengths.append(len(expected))
        self.positions = np.concatenate(positions)
        self.rows = cache.rows[self.positions]
        self.ends = np.cumsum(lengths)
        self.starts = np.r_[0, self.ends[:-1]]
        self.maps = {}

    def __len__(self):
        return len(self.positions)

    def __getitem__(self, position):
        shard = int(np.searchsorted(self.ends, position, side="right"))
        if shard not in self.maps:
            self.maps[shard] = np.load(self.paths[shard], mmap_mode="r", allow_pickle=False)
        unchanged = np.array(self.maps[shard][position - self.starts[shard]], copy=True)
        crop, row = self.cache[int(self.positions[position])]
        return crop, row, torch.from_numpy(unchanged)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shard", type=int, required=True, help="Existing fit shard number")
    parser.add_argument("--check_samples", type=int, default=128, help="Random checks per label per shard")
    parser.add_argument("--seed", type=int, default=1020)
    args = parser.parse_args()
    if args.check_samples < 1:
        parser.error("--check_samples must be positive")
    started = time.monotonic()
    pool = Pool("second_cv")
    cache = CropCache(args.cache, pool)
    train, val = verifier_split()
    if (cache.spec["split"] != "fit" or cache.spec["train_images"] != train
            or cache.spec["val_images"] != val):
        raise ValueError("Expected the current verifier fit cache")
    if not 0 <= args.shard < len(cache.paths):
        parser.error("--shard is outside the existing cache")
    positions = np.arange(cache.starts[args.shard], cache.ends[args.shard])
    positions = positions[labeled_recall(pool, train)[cache.rows[positions]]]
    rows = cache.rows[positions]
    source = cache.paths[args.shard].parent
    out = args.out / source.name
    out.mkdir(parents=True, exist_ok=False)
    temporary = out / "unchanged.partial.npy"
    shape = (len(rows), 1, SIZE, SIZE)
    if len(rows):
        data = np.lib.format.open_memmap(temporary, mode="w+", dtype=np.uint8, shape=shape)
    else:
        np.save(temporary, np.empty(shape, np.uint8))
    rng = np.random.default_rng(args.seed + args.shard)
    checks = set()
    for label in (0, 1):
        eligible = np.flatnonzero(pool.label[rows] == label)
        checks.update(rng.choice(eligible, min(args.check_samples, len(eligible)), replace=False).tolist())
    statistics, current = [], None
    for i, row in enumerate(rows):
        name = pool.images[pool.image_id[row]]
        if name != current:
            changed = load_label("second_cv", name)
            masks = load_piece_masks("second_cv", name)
            current = name
        bbox, mask = masks[int(pool.piece_id[row])]
        data[i] = unchanged_crop(changed, bbox, cache.config["context"])
        if i not in checks:
            continue
        x0, y0, x1, y1 = bbox
        original_fraction = float(changed[y0:y1, x0:x1][mask.astype(bool)].mean())
        label = int(pool.label[row])
        expected_label = 0 if original_fraction < 0.1 else 1 if original_fraction >= 0.5 else -1
        if label != expected_label:
            raise ValueError(f"{name}/{pool.piece_id[row]}: GT and original 6a label differ")
        left, top, side = window(bbox, cache.config["context"])
        piece = np.zeros((side, side), np.uint8)
        piece[y0 - top:y1 - top, x0 - left:x1 - left] = mask.astype(np.uint8) * 255
        crop, cached_row = cache[int(positions[i])]
        cached_mask = crop[6].numpy() > 0
        if cached_row != row or not np.array_equal(resize_mask(piece) > 0, cached_mask):
            raise ValueError(f"{name}: mask geometry differs from fit crop")
        resized_fraction = float((data[i, 0][cached_mask] == 0).mean()) if cached_mask.any() else None
        statistics.append(dict(row=int(row), label=label, original_changed_fraction=original_fraction,
                               resized_changed_fraction=resized_fraction,
                               resized_piece_pixels=int(cached_mask.sum())))
    if len(rows):
        data.flush()
        del data
    temporary.replace(out / "unchanged.npy")
    save_npz(out / "index.npz", fit_position=positions, row=rows)
    summary = {}
    for label in (0, 1):
        records = [r for r in statistics if r["label"] == label]
        values = [r["resized_changed_fraction"] for r in records if r["resized_changed_fraction"] is not None]
        summary[str(label)] = dict(checked=len(records), resized_nonempty=len(values),
            original_mean=float(np.mean([r["original_changed_fraction"] for r in records])) if records else None,
            resized_mean=float(np.mean(values)) if values else None,
            resized_below_10pct=sum(v < 0.1 for v in values),
            resized_at_least_50pct=sum(v >= 0.5 for v in values))
    write_json(out / "selfcheck.json", dict(summary=summary, samples=statistics))
    write_json(out / "manifest.json", dict(source_manifest=json.loads((source / "manifest.json").read_text()),
        samples=len(rows), bytes=len(rows) * SIZE * SIZE,
        encoding="255=valid unchanged; 0=changed or padding", interpolation="nearest; zero padding",
        gt_loader="common.load_label(second_cv, name), as in exp6a_pieces", seconds=time.monotonic() - started))
    print(json.dumps(dict(out=str(out), samples=len(rows), selfcheck=summary), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
