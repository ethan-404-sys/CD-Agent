"""Produce B1 image-grouped out-of-fold baseline scores in exp5-env.

Estimate: K times tabular training, CPU HGB 16–32 GB / 3–18 h; single-GPU MLP
16–24 GB / 2–10 GPU h (unmeasured). --max_hours limits the entire stage to <12 h.
Outer held-out images never enter fitting, normalization, or early stopping.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from sklearn.model_selection import KFold, train_test_split

from exp6b_common import Pool, save_npz, verifier_split, write_json
from exp6f_score import score_run
from exp6f_train import train_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline_dir", type=Path, required=True,
                        help="Completed exp6f_train none/a1/context baseline selected on validation")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--inner_val_fraction", type=float, default=0.1)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--max_hours", type=float, default=11.5)
    args = parser.parse_args()
    if args.folds < 2 or not 0 < args.inner_val_fraction < 0.5 or args.max_hours <= 0:
        parser.error("Invalid cross-fitting parameters")
    if (args.out / "crossfit.json").exists() or (args.out / "oof_second_cv.npz").exists():
        raise FileExistsError(args.out)
    config = json.loads((args.baseline_dir / "config.json").read_text())
    completed = json.loads((args.baseline_dir / "val_metrics.json").read_text())
    if config["mode"] not in ("none", "a1", "context") or config["backbone"] != "none":
        raise ValueError("B1 baseline must be a tabular none/A1/D run")
    train_images, val_images = verifier_split()
    if completed["train_images"] != train_images or completed["val_images"] != val_images:
        raise ValueError("Baseline was not fitted on the current full verifier training split")
    start = time.monotonic()
    pool = Pool("second_cv")
    scores = np.full(len(pool), np.nan, np.float32)
    fold_id = np.full(len(pool), -1, np.int16)
    images = np.asarray(train_images)
    folds = []
    splitter = KFold(n_splits=args.folds, shuffle=True, random_state=config["seed"])
    for fold, (fit_idx, held_idx) in enumerate(splitter.split(images)):
        fit, inner_val = train_test_split(images[fit_idx], test_size=args.inner_val_fraction,
                                          random_state=config["seed"] + fold)
        held = images[held_idx].tolist()
        remaining = args.max_hours - (time.monotonic() - start) / 3600
        if remaining <= 0:
            raise RuntimeError("Cross-fitting reached --max_hours; review runtime before continuing")
        fold_config = {**config, "device": args.device, "ablate": False,
                       "importance_samples": 0, "max_hours": remaining}
        directory = args.out / f"fold_{fold}"
        result = train_run(fold_config, directory, sorted(fit.tolist()), sorted(inner_val.tolist()), pool)
        selected = pool.image_mask(held)
        predicted, run_id = score_run(directory, pool, selected, args.device,
                                      config["batch_size"], check_split=False)
        if (fold_id[selected] >= 0).any():
            raise ValueError("Duplicate held-out image assignment")
        scores[selected] = predicted[selected]
        fold_id[selected] = fold
        folds.append(dict(fold=fold, train_images=sorted(fit.tolist()),
                          early_stop_images=sorted(inner_val.tolist()), heldout_images=held,
                          run_id=run_id, best_epoch=result["best_epoch"], directory=str(directory)))
        write_json(args.out / "progress.json", dict(folds=folds, seconds=time.monotonic() - start))
    selected = pool.image_mask(train_images)
    if not np.isfinite(scores[selected]).all() or (fold_id[selected] < 0).any():
        raise ValueError("Missing out-of-fold training scores")
    save_npz(args.out / "oof_second_cv.npz", image=np.asarray(pool.images)[pool.image_id[selected]],
             piece_id=pool.piece_id[selected], score=scores[selected], fold=fold_id[selected])
    elapsed = time.monotonic() - start
    write_json(args.out / "crossfit.json", dict(
        baseline_dir=str(args.baseline_dir), baseline_run_id=completed["run_id"],
        train_images=train_images, val_images=val_images, folds=folds,
        selection="each_fold_image_grouped_inner_validation_AP; outer_heldout_never_used",
        validation_test_base="full_training_baseline", seconds=elapsed,
        gpu_seconds=elapsed if config["model"] == "mlp" and args.device.startswith("cuda") else 0.0))


if __name__ == "__main__":
    main()
