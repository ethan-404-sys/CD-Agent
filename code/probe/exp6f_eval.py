"""Evaluate saved scores with threshold or A2 budget rules in exp5-env, CPU.

Estimate: 8–16 GB RAM, 0.5–4 h per pool (unmeasured). A2 adds sorting/mask work.
Use separate --out directories for threshold and budget. Validation must run first.
"""
from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path

import numpy as np

from common import load_pred, pixel_metrics
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import Pool, SOURCES, write_json
from exp6b_eval import (COMBINATIONS, PRED_INDEX, curve_counts, curve_summary,
                       evaluate_pixels, evaluate_scores, image_maps, pixel_counts, threshold_grid)
from exp6f_common import load_scores

DEFAULT_K = (0, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5)


def dataset_mean_prediction(pool):
    # Unlabeled dataset-wide statistic, including zero-prediction images.
    return float(np.mean([np.count_nonzero(load_pred(pool.dataset, DETECTOR[pool.dataset], name))
                          for name in pool.images]))


def budget_maps(pool, scores, name, base, empty_base):
    """Each novel pixel activates when k exceeds the preceding union area/base area.

    Whole pieces are accepted through the first piece reaching the requested budget;
    that last piece may overshoot. k=0 adds nothing. Ties use ascending piece ID.
    """
    sl = pool.slices[name]
    masks = load_piece_masks(pool.dataset, name)
    denominator = int(base.sum()) or empty_base
    maps = {}
    for key, sources in COMBINATIONS.items():
        activation = np.full(base.shape, np.inf, np.float64)
        if denominator > 0:
            indices = np.arange(sl.start, sl.stop)
            indices = indices[np.isin(pool.source[indices], [SOURCES.index(s) for s in sources])]
            order = np.lexsort((pool.piece_id[indices], -scores[indices]))
            total = 0
            for i in indices[order]:
                (x0, y0, x1, y1), local = masks[int(pool.piece_id[i])]
                region = np.s_[y0:y1, x0:x1]
                view = activation[region]
                new = local & ~base[region] & ~np.isfinite(view)
                added = int(new.sum())
                if added:
                    view[new] = total / denominator
                    total += added
        maps[key] = activation
    return maps


def calibrate_budget(pool, scores, images, out, run_id, ks):
    selected = pool.image_mask(images)
    pred = selected & (pool.source == PRED_INDEX)
    remove_grids = dict(verifier=threshold_grid(scores[pred], "remove"),
                        detector_mean=threshold_grid(pool.meta[pred, 0], "remove"))
    added = {key: np.zeros((2, len(ks)), np.int64) for key in COMBINATIONS}
    removed = {key: np.zeros((2, len(grid)), np.int64) for key, grid in remove_grids.items()}
    baseline = np.zeros(3, np.int64)
    empty_base = dataset_mean_prediction(pool)
    for name, gt, base, _, remove in image_maps(pool, scores, images):
        baseline += pixel_counts(gt, base)
        for key, activation in budget_maps(pool, scores, name, base, empty_base).items():
            for cls, mask in enumerate((gt, ~gt)):
                added[key][cls] += curve_counts(activation[~base & mask], ks, "remove")
        for key, score_map in remove.items():
            for cls, mask in enumerate((gt, ~gt)):
                removed[key][cls] += curve_counts(score_map[base & mask], remove_grids[key], "remove")
    report = dict(run_id=run_id, threshold_from="second_cv", validation_images=images,
                  rule="budget", search="validation_k_grid_max_micro_F1", k_grid=ks.tolist(),
                  baseline=pixel_metrics(*map(int, baseline)), recall={}, remove={},
                  empty_prediction_rule="mean prediction pixels over the entire current dataset; all-empty dataset adds nothing",
                  empty_prediction_base=empty_base,
                  budget_rule="whole pieces until novel union pixels reach base*k; last piece may overshoot; k=0 adds nothing")
    for key, counts in added.items():
        curve = curve_summary(ks, baseline[0] + counts[0], baseline[1] + counts[1],
                              baseline[2] - counts[0], baseline, "budget",
                              out / "curves" / f"budget_{key}.npz", x_label="k (added pixels / prediction pixels)")
        curve["k"] = curve["threshold"]  # Retain 6b report schema; rule identifies the axis.
        curve["axis"] = "k"
        report["recall"][key] = curve
    for key, counts in removed.items():
        report["remove"][key] = curve_summary(
            remove_grids[key], baseline[0] - counts[0], baseline[1] - counts[1],
            baseline[2] + counts[0], baseline, "remove", out / "curves" / f"remove_{key}.npz")
    report["best_combination"] = max(COMBINATIONS, key=lambda k: report["recall"][k]["f1"])
    write_json(out / "thresholds_second_cv.json", report)
    return report


def evaluate_budget(pool, scores, images, calibration, diagnostic_threshold):
    empty_base = dataset_mean_prediction(pool)

    def accepted(current_pool, values, name, base, frozen):
        maps = budget_maps(current_pool, values, name, base, empty_base)
        return {key: activation < frozen["recall"][key]["k"] for key, activation in maps.items()}

    result = evaluate_pixels(pool, scores, images, calibration, diagnostic_threshold, accepted_fn=accepted)
    result["empty_prediction_base"] = empty_base
    result["empty_prediction_rule"] = calibration["empty_prediction_rule"]
    result["budget_rule"] = calibration["budget_rule"]
    result["budget_base_population"] = dict(dataset=pool.dataset, images=len(pool.images), gt_used=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores_dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", choices=tuple(DETECTOR), required=True)
    parser.add_argument("--rule", choices=("threshold", "budget"), default="threshold")
    parser.add_argument("--ks", type=float, nargs="+", default=DEFAULT_K)
    args = parser.parse_args()
    ks = np.unique(np.r_[0.0, args.ks])
    if not np.isfinite(ks).all() or (ks < 0).any():
        parser.error("k values must be finite nonnegative ratios (0.1 = 10%)")
    pool = Pool(args.dataset)
    indexed = args.scores_dir / f"scores_{args.dataset}.npz"
    if not (args.scores_dir / f"scores_{args.dataset}.npy").exists():
        indexed = args.scores_dir / args.dataset / "scores.npz"
    with np.load(indexed, allow_pickle=False) as z:
        run_id = str(z["run_id"])
    scores = load_scores(args.scores_dir, pool, run_id)
    callbacks = (dict(calibrate_fn=partial(calibrate_budget, ks=ks), pixels_fn=evaluate_budget,
                      rule="budget") if args.rule == "budget" else {})
    report = evaluate_scores(pool, scores, args.out, run_id, args.scores_dir.name,
                             feature_config=dict(scores_dir=str(args.scores_dir)), **callbacks)
    print(f"saved {args.out / ('eval_' + args.dataset + '.json')} ({report['images']} images)", flush=True)


if __name__ == "__main__":
    main()
