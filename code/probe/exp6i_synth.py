"""6i real-pair differential calibration, CPU only; run shards, then --merge.

50 real test pairs, unchanged windows, 13 shifts, 8 configurations.
Use 10 shards; limit BLAS threads to one. Runtime and memory are unmeasured.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import shift as image_shift

from common import names
from exp6b_common import SEED, save_npz, write_json
from exp6i_common import OUT, boxes, estimate, fraction, load_pair, standardize

WINDOWS = (32, 48, 64, 96)
NORMALIZATIONS = (None, "phase")
SHIFTS = [(0.0, 0.0)] + [(dx, dy) for d in (2.0, 2.5, 5.0, 10.0)
                         for dx, dy in ((d, 0.0), (0.0, d), (d, d))]
COLUMNS = ("image_id", "shift_id", "window_id", "normalization_id", "delta_dx", "delta_dy",
           "pcc_error", "peak_ratio", "texture", "euclidean_error",
           "baseline_dx", "baseline_dy", "shifted_dx", "shifted_dy",
           "baseline_peak_ratio", "shifted_peak_ratio", "baseline_pcc_error",
           "baseline_texture", "shifted_texture", "x0", "y0")
METHOD = "real_pair_difference_r2"


def summary(rows, selected):
    r = rows[selected]
    good = np.isfinite(r[:, 9])
    expected = np.asarray(SHIFTS)[r[good, 1].astype(int)]
    absolute = np.abs(r[good, 4:6] - expected)
    return dict(n=len(r), finite=int(good.sum()),
                mae_xy=float(absolute.mean()) if good.any() else None,
                mae_dx=float(absolute[:, 0].mean()) if good.any() else None,
                mae_dy=float(absolute[:, 1].mean()) if good.any() else None,
                mean_euclidean_error=float(r[good, 9].mean()) if good.any() else None,
                failure_rate=fraction(np.count_nonzero(~good | (r[:, 9] > 1)), len(r)),
                within_one_pixel=fraction(np.count_nonzero(good & (r[:, 9] <= 1)), len(r)))


def measure(args, selected):
    started = time.monotonic()
    rows = []
    image_ids = list(range(args.shard, len(selected), args.shards))
    for image_id in image_ids:
        name = selected[image_id]
        raw, normalized, gt = load_pair("second", name)
        a, b = raw
        aa, bb = normalized
        shifted = [b] + [image_shift(b, shift=(dy, dx), order=3, mode="reflect", prefilter=True)
                         for dx, dy in SHIFTS[1:]]
        shifted_normalized = [bb] + [standardize(value) for value in shifted[1:]]
        for wi, window in enumerate(WINDOWS):
            for x0, y0, x1, y1 in boxes(a.shape, window):
                sl = np.s_[y0:y1, x0:x1]
                if gt[sl].any():
                    continue
                baseline_texture = min(float(a[sl].std()), float(b[sl].std()))
                baseline = [estimate(aa[sl], bb[sl], norm) for norm in NORMALIZATIONS]
                for shift_id, (dx, dy) in enumerate(SHIFTS):
                    # The translated source and its cubic interpolation margin must
                    # also be unchanged and inside the real image, never reflection.
                    sx0, sy0 = int(np.floor(x0 - dx)) - 3, int(np.floor(y0 - dy)) - 3
                    sx1, sy1 = int(np.ceil(x1 - dx)) + 3, int(np.ceil(y1 - dy)) + 3
                    if shift_id and (sx0 < 0 or sy0 < 0 or sx1 > a.shape[1]
                                     or sy1 > a.shape[0] or gt[sy0:sy1, sx0:sx1].any()):
                        continue
                    shifted_texture = min(float(a[sl].std()), float(shifted[shift_id][sl].std()))
                    for ni, normalization in enumerate(NORMALIZATIONS):
                        bx, by, base_error, base_peak = baseline[ni]
                        ex, ey, error, peak = (baseline[ni] if shift_id == 0 else
                                              estimate(aa[sl], shifted_normalized[shift_id][sl], normalization))
                        delta_x, delta_y = ex - bx, ey - by
                        rows.append((image_id, shift_id, wi, ni, delta_x, delta_y, error,
                                     min(base_peak, peak), min(baseline_texture, shifted_texture),
                                     np.hypot(delta_x - dx, delta_y - dy), bx, by, ex, ey,
                                     base_peak, peak, base_error, baseline_texture, shifted_texture, x0, y0))
        print(f"synth {name} rows={len(rows)} elapsed={time.monotonic() - started:.1f}s", flush=True)
    save_npz(args.out / "synth_shards" / f"shard_{args.shard}.npz",
             values=np.asarray(rows, np.float32).reshape(-1, len(COLUMNS)), columns=np.asarray(COLUMNS),
             image=np.asarray(selected), shifts=np.asarray(SHIFTS), seed=args.seed,
             measured_image_ids=np.asarray(image_ids), calibration_method=METHOD,
             shard=args.shard, shards=args.shards, elapsed_seconds=time.monotonic() - started)


def merge(args, selected):
    folder = args.out / "synth_shards"
    if {p.name for p in folder.glob("shard_*.npz")} != {f"shard_{i}.npz" for i in range(args.shards)}:
        raise ValueError("Synthetic shards are incomplete or mixed")
    parts, seconds = [], []
    for shard in range(args.shards):
        with np.load(folder / f"shard_{shard}.npz", allow_pickle=False) as z:
            values = z["values"]
            if (z["image"].tolist() != selected or z["columns"].tolist() != list(COLUMNS)
                    or int(z["seed"]) != args.seed or int(z["shard"]) != shard
                    or int(z["shards"]) != args.shards or not np.array_equal(z["shifts"], SHIFTS)
                    or str(z["calibration_method"]) != METHOD
                    or z["measured_image_ids"].tolist() != list(range(shard, 50, args.shards))
                    or not set(values[:, 0].astype(int)) <= set(range(shard, 50, args.shards))):
                raise ValueError("Synthetic shard metadata disagree")
            parts.append(values)
            seconds.append(float(z["elapsed_seconds"]))
    rows = np.concatenate(parts)
    del parts
    report = dict(images=selected, shifts_dx_dy=SHIFTS, seed=args.seed, calibration_method=METHOD,
                  shift_method="scipy.ndimage.shift(order=3, mode=reflect); reflected edges excluded",
                  grid="window/2 stride plus final boundary origin; all eligible windows",
                  error_definition="norm(estimate(t1, shifted_t2) - estimate(t1, t2) - shift); "
                                   "nonfinite and periodic wrap failures count as failures",
                  quality_definition="minimum baseline/shifted peak ratio; PCC error is diagnostic only",
                  texture_definition="minimum baseline/shifted pair texture, gray in [0,1]",
                  texture_calibration="q20 of unshifted unchanged windows in first 40 real pairs only",
                  unchanged_support="GT has no change in target window or translated source plus 3px margin",
                  threshold_fit="first 40 images; last 10 image-disjoint held out",
                  acceptance="both estimates finite; texture > q20 and peak_ratio > threshold >= 1",
                  selection="nonzero shifts only; zero shift is an algebraic identity, reported separately",
                  limitations="Differential consistency does not establish the absolute baseline displacement; "
                              "real reference and manual checks remain necessary.",
                  eligible_rows_per_image=[int(np.count_nonzero(rows[:, 0] == i)) for i in range(50)],
                  shard_seconds=seconds, configurations=[])
    candidates = []
    for wi, window in enumerate(WINDOWS):
        baseline = rows[(rows[:, 0] < 40) & (rows[:, 2] == wi)
                        & (rows[:, 1] == 0) & (rows[:, 3] == 0), 8]
        texture_threshold = float(np.quantile(baseline, 0.2)) if len(baseline) else None
        for ni, normalization in enumerate(NORMALIZATIONS):
            r = rows[(rows[:, 2] == wi) & (rows[:, 3] == ni)]
            nonzero = r[:, 1] != 0
            train = (r[:, 0] < 40) & nonzero
            test = (r[:, 0] >= 40) & nonzero
            finite = np.isfinite(r[:, [4, 5, 7, 8, 9, 10, 11, 12, 13]]).all(1)
            texture_ok = r[:, 8] > texture_threshold if texture_threshold is not None else np.zeros(len(r), bool)
            eligible = train & finite & texture_ok
            peaks = r[eligible, 7]
            trial_thresholds = np.unique(np.r_[1.0, np.quantile(peaks, np.linspace(0, 0.99, 100))]) if len(peaks) else [1.0]
            trials, passing = [], []
            for threshold in trial_thresholds:
                keep = eligible & (r[:, 7] > threshold)
                result = summary(r, keep)
                result.update(peak_ratio_threshold=float(threshold), retention=fraction(keep.sum(), train.sum()))
                trials.append(result)
                # Avoid declaring success by retaining only a few easy blocks.
                if result["n"] and result["within_one_pixel"] >= 0.9 and result["retention"] >= 0.1:
                    passing.append(result)
            best = max(passing, key=lambda item: (item["retention"], -item["mae_xy"])) if passing else None
            item = dict(window=window, normalization=normalization, texture_threshold=texture_threshold,
                        all=summary(r, nonzero), zero_shift=summary(r, ~nonzero), thresholds=trials,
                        selected_training=best, by_shift=[])
            if best:
                keep = finite & texture_ok & (r[:, 7] > best["peak_ratio_threshold"])
                held = summary(r, keep & test)
                held["retention"] = fraction(np.count_nonzero(keep & test), np.count_nonzero(test))
                item.update(accepted=summary(r, keep & nonzero), held_out=held)
                candidates.append((item, best))
            else:
                keep = np.zeros(len(r), bool)
            for shift_id, displacement in enumerate(SHIFTS):
                same = r[:, 1] == shift_id
                item["by_shift"].append(dict(dx_dy=displacement, all=summary(r, same),
                                            accepted=summary(r, same & keep),
                                            retention=fraction(np.count_nonzero(same & keep), np.count_nonzero(same)),
                                            held_out=summary(r, same & keep & (r[:, 0] >= 40)),
                                            held_out_retention=fraction(
                                                np.count_nonzero(same & keep & (r[:, 0] >= 40)),
                                                np.count_nonzero(same & (r[:, 0] >= 40)))))
            report["configurations"].append(item)
    # Selection uses training errors only. Held-out results cannot switch the winner.
    config = dict(calibration_passed=False, seed=args.seed, images=selected, calibration_method=METHOD)
    if candidates:
        chosen, best = min(candidates, key=lambda pair: (pair[1]["mae_xy"], -pair[1]["retention"]))
        held = chosen["held_out"]
        passed = bool(held["n"] and held["within_one_pixel"] >= 0.9 and held["retention"] >= 0.1)
        config.update(calibration_passed=passed, window=chosen["window"], stride=chosen["window"] // 2,
                      normalization=chosen["normalization"], upsample_factor=4,
                      texture_threshold=chosen["texture_threshold"],
                      texture_reference="q20 of unshifted unchanged windows in first 40 real pairs",
                      peak_ratio_threshold=best["peak_ratio_threshold"],
                      minimum_valid_blocks=4, maximum_change_fraction=0.1,
                      interpolation="nearest valid grid center; Euclidean distance in pixels recorded",
                      piece_grid="area-weighted valid overlapping windows; nearest valid center only if none overlap",
                      primary_piece_method="direct",
                      primary_piece_reason="centered full-size window avoids distant grid interpolation for small/edge pieces",
                      direction="t2 displacement relative to t1: +dx right, +dy down",
                      held_out=held)
    report["recommended_config"] = config
    write_json(args.out / "synth.json", report)
    write_json(args.out / "config.json", config)
    if not config["calibration_passed"]:
        raise ValueError("Calibration failed; inspect synth.json before estimating real offsets")
    print(json.dumps(config, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", "0")))
    parser.add_argument("--shards", type=int, default=10)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.shards <= 50 or not 0 <= args.shard < args.shards:
        parser.error("Require 1 <= shards <= 50 and 0 <= shard < shards")
    selected = np.random.default_rng(args.seed).choice(names("second"), 50, replace=False).tolist()
    if args.merge:
        merge(args, selected)
    else:
        measure(args, selected)


if __name__ == "__main__":
    main()
