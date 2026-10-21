"""6f-C crops and cache reader; exp5-env (torch 2.5.1, torchvision 0.20.1).

CPU RAM: roughly 1–3 GiB for Pool/index metadata plus one image's piece masks.
Crop arrays remain uint8 on disk and are memory mapped, not loaded as a whole.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from common import load_prob, load_rgb
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import SOURCES, verifier_split

SIZE = 64
BYTES_PER_CROP = 8 * SIZE * SIZE


def crop_config(probability="raw", context=1.5):
    return dict(size=SIZE, channels=["t1_rgb", "t2_rgb", "mask", "probability"],
                probability=probability, context=context, rgb_padding="edge",
                mask_padding="zero", probability_padding="edge", dtype="uint8",
                probability_quantization="round(255*p)",
                rank_reference="all_pixels_of_current_image_midrank")


def image_probability(prob, mode):
    if not np.isfinite(prob).all() or np.any((prob < 0) | (prob > 1)):
        raise ValueError("Detector probability must be finite and in [0, 1]")
    if mode == "raw":
        return prob
    if mode != "image_rank":
        raise ValueError(mode)
    _, inverse, counts = np.unique(prob, return_inverse=True, return_counts=True)
    if len(counts) == 1:
        return np.full_like(prob, 0.5)
    # Empirical pixel percentile, with average ranks for ties.
    ranks = (np.cumsum(counts) - (counts + 1) / 2) / (prob.size - 1)
    return ranks[inverse].reshape(prob.shape).astype(np.float32)


def read_image(pool, name, config):
    t1, t2 = (load_rgb(pool.dataset, name, view) for view in ("t1", "t2"))
    prob = image_probability(load_prob(pool.dataset, DETECTOR[pool.dataset], name),
                             config["probability"])
    if t1.shape != t2.shape or t1.shape[:2] != prob.shape:
        raise ValueError(f"{name}: RGB/probability sizes differ")
    return t1, t2, prob, load_piece_masks(pool.dataset, name)


def make_crop(t1, t2, prob, bbox, mask, config):
    x0, y0, x1, y1 = bbox
    side = max(SIZE, math.ceil(max(x1 - x0, y1 - y0) * config["context"]))
    left, top = math.floor((x0 + x1 - side) / 2), math.floor((y0 + y1 - side) / 2)
    ys = np.clip(np.arange(top, top + side), 0, t1.shape[0] - 1)
    xs = np.clip(np.arange(left, left + side), 0, t1.shape[1] - 1)
    local_mask = np.zeros((side, side), np.uint8)
    local_mask[y0 - top:y1 - top, x0 - left:x1 - left] = mask.astype(np.uint8) * 255
    rgb = [np.asarray(Image.fromarray(im[ys[:, None], xs]).resize(
        (SIZE, SIZE), Image.Resampling.BILINEAR)) for im in (t1, t2)]
    resized_mask = np.asarray(Image.fromarray(local_mask).resize(
        (SIZE, SIZE), Image.Resampling.NEAREST))
    resized_prob = np.asarray(Image.fromarray(prob[ys[:, None], xs]).resize(
        (SIZE, SIZE), Image.Resampling.BILINEAR))
    probability = np.rint(np.clip(resized_prob, 0, 1) * 255).astype(np.uint8)
    return np.concatenate((*rgb, resized_mask[..., None], probability[..., None]),
                          axis=2).transpose(2, 0, 1)


def crop_batches(pool, images, config, batch_size, selections=None):
    """Yield Pool rows and uint8 crops; at most one image plus one batch in RAM."""
    buffer = np.empty((batch_size, 8, SIZE, SIZE), np.uint8)
    indices = np.empty(batch_size, np.int64)
    filled = 0
    for name in images:
        sl = pool.slices[name]
        rows = np.arange(sl.start, sl.stop) if selections is None else selections[name]
        if not len(rows):
            continue
        t1, t2, prob, masks = read_image(pool, name, config)
        for row in rows:
            bbox, mask = masks[int(pool.piece_id[row])]
            buffer[filled] = make_crop(t1, t2, prob, bbox, mask, config)
            indices[filled] = row
            filled += 1
            if filled == batch_size:
                yield indices.copy(), buffer.copy()
                filled = 0
    if filled:
        yield indices[:filled].copy(), buffer[:filled].copy()


def selected_rows(pool, name, split, train_images, per_image, seed):
    sl = pool.slices[name]
    rows = np.arange(sl.start, sl.stop, dtype=np.int64)
    if split == "all" or name not in train_images:
        return rows
    rows = rows[(pool.label[rows] >= 0) & (pool.source[rows] != SOURCES.index("pred"))]
    if not per_image or len(rows) <= per_image:
        return rows
    positive = rows[pool.label[rows] == 1]
    negative = rows[pool.label[rows] == 0]
    rng = np.random.default_rng(np.random.SeedSequence([seed, pool.image_index[name]]))
    negative = rng.choice(negative, min(len(negative), max(0, per_image - len(positive))),
                          replace=False)
    return np.sort(np.concatenate((positive, negative)))


class CropCache(Dataset):
    """Index cache positions to Pool rows; each worker lazily opens read-only maps."""

    def __init__(self, directory, pool):
        self.directory = Path(directory)
        paths = sorted(self.directory.glob("shard_*/manifest.json"))
        if not paths:
            raise FileNotFoundError(f"No completed crop shards in {directory}")
        manifests = [json.loads(path.read_text()) for path in paths]
        first = manifests[0]
        count = first["shards"]
        if [m["shard"] for m in manifests] != list(range(count)):
            raise ValueError("Crop cache has missing or duplicate shards")
        self.config, self.spec = first["crop_config"], first["selection"]
        self.smoke = first["smoke"]
        self.paths, rows, lengths = [], [], []
        for path, manifest in zip(paths, manifests):
            if (manifest["dataset"] != pool.dataset or manifest["pool_rows"] != len(pool)
                    or manifest["pool_images"] != pool.images
                    or manifest["crop_config"] != self.config
                    or manifest["selection"] != self.spec or manifest["shards"] != count
                    or manifest["smoke"] != self.smoke):
                raise ValueError(f"{path}: inconsistent cache configuration or Pool")
            with np.load(path.parent / "index.npz", allow_pickle=False) as z:
                idx = z["row"]
                if (np.any((idx < 0) | (idx >= len(pool)))
                        or not np.array_equal(pool.image_id[idx], z["image_id"])
                        or not np.array_equal(pool.piece_id[idx], z["piece_id"])):
                    raise ValueError(f"{path}: cache rows do not match Pool")
            array_path = path.parent / "crops.npy"
            array = np.load(array_path, mmap_mode="r", allow_pickle=False)
            if array.dtype != np.uint8 or array.shape != (len(idx), 8, SIZE, SIZE):
                raise ValueError(f"{array_path}: wrong shape or dtype")
            if len(idx) != manifest["samples"]:
                raise ValueError(f"{path}: incomplete cache")
            del array
            self.paths.append(array_path)
            rows.append(idx)
            lengths.append(len(idx))
        self.rows = np.concatenate(rows)
        if len(np.unique(self.rows)) != len(self.rows):
            raise ValueError("Duplicate rows in crop cache")
        self.ends = np.cumsum(lengths)
        self.starts = np.r_[0, self.ends[:-1]]
        self.maps = {}

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, position):
        shard = int(np.searchsorted(self.ends, position, side="right"))
        if shard not in self.maps:
            self.maps[shard] = np.load(self.paths[shard], mmap_mode="r", allow_pickle=False)
        crop = np.array(self.maps[shard][position - self.starts[shard]], copy=True)
        return torch.from_numpy(crop), int(self.rows[position])

    def require_coverage(self, rows):
        if not np.isin(rows, self.rows).all():
            raise ValueError("Cache is missing required Pool rows; rebuild the full requested split")


def loader(cache, positions, batch_size, workers, shuffle=False, seed=1020):
    return DataLoader(torch.utils.data.Subset(cache, positions), batch_size=batch_size,
                      shuffle=shuffle, num_workers=workers, pin_memory=True,
                      generator=torch.Generator().manual_seed(seed),
                      persistent_workers=False,
                      **({"prefetch_factor": 2} if workers else {}))


def labeled_recall(pool, images):
    return (pool.image_mask(images) & (pool.label >= 0)
            & (pool.source != SOURCES.index("pred")))


def fit_split(pool):
    if pool.dataset != "second_cv":
        raise ValueError("Training cache requires second_cv")
    return verifier_split()
