"""Score full pools with a frozen 6f model; exp5-env, CPU or one GPU.

Estimate: 8–24 GB host RAM, 10–60 min per SECOND pool (unmeasured).
No labels are used to choose a model here. Train/validation split must match.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import torch

from exp6_pool_io import DETECTOR
from exp6b_common import Pool, predict, verifier_split
from exp6f_common import VariantFeatures, save_scores
from exp6f_train import model_for


def score_run(directory, pool, selected=None, device="cpu", batch_size=4096, check_split=True):
    directory = Path(directory)
    config = json.loads((directory / "config.json").read_text())
    checkpoint = (torch.load(directory / "model.pt", map_location="cpu", weights_only=True)
                  if config["model"] == "mlp" else joblib.load(directory / "model.joblib"))
    completed = json.loads((directory / "val_metrics.json").read_text())
    if checkpoint["run_id"] != completed["run_id"] or config != checkpoint["config"]:
        raise ValueError("Model and completed run disagree")
    if check_split:
        train, val = verifier_split()
        if checkpoint["train_images"] != train or checkpoint["val_images"] != val:
            raise ValueError("Verifier split changed")
    selected = np.ones(len(pool), bool) if selected is None else selected
    features = VariantFeatures(pool, config, directory)
    if features.config != checkpoint["feature_config"]:
        raise ValueError("Inference features differ from training")
    if config["model"] == "mlp":
        model = model_for(features, config).to(device)
        model.load_state_dict(checkpoint["state_dict"])
        scores = predict(model, features, selected, checkpoint["mean"].numpy(),
                         checkpoint["std"].numpy(), batch_size, device)
    else:
        scores = np.full(len(pool), np.nan, np.float32)
        for idx, x in features.batches(selected, batch_size):
            scores[idx] = checkpoint["estimator"].predict_proba(x)[:, 1]
    if not np.isfinite(scores[selected]).all():
        raise ValueError("Incomplete model scores")
    return scores, checkpoint["run_id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", choices=tuple(DETECTOR), required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch_size", type=int, default=4096)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch_size must be positive")
    path = args.run / f"scores_{args.dataset}.npy"
    if path.exists():
        raise FileExistsError(path)
    pool = Pool(args.dataset)
    scores, run_id = score_run(args.run, pool, device=args.device, batch_size=args.batch_size)
    save_scores(args.run, pool, scores, run_id)
    print(f"saved {path}", flush=True)


if __name__ == "__main__":
    main()
