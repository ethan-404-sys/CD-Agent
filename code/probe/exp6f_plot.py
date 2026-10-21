"""Plot 6b/6f traces and none score distributions; exp5-env, CPU.

Estimate: 1–4 GB, 1–10 min (unmeasured). Reads existing logs/scores only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from exp6b_common import Pool, SOURCES, verifier_split
from exp6f_common import load_scores


def training_curves(runs, out):
    overview, overview_axes = plt.subplots(1, 2, figsize=(11, 4))
    for directory in runs:
        records = [json.loads(line) for line in (directory / "train_log.jsonl").read_text().splitlines() if line.strip()]
        if not records:
            raise ValueError(f"{directory}: empty training log")
        epochs = [r["epoch"] for r in records]
        fig, axes = plt.subplots(2, 2, figsize=(11, 8))
        axes[0, 0].plot(epochs, [r["loss"] for r in records], label="Training")
        axes[0, 0].set(ylabel="Weighted BCE", title="Training loss")
        for key, label in (("train_eval", "Fixed training subset (eval mode)"), ("val", "Validation")):
            for field, axis in (("loss", axes[0, 1]), ("auc", axes[1, 0]), ("ap", axes[1, 1])):
                values = [(r.get(key) or {}).get(field) for r in records]
                if any(value is not None for value in values):
                    axis.plot(epochs, [np.nan if v is None else v for v in values], label=label)
        axes[0, 1].set(ylabel="Weighted BCE", title="Evaluation loss")
        axes[1, 0].set(ylabel="AUC", title="Evaluation AUC")
        axes[1, 1].set(ylabel="AP", title="Evaluation AP")
        for axis in axes.flat:
            axis.set_xlabel("Epoch / boosting iteration")
            axis.legend(fontsize=8)
        fig.suptitle(directory.name)
        fig.tight_layout()
        fig.savefig(out / f"{directory.name}_curves.png", dpi=180)
        plt.close(fig)
        overview_axes[0].plot(epochs, [r["loss"] for r in records], label=directory.name)
        overview_axes[1].plot(epochs, [r["val"]["ap"] for r in records], label=directory.name)
    for axis, ylabel in zip(overview_axes, ("Training loss", "Validation AP")):
        axis.set(xlabel="Epoch / iteration", ylabel=ylabel)
        axis.legend(fontsize=8)
    overview.tight_layout()
    overview.savefig(out / "v1_curves.png", dpi=180)
    plt.close(overview)


def distribution(directory, evaluation, out):
    calibration = json.loads((evaluation / "thresholds_second_cv.json").read_text())
    if calibration.get("rule", "threshold") != "threshold":
        raise ValueError("Score histogram requires score thresholds, not A2 k")
    _, val = verifier_split()
    fig, axis = plt.subplots(figsize=(8, 5))
    for dataset, label in (("second_cv", "Validation"), ("second", "Test")):
        pool = Pool(dataset)
        scores = load_scores(directory, pool, calibration["run_id"])
        selected = pool.source != SOURCES.index("pred")
        if dataset == "second_cv":
            selected &= pool.image_mask(val)
        axis.hist(scores[selected], bins=np.linspace(0, 1, 81), density=True,
                  histtype="step", linewidth=1.5, label=f"{label} ({selected.sum():,} recall pieces)")
    for key, result in calibration["recall"].items():
        axis.axvline(result["threshold"], linestyle="--", linewidth=1,
                     label=f"{key}: {result['threshold']:.4f}")
    axis.set(xlabel="Verifier score", ylabel="Density", title=f"{directory.name}: all recall pieces, including unlabeled")
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "none_score_distribution.png", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="*")
    parser.add_argument("--hist_run", type=Path)
    parser.add_argument("--hist_eval", type=Path, help="Threshold directory; defaults to hist_run")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.runs and args.hist_run is None:
        parser.error("Supply --runs and/or --hist_run")
    if args.runs and len({p.name for p in args.runs}) != len(args.runs):
        parser.error("Curve run directory names must be unique")
    args.out.mkdir(parents=True, exist_ok=True)
    if args.runs:
        training_curves(args.runs, args.out)
    if args.hist_run:
        distribution(args.hist_run, args.hist_eval or args.hist_run, args.out)


if __name__ == "__main__":
    main()
