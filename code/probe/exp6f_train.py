"""Train A1/D/B1/B2 in exp5-env; selection uses image-disjoint validation AP.

Unmeasured estimates: MLP one GPU, 16–32 GB host RAM, 0.5–4 h; HGB CPU 8 cores,
16–32 GB RAM, 1–6 h. D adds seven ablation runs. Limit each GPU run
with --max_hours and Slurm time <=12 h; B1 cross-fitting is a separate stage.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import time
import uuid
from pathlib import Path

import joblib
import numpy as np
import torch
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from torch import nn

from exp6b_common import (ROOT, SEED, SOURCES, Pool, binary_metrics, classifier,
                         grouped_metrics, normalization, predict, verifier_split, write_json)
from exp6b_train import weighted_bce
from exp6f_common import VariantFeatures, ResidualMLP, fit_pca
from exp6f_context import GROUPS


def model_for(features, config):
    return (ResidualMLP(features.input_dim, config["width"], config["dropout"])
            if config["mode"] == "residual" else classifier(features.input_dim, config["dropout"]))


def metric(labels, scores, weight):
    result = binary_metrics(labels, scores)
    result["loss"] = weighted_bce(labels, scores, weight)
    return result


def table_values(features, selected, batch_size):
    indices = np.flatnonzero(selected)
    values = np.empty((len(indices), features.input_dim), np.float32)
    for idx, x in features.batches(selected, batch_size):
        values[np.searchsorted(indices, idx)] = x
    return indices, values


def train_run(config, out, train_images, val_images, pool=None):
    out = Path(out)
    if any((out / name).exists() for name in ("train_log.jsonl", "model.pt", "model.joblib", "val_metrics.json")):
        raise FileExistsError(f"{out}: existing training run")
    if set(train_images) & set(val_images):
        raise ValueError("Training and early stopping images overlap")
    out.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    prior_gpu_seconds = 0.0
    if config["mode"] == "residual" and config["device"].startswith("cuda"):
        crossfit = json.loads((Path(config["oof_dir"]) / "crossfit.json").read_text())
        prior_gpu_seconds = crossfit["gpu_seconds"]
    random.seed(config["seed"])
    np.random.seed(config["seed"])
    torch.manual_seed(config["seed"])
    rng = np.random.default_rng(config["seed"])
    pool = Pool("second_cv") if pool is None else pool
    eligible = (pool.label >= 0) & (pool.source != SOURCES.index("pred"))
    train = eligible & pool.image_mask(train_images)
    val = eligible & pool.image_mask(val_images)
    positive = int((pool.label[train] == 1).sum())
    negative = int((pool.label[train] == 0).sum())
    if min(positive, negative) == 0 or len(np.unique(pool.label[val])) != 2:
        raise ValueError("Train and validation must each have both classes")
    weight = negative / positive
    run_id = str(uuid.uuid4())
    write_json(out / "config.json", config)
    if config["mode"] == "pca":
        fit_pca(pool, config["backbone"], train, config["components"], config["batch_size"], out / "pca.npz")
    features = VariantFeatures(pool, config, out, training=True)
    monitor = np.zeros(len(pool), bool)
    chosen = np.flatnonzero(train)
    chosen = np.random.default_rng(config["seed"] + 1).choice(
        chosen, min(config["train_monitor"], len(chosen)), replace=False)
    monitor[chosen] = True
    best_ap, best_epoch, best_state, best_scores = -1.0, 0, None, None
    if config["model"] == "mlp":
        mean, std = normalization(features, train, config["batch_size"])
        if config["mode"] == "residual":
            mean[-1], std[-1] = 0, 1  # Frozen baseline logit passes through unchanged.
        model = model_for(features, config).to(config["device"])
        optimizer = torch.optim.AdamW(model.parameters(), lr=config["lr"], weight_decay=config["weight_decay"])
        criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight, device=config["device"]))
    else:
        train_idx, x_train = table_values(features, train, config["batch_size"])
        val_idx, x_val = table_values(features, val, config["batch_size"])
        monitor_local = np.searchsorted(train_idx, chosen)
        model = HistGradientBoostingClassifier(
            learning_rate=config["hgb_lr"], max_iter=1, max_leaf_nodes=config["max_leaf_nodes"],
            l2_regularization=config["weight_decay"], min_samples_leaf=config["min_samples_leaf"],
            early_stopping=False, warm_start=True, random_state=config["seed"])
        sample_weight = np.where(pool.label[train_idx] == 1, weight, 1.0)
    log_path = out / "train_log.jsonl"
    with log_path.open("w") as log:
        # Epoch zero is a selectable frozen baseline for residual models.
        first = 0 if config["mode"] == "residual" else 1
        for epoch in range(first, config["epochs"] + 1):
            if prior_gpu_seconds + time.monotonic() - start >= config["max_hours"] * 3600:
                write_json(out / "interrupted.json", dict(reason="max_hours", epoch=epoch,
                           seconds=time.monotonic() - start, best_epoch=best_epoch))
                raise RuntimeError("Training exceeded --max_hours; review timing before resubmission")
            if config["model"] == "mlp":
                loss_sum, count = 0.0, 0
                if epoch:
                    model.train()
                    for idx, x in features.batches(train, config["batch_size"], rng):
                        tensor = torch.from_numpy((x - mean) / std).to(config["device"])
                        labels = torch.from_numpy(pool.label[idx].astype(np.float32)).to(config["device"])
                        optimizer.zero_grad(set_to_none=True)
                        loss = criterion(model(tensor).squeeze(1), labels)
                        if not torch.isfinite(loss):
                            raise ValueError("Non-finite training loss")
                        loss.backward()
                        optimizer.step()
                        loss_sum += float(loss.detach()) * len(idx)
                        count += len(idx)
                scores = predict(model, features, val, mean, std, config["batch_size"], config["device"])
                train_scores = predict(model, features, monitor, mean, std, config["batch_size"], config["device"])
                train_eval = metric(pool.label[monitor], train_scores[monitor], weight)
                if epoch:
                    train_loss = loss_sum / count
                else:
                    initial = predict(model, features, train, mean, std, config["batch_size"], config["device"])
                    train_loss = weighted_bce(pool.label[train], initial[train], weight)
            else:
                model.set_params(max_iter=epoch)
                model.fit(x_train, pool.label[train_idx], sample_weight=sample_weight)
                scores = np.full(len(pool), np.nan, np.float32)
                scores[val_idx] = model.predict_proba(x_val)[:, 1]
                train_scores = model.predict_proba(x_train)[:, 1]
                train_loss = weighted_bce(pool.label[train_idx], train_scores, weight)
                train_eval = metric(pool.label[chosen], train_scores[monitor_local], weight)
            validation = metric(pool.label[val], scores[val], weight)
            if validation["ap"] > best_ap:
                best_ap, best_epoch, best_scores = validation["ap"], epoch, scores.copy()
                best_state = ({k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                              if config["model"] == "mlp" else copy.deepcopy(model))
            record = dict(epoch=epoch, loss=train_loss, train_samples=int(train.sum()),
                          val=validation, train_eval=train_eval, best_epoch=best_epoch,
                          seconds=time.monotonic() - start,
                          loss_population="all_training_pieces", loss_kind="weighted_BCE")
            log.write(json.dumps(record, allow_nan=False) + "\n")
            log.flush()
            print(f"{out.name} epoch {epoch}: loss={train_loss:.5f} val AP={validation['ap']:.5f} best={best_epoch}", flush=True)
            if epoch - best_epoch >= config["patience"]:
                break
    checkpoint = dict(run_id=run_id, config=config, feature_config=features.config,
                      input_dim=features.input_dim, train_images=train_images, val_images=val_images,
                      best_epoch=best_epoch, best_val_ap=best_ap, pos_weight=weight)
    if config["model"] == "mlp":
        checkpoint.update(state_dict=best_state, mean=torch.from_numpy(mean), std=torch.from_numpy(std))
        torch.save(checkpoint, out / "model.tmp.pt")
        (out / "model.tmp.pt").replace(out / "model.pt")
    else:
        checkpoint["estimator"] = best_state
        joblib.dump(checkpoint, out / "model.tmp.joblib")
        (out / "model.tmp.joblib").replace(out / "model.joblib")
        if config["importance_samples"]:
            take = np.random.default_rng(config["seed"] + 2).choice(
                len(x_val), min(config["importance_samples"], len(x_val)), replace=False)
            importance = permutation_importance(
                best_state, x_val[take], pool.label[val_idx[take]],
                scoring={"auc": "roc_auc", "ap": "average_precision"},
                n_repeats=config["importance_repeats"], random_state=config["seed"], n_jobs=1)
            write_json(out / "importance.json", dict(
                population="validation_only", samples=len(take), features=features.names,
                repeats=config["importance_repeats"],
                metrics={k: dict(mean=v.importances_mean.tolist(), std=v.importances_std.tolist())
                         for k, v in importance.items()}))
    report = dict(run_id=run_id, backbone=config["backbone"], variant=config["mode"],
                  include_pred=False, best_epoch=best_epoch, epochs_run=epoch,
                  selection="validation_AP", selection_population=binary_metrics(pool.label[val], best_scores[val]),
                  train=dict(images=len(train_images), n=int(train.sum()), positive=positive,
                             negative=negative, positive_rate=positive / (positive + negative), pos_weight=weight),
                  validation_images=len(val_images), train_images=train_images, val_images=val_images,
                  recall=grouped_metrics(pool, best_scores, val), input_dim=features.input_dim,
                  feature_config=features.config, seconds=time.monotonic() - start,
                  log=str(log_path), model=config["model"], prior_crossfit_gpu_seconds=prior_gpu_seconds)
    write_json(out / "val_metrics.json", report)
    return report


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("none", "a1", "context", "residual", "pca"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--backbone", choices=("none", "dinov3", "sam_vith", "remoteclip"), default="none")
    parser.add_argument("--model", choices=("mlp", "hgb"), default="mlp")
    parser.add_argument("--rank", action="store_true")
    parser.add_argument("--context_dir", default=str(ROOT / "variants/context"))
    parser.add_argument("--drop_group", choices=("probability", "geometry", *GROUPS))
    parser.add_argument("--ablate", action="store_true", help="Train full context and every leave-one-group-out run")
    parser.add_argument("--baseline_dir")
    parser.add_argument("--oof_dir")
    parser.add_argument("--components", type=int, choices=(16, 32), default=32)
    parser.add_argument("--epochs", type=int, help="Default MLP 50, HGB 200")
    parser.add_argument("--patience", type=int, default=7)
    parser.add_argument("--batch_size", type=int, default=2048)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight_decay", type=float, help="Default 1e-4; residual 1e-2; HGB 1")
    parser.add_argument("--dropout", type=float, help="Default 0.15; residual 0.5")
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--train_monitor", type=int, default=300000)
    parser.add_argument("--hgb_lr", type=float, default=0.1)
    parser.add_argument("--max_leaf_nodes", type=int, default=31)
    parser.add_argument("--min_samples_leaf", type=int, default=50)
    parser.add_argument("--importance_samples", type=int, default=30000)
    parser.add_argument("--importance_repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--max_hours", type=float, default=11.5)
    args = parser.parse_args()
    if args.epochs is None:
        args.epochs = 200 if args.model == "hgb" else 50
    if args.dropout is None:
        args.dropout = 0.5 if args.mode == "residual" else 0.15
    if args.weight_decay is None:
        args.weight_decay = 1.0 if args.model == "hgb" else 1e-2 if args.mode == "residual" else 1e-4
    if (min(args.epochs, args.patience, args.batch_size, args.train_monitor, args.width,
            args.min_samples_leaf, args.importance_repeats) < 1 or args.max_leaf_nodes < 2
            or args.importance_samples < 0 or not 0 <= args.dropout < 1
            or min(args.lr, args.hgb_lr, args.max_hours) <= 0 or args.weight_decay < 0):
        parser.error("Invalid training parameters")
    if args.mode == "context" and args.backbone != "none":
        parser.error("D uses tabular features: --backbone none")
    if args.model == "hgb" and args.mode not in ("none", "a1", "context"):
        parser.error("HGB is a tabular baseline only")
    if args.model == "hgb" and args.backbone != "none":
        parser.error("HGB requires --backbone none")
    if args.mode in ("pca", "residual") and args.backbone == "none":
        parser.error("B1/B2 require --backbone dinov3 (or another frozen backbone)")
    if (args.ablate or args.drop_group) and args.mode != "context":
        parser.error("Ablations require context mode")
    if args.ablate and args.drop_group:
        parser.error("Use --ablate or --drop_group")
    if args.mode == "residual" and (not args.baseline_dir or not args.oof_dir):
        parser.error("B1 requires --baseline_dir and --oof_dir")
    return args


def main():
    args = arguments()
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    if args.mode == "residual":
        report = json.loads((Path(args.baseline_dir) / "val_metrics.json").read_text())
        config["baseline_run_id"] = report["run_id"]
    train_images, val_images = verifier_split()
    pool = Pool("second_cv")
    result = train_run(config, args.out, train_images, val_images, pool)
    if args.ablate:
        rows = [dict(group="full", **result["selection_population"], directory=str(args.out))]
        for group in ("probability", "geometry", *GROUPS):
            variant = {**config, "drop_group": group, "ablate": False}
            target = args.out / "ablations" / group
            report = train_run(variant, target, train_images, val_images, pool)
            value = report["selection_population"]
            rows.append(dict(group=group, **value, directory=str(target),
                             delta_auc=value["auc"] - result["selection_population"]["auc"],
                             delta_ap=value["ap"] - result["selection_population"]["ap"]))
        write_json(args.out / "ablations.json", dict(population="validation_only", rows=rows))


if __name__ == "__main__":
    main()
