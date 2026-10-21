"""Shared CPU registration, grid storage and mask-weighted offsets for experiment 6i."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from scipy.fft import fftn, ifftn
from scipy.spatial import cKDTree
from skimage.registration import phase_cross_correlation

from common import load_label, load_rgb, names
from exp6b_common import ROOT

OUT = ROOT / "misregistration/r2"
AUDIT = ROOT / "label_noise/audit"
OFFSET_LABELS = ("<1", "1-3", "3-6", ">6", "unknown")
RELATIVE_LABELS = ("<0.1", "0.1-0.3", "0.3-1", ">1", "unknown")
AREA_LABELS = ("<50", "50-300", "300-1000", ">1000")
GRID_FIELDS = ("bbox", "raw_dx", "raw_dy", "error", "peak_ratio", "texture",
               "change_fraction", "valid", "interpolated", "dx", "dy", "residual_dx",
               "residual_dy", "global_dx", "global_dy", "unestimable", "shape",
               "interpolation_distance")


def read_config(path):
    config = json.loads(Path(path).read_text())
    if not config["calibration_passed"] or config.get("calibration_method") != "real_pair_difference_r2":
        raise ValueError("Real-pair r2 calibration did not pass")
    return config


def gray(rgb):
    return np.asarray(rgb, np.float32) @ np.asarray([0.299, 0.587, 0.114], np.float32) / 255


def standardize(image):
    std = float(image.std())
    return (image - image.mean()) / max(std, 1e-8)


def boxes(shape, window):
    """Half-window stride; append the last origin to cover the image boundary."""
    axes = []
    for length in shape:
        if length < window:
            raise ValueError("Image is smaller than registration window")
        starts = list(range(0, length - window + 1, window // 2))
        if starts[-1] != length - window:
            starts.append(length - window)
        axes.append(starts)
    return np.asarray([(x, y, x + window, y + window) for y in axes[0] for x in axes[1]],
                      np.int32)


def estimate(a, b, normalization):
    """Return t2 displacement (dx,dy), PCC error, primary/secondary peak ratio.

    PCC returns the correction to apply to t2, so negate and reverse (dy,dx).
    Remove each tile's DC term; no taper. The secondary absolute correlation peak
    excludes the periodic 3x3 neighbourhood of the primary peak. Both correlation
    and PCC use the same normalization, including skimage's phase epsilon.
    """
    a, b = a - a.mean(), b - b.mean()
    if float(a.std()) < 1e-8 or float(b.std()) < 1e-8:
        return np.nan, np.nan, np.nan, 0.0
    fa, fb = fftn(a), fftn(b)
    shift, error, _ = phase_cross_correlation(
        fa, fb, space="fourier", upsample_factor=4, normalization=normalization)
    product = fa * fb.conj()
    if normalization == "phase":
        product /= np.maximum(np.abs(product), 100 * np.finfo(product.real.dtype).eps)
    corr = np.abs(ifftn(product))
    y, x = np.unravel_index(corr.argmax(), corr.shape)
    peak = float(corr[y, x])
    yy = (y + np.arange(-1, 2)) % corr.shape[0]
    xx = (x + np.arange(-1, 2)) % corr.shape[1]
    corr[np.ix_(yy, xx)] = 0
    ratio = peak / max(float(corr.max()), np.finfo(np.float32).eps)
    return float(-shift[1]), float(-shift[0]), float(error), ratio


def load_pair(dataset, name):
    raw = [gray(load_rgb(dataset, name, view)) for view in ("t1", "t2")]
    gt = load_label(dataset, name)
    if raw[0].shape != raw[1].shape or gt.shape != raw[0].shape:
        raise ValueError(f"{dataset}/{name}: image/label shape mismatch")
    normalized = [standardize(a) for a in raw]
    return raw, normalized, gt


def reliable(values, texture, changed, config):
    """PCC error is diagnostic only, including when normalization is phase."""
    values = np.asarray(values)
    return (np.isfinite(values[..., [0, 1, 3]]).all(-1)
            & (texture > config["texture_threshold"])
            & (changed < config["maximum_change_fraction"])
            & (values[..., 3] > config["peak_ratio_threshold"]))


def estimate_image(dataset, name, config):
    raw, normalized, gt = load_pair(dataset, name)
    bbox = boxes(raw[0].shape, config["window"])
    measurements, texture, changed = [], [], []
    for x0, y0, x1, y1 in bbox:
        sl = np.s_[y0:y1, x0:x1]
        measurements.append(estimate(normalized[0][sl], normalized[1][sl], config["normalization"]))
        texture.append(min(float(a[sl].std()) for a in raw))
        changed.append(float(gt[sl].mean()))
    values = np.asarray(measurements, np.float32)
    texture, changed = np.asarray(texture, np.float32), np.asarray(changed, np.float32)
    valid = reliable(values, texture, changed, config)
    unestimable = int(valid.sum()) < config["minimum_valid_blocks"]
    filled = np.full((len(bbox), 2), np.nan, np.float32)
    global_offset = np.full(2, np.nan, np.float32)
    interpolated = np.zeros(len(bbox), bool)
    interpolation_distance = np.full(len(bbox), np.nan, np.float32)
    if not unestimable:
        filled[valid] = values[valid, :2]
        global_offset = np.median(filled[valid], axis=0)
        centers = (bbox[:, :2] + bbox[:, 2:]) / 2
        distance, indices = cKDTree(centers[valid]).query(centers[~valid], k=1)
        filled[~valid] = filled[valid][indices]
        interpolated[~valid] = True
        interpolation_distance[valid] = 0
        interpolation_distance[~valid] = distance
    residual = filled - global_offset
    return dict(bbox=bbox, raw_dx=values[:, 0], raw_dy=values[:, 1], error=values[:, 2],
                peak_ratio=values[:, 3], texture=texture, change_fraction=changed, valid=valid,
                interpolated=interpolated, dx=filled[:, 0], dy=filled[:, 1],
                residual_dx=residual[:, 0], residual_dy=residual[:, 1],
                global_dx=global_offset[0], global_dy=global_offset[1],
                interpolation_distance=interpolation_distance,
                unestimable=np.bool_(unestimable), shape=np.asarray(raw[0].shape, np.int32))


def iter_grids(out, dataset, config):
    """Validate complete shard membership, then hold only one shard in memory."""
    folder = Path(out) / dataset
    with np.load(folder / "shard_0.npz", allow_pickle=False) as z:
        shards = int(z["shards"])
    expected_paths = {f"shard_{i}.npz" for i in range(shards)}
    if {p.name for p in folder.glob("shard_*.npz")} != expected_paths:
        raise ValueError(f"{folder}: incomplete or mixed grid shards")
    images = names(dataset)
    for shard in range(shards):
        path = folder / f"shard_{shard}.npz"
        with np.load(path, allow_pickle=False) as z:
            if (str(z["dataset"]) != dataset or int(z["shard"]) != shard
                    or int(z["shards"]) != shards or json.loads(str(z["config"])) != config
                    or z["image"].tolist() != images[shard::shards]):
                raise ValueError(f"{path}: configuration or image membership mismatch")
            data = {key: z[key] for key in GRID_FIELDS}
            for i, name in enumerate(z["image"].tolist()):
                yield name, {key: value[i] for key, value in data.items()}


def bucket(values, relative=False):
    values = np.asarray(values)
    a, b, c = (0.1, 0.3, 1.0) if relative else (1.0, 3.0, 6.0)
    return np.where(~np.isfinite(values), 4,
                    np.where(values < a, 0, np.where(values <= b, 1,
                             np.where(values <= c, 2, 3)))).astype(np.int8)


def area_bucket(values):
    values = np.asarray(values)
    return np.where(values < 50, 0, np.where(values < 300, 1,
                    np.where(values <= 1000, 2, 3))).astype(np.int8)


def pixel_fields(grid):
    """Area-weighted support from valid measured windows only, plus nearest lookup."""
    height, width = map(int, grid["shape"])
    if grid["unestimable"]:
        return None
    valid = grid["valid"]
    offsets = np.column_stack((grid["raw_dx"][valid], grid["raw_dy"][valid]))
    values = np.column_stack((offsets, np.ones(valid.sum())))
    fields = np.zeros((height + 1, width + 1, 3), np.float64)
    bbox = grid["bbox"][valid]
    x0, y0, x1, y1 = bbox.T
    for yy, xx, sign in ((y0, x0, 1), (y1, x1, 1), (y0, x1, -1), (y1, x0, -1)):
        np.add.at(fields, (yy, xx), sign * values)
    centers = (bbox[:, :2] + bbox[:, 2:]) / 2
    return fields.cumsum(0).cumsum(1)[:height, :width], cKDTree(centers), offsets


def piece_center(bbox, mask):
    yy, xx = np.nonzero(mask)
    return np.asarray([bbox[0] + xx.mean(), bbox[1] + yy.mean()])


def offset_values(dx, dy, bbox, interpolation=0.0, distance=0.0):
    x0, y0, x1, y1 = bbox
    magnitude = np.hypot(dx, dy)
    return np.asarray([dx, dy, magnitude, interpolation,
                       magnitude / max(x1 - x0, y1 - y0), distance], np.float32)


def piece_offset(fields, bbox, mask):
    if fields is None:
        return np.full(6, np.nan, np.float32)
    x0, y0, x1, y1 = bbox
    summed, tree, offsets = fields
    totals = summed[y0:y1, x0:x1][mask].sum(0)
    if totals[2] > 0:
        dx, dy = totals[:2] / totals[2]
        return offset_values(dx, dy, bbox)
    distance, index = tree.query(piece_center(bbox, mask), k=1)
    dx, dy = offsets[index]
    return offset_values(dx, dy, bbox, interpolation=1.0, distance=distance)


def direct_piece(pair, bbox, mask, config):
    """Keep the calibrated window size; move an edge window inward, never truncate."""
    raw, normalized, gt = pair
    window = config["window"]
    height, width = gt.shape
    if min(height, width) < window:
        raise ValueError("Image is smaller than registration window")
    cx, cy = piece_center(bbox, mask)
    x0 = int(np.clip(int(cx) - window // 2, 0, width - window))
    y0 = int(np.clip(int(cy) - window // 2, 0, height - window))
    sl = np.s_[y0:y0 + window, x0:x0 + window]
    measurement = estimate(normalized[0][sl], normalized[1][sl], config["normalization"])
    texture = min(float(a[sl].std()) for a in raw)
    changed = float(gt[sl].mean())
    valid = bool(reliable(measurement, texture, changed, config))
    dx, dy, error, peak = measurement
    values = offset_values(dx, dy, bbox) if valid else np.full(6, np.nan, np.float32)
    details = dict(window_bbox=[x0, y0, x0 + window, y0 + window],
                   raw_dx=finite(dx), raw_dy=finite(dy), error=finite(error),
                   peak_ratio=finite(peak), texture=texture, change_fraction=changed, valid=valid)
    return values, details


def offset_record(values):
    dx, dy, magnitude, interpolation, relative, distance = map(float, values)
    return dict(dx=finite(dx), dy=finite(dy), magnitude=finite(magnitude),
                interpolation_fraction=finite(interpolation), relative=finite(relative),
                interpolation_distance=finite(distance),
                uses_interpolation=bool(interpolation > 1e-7) if np.isfinite(interpolation) else None,
                unestimable=not np.isfinite(magnitude),
                offset_bucket=OFFSET_LABELS[int(bucket(magnitude))],
                relative_bucket=RELATIVE_LABELS[int(bucket(relative, True))])


def combined_record(direct, grid, details):
    return dict(**offset_record(direct), primary_method="direct", direct=offset_record(direct),
                grid=offset_record(grid), direct_measurement=details)


def reference_result(direct, grid, details, config):
    """Report the strict criterion separately from the requested disagreement case."""
    result = combined_record(direct, grid, details)
    strict = bool(8 <= direct[2] <= 12)
    disagreement = bool(8 <= direct[2] <= 14 and not 8 <= grid[2] <= 14)
    result.update(dataset="second", image="test_857.png", piece_id=18, config=config,
                  expected_magnitude=[8, 12], passed=strict,
                  grid_passed=bool(8 <= grid[2] <= 12),
                  direct_8_14_grid_outside=disagreement,
                  proceed=bool(strict or disagreement),
                  note=("直接估计在 8–14 像素，网格估计不在此范围（含无法估计）；"
                        "按预先统一的规则以直接估计为主，保留 8–12 判据的实际结果。"
                        if disagreement else "以直接估计检查 8–12 像素判据。"))
    # Explicit, recorded override (environment variable carries the reason); passed stays as measured.
    override = os.environ.get("EXP6I_REFERENCE_OVERRIDE")
    if override and not result["proceed"]:
        result.update(proceed=True, override=override)
    return result


def finite(value):
    return float(value) if np.isfinite(value) else None


def fraction(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def distribution(values):
    values = np.asarray(values)
    known = values[np.isfinite(values)]
    return dict(total=int(values.size), estimated=int(known.size),
                unknown=int(values.size - known.size), mean=float(known.mean()) if known.size else None,
                quantiles={str(q): float(np.percentile(known, q)) if known.size else None
                           for q in (0, 10, 25, 50, 75, 90, 95, 99, 100)})
