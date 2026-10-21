"""Exact added-FP / detector-area versus target-recall curves; exp5-env, CPU.

Estimate: 8–24 GB, 1–4 h per model and dataset (unmeasured). Curves are diagnostic,
not test-set operating-point selection. Oracle ranks pieces by stored GT fraction.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np
from scipy import ndimage

from common import AREA_BUCKETS, area_bucket
from objects import gt_label
from exp6b_common import Pool, SOURCES, rows_by_image, save_npz, verifier_split, write_json
from exp6b_eval import COMBINATIONS, ABOVE_ONE, image_maps, threshold_grid
from exp6f_common import load_scores

BUDGETS = (0.05, 0.1, 0.2)
BUCKETS = ("all", *(b[2] for b in AREA_BUCKETS))


def oracle_scores(pool):
    scores = np.empty(len(pool), np.float32)
    for name, rows in rows_by_image(pool.dataset):
        scores[pool.slices[name]] = [row["gt_frac"] for row in rows]
    return scores


def add_histogram(histogram, values, thresholds):
    values = values[np.isfinite(values)]
    bins, counts = np.unique(np.searchsorted(thresholds, values, side="right"), return_counts=True)
    histogram[bins] += counts


def recall_curves(pool, scores, images, out, label):
    selected = pool.image_mask(images)
    grids, fps, hits = {}, {}, {}
    for key, sources in COMBINATIONS.items():
        take = selected & np.isin(pool.source, [SOURCES.index(s) for s in sources])
        # 0.5 explicitly preserves the historical piece oracle operating point.
        grids[key] = threshold_grid(np.r_[scores[take], 0.5], "recall")
        fps[key] = np.zeros(len(grids[key]) + 1, np.int64)
        hits[key] = {b: np.zeros(len(grids[key]) + 1, np.int64) for b in BUCKETS}
    totals = {b: 0 for b in BUCKETS}
    baseline_hits = {b: 0 for b in BUCKETS}
    predicted_pixels = 0
    for name, gt, base, add, _ in image_maps(pool, scores, images):
        predicted_pixels += int(base.sum())
        labels, count, _ = gt_label(pool.dataset, name, gt)
        areas = np.bincount(labels.ravel(), minlength=count + 1)
        covered = np.bincount(labels[base], minlength=count + 1)
        slices = ndimage.find_objects(labels)
        thresholds = {key: {b: [] for b in BUCKETS} for key in COMBINATIONS}
        for obj, region in enumerate(slices, 1):
            bucket = area_bucket(int(areas[obj]))
            totals["all"] += 1
            totals[bucket] += 1
            needed = int((areas[obj] + 1) // 2 - covered[obj])
            if needed <= 0:
                baseline_hits["all"] += 1
                baseline_hits[bucket] += 1
            local = (labels[region] == obj) & ~base[region]
            for key in COMBINATIONS:
                if needed <= 0:
                    threshold = ABOVE_ONE
                else:
                    values = add[key][region][local]
                    # kth largest novel-pixel score is the exact target-hit breakpoint.
                    threshold = float(np.partition(values, len(values) - needed)[len(values) - needed])
                if np.isfinite(threshold):
                    thresholds[key]["all"].append(threshold)
                    thresholds[key][bucket].append(threshold)
        for key in COMBINATIONS:
            add_histogram(fps[key], add[key][~base & ~gt], grids[key])
            for bucket in BUCKETS:
                add_histogram(hits[key][bucket], np.asarray(thresholds[key][bucket]), grids[key])
    # One cumulative scan per curve, rather than per image over millions of breakpoints.
    fps = {key: np.cumsum(value[:0:-1])[::-1] for key, value in fps.items()}
    hits = {key: {bucket: np.cumsum(value[:0:-1])[::-1] for bucket, value in buckets.items()}
            for key, buckets in hits.items()}
    report = dict(dataset=pool.dataset, model=label, images=len(images), diagnostic_only=True,
                  target_rule="all GT objects, semantic transitions when available; >=50% pixel coverage",
                  predicted_pixels=predicted_pixels, totals=totals, baseline_hits=baseline_hits,
                  operating_points="largest achieved recall under each FP budget; no interpolation or test threshold transfer",
                  combinations={})
    for key in COMBINATIONS:
        ratio = fps[key] / predicted_pixels if predicted_pixels else np.full(len(fps[key]), np.nan)
        rows = []
        for budget in BUDGETS:
            feasible = np.flatnonzero(fps[key] <= budget * predicted_pixels) if predicted_pixels else np.array([], int)
            # As threshold increases, additions decrease. Pick lowest feasible threshold.
            point = int(feasible[0]) if len(feasible) else None
            rows.append(dict(budget=budget, threshold=float(grids[key][point]) if point is not None else None,
                             added_fp=int(fps[key][point]) if point is not None else None,
                             actual_fp_ratio=float(ratio[point]) if point is not None else None,
                             target_recall={b: float(hits[key][b][point] / totals[b])
                                            if point is not None and totals[b] else None for b in BUCKETS}))
        path = out / f"{label}_{pool.dataset}_{key}.npz"
        save_npz(path, threshold=grids[key], added_fp=fps[key], fp_ratio=ratio,
                 predicted_pixels=predicted_pixels,
                 **{f"hits_{b}": hits[key][b] for b in BUCKETS},
                 **{f"total_{b}": totals[b] for b in BUCKETS})
        half = int(np.searchsorted(grids[key], 0.5))
        report["combinations"][key] = dict(curve=str(path), budgets=rows,
            oracle_05={"added_fp": int(fps[key][half]),
                       "target_recall": float(hits[key]["all"][half] / totals["all"]) if totals["all"] else None}
            if label == "oracle" else None)
    if label == "oracle":
        report["oracle_definition"] = "GT fraction ranking, including unlabeled pieces; 0.5 matches historical piece oracle; not a combinatorial upper bound"
    write_json(out / f"{label}_{pool.dataset}.json", report)
    return report


def plot_reports(reports, out):
    for key in COMBINATIONS:
        fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
        for report in reports:
            axis = axes[0 if report["dataset"] == "second_cv" else 1]
            with np.load(report["combinations"][key]["curve"], allow_pickle=False) as z:
                total = int(z["total_all"])
                if not total or not report["predicted_pixels"]:
                    continue
                # Limit rendering only; NPZ and budget points retain every breakpoint.
                use = np.flatnonzero(z["fp_ratio"] <= 0.5)
                if len(use) > 6000:
                    use = use[np.linspace(0, len(use) - 1, 6000, dtype=int)]
                axis.plot(z["fp_ratio"][use][::-1], (z["hits_all"][use] / total)[::-1], label=report["model"])
        for axis, title in zip(axes, ("Validation", "Test (diagnostic)")):
            axis.set(xlabel="Added FP / detector prediction pixels", ylabel="Target recall", title=title, xlim=(0, 0.5))
            for budget in BUDGETS:
                axis.axvline(budget, color="grey", linewidth=0.5, linestyle=":")
            axis.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(out / f"recall_{key}.png", dpi=180)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True, help="name=/absolute/score/directory")
    parser.add_argument("--oracle", action="store_true")
    parser.add_argument("--datasets", nargs="+", choices=("second_cv", "second"), default=("second_cv", "second"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = [item.split("=", 1) for item in args.runs]
    if any(len(item) != 2 or not item[0] or "/" in item[0] or item[0] == "oracle" for item in runs):
        parser.error("Runs must be unique name=directory pairs; oracle is reserved")
    if len({item[0] for item in runs}) != len(runs):
        parser.error("Duplicate run names")
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    _, val_images = verifier_split()
    for dataset in args.datasets:
        pool = Pool(dataset)
        images = val_images if dataset == "second_cv" else pool.images
        for label, directory in runs + ([("oracle", None)] if args.oracle else []):
            path = args.out / f"{label}_{dataset}.json"
            if path.exists():
                # Replot existing diagnostics without rerunning test evaluation.
                report = json.loads(path.read_text())
                if report["model"] != label or report["dataset"] != dataset:
                    raise ValueError(f"{path}: wrong diagnostic report")
            else:
                scores = oracle_scores(pool) if directory is None else load_scores(directory, pool)
                report = recall_curves(pool, scores, images, args.out, label)
            reports.append(report)
    plot_reports(reports, args.out)


if __name__ == "__main__":
    main()
