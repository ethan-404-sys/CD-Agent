"""Train 6h-1 in exp5-env; one GPU, three independent seeds on CUDA streams.

Request 12+ GiB GPU / 16 GiB host RAM (24 GiB host recommended); shared fp16
f1/f2 cache is 3.46 GiB. Target all 21 runs ~2 GPU h, unmeasured; cumulative
4 h cap includes loading and score export. 6b optimizer/batch/early-stop defaults.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from exp6b_common import (ROOT, SOURCES, Pool, binary_metrics, grouped_metrics,
                         verifier_split, write_json)
from exp6b_train import weighted_bce
from exp6f_common import save_scores
from exp6h_common import (Budget, TemporalMLP, fit_normalization, load_cache,
                         predict, setting_name)


def eval_metrics(labels, scores, cosine, weight, lam):
    result = binary_metrics(labels, scores)
    bce = weighted_bce(labels, scores, weight)
    lt = float((1 - cosine[labels == 0]).mean())
    result.update(loss=bce, bce=bce, lt=lt, total_loss=bce + lam * lt,
                  cos_auc=binary_metrics(labels, cosine)["auc"],
                  dissimilarity_auc=binary_metrics(labels, 1 - cosine)["auc"])
    return result


def train_setting(args, mode, lam, pool, pair, meta, norm, train, val, budget):
    setting = setting_name(mode, lam)
    train_images, val_images = verifier_split()
    train_cpu, val_cpu = train.cpu().numpy(), val.cpu().numpy()
    y = torch.from_numpy(pool.label.astype(np.float32)).to(args.device)
    positive = int((pool.label[train_cpu] == 1).sum())
    negative = len(train_cpu) - positive
    if not min(positive, negative) or len(np.unique(pool.label[val_cpu])) != 2:
        raise ValueError("Training and validation need both classes")
    weight = negative / positive
    pos_weight = torch.tensor(weight, device=args.device)
    states = []
    start = time.monotonic()
    torch.cuda.reset_peak_memory_stats(args.device)
    with ExitStack() as stack:
        for seed in args.seeds:
            out = args.out_root / f"h1_{setting}_s{seed}"
            out.mkdir(parents=True, exist_ok=True)
            if any(out.iterdir()):
                raise FileExistsError(f"{out}: existing run; choose an empty run directory")
            config = dict(mode=mode, temporal_lambda=lam, seed=seed, setting=setting,
                          dropout=args.dropout, batch_size=args.batch_size, lr=args.lr,
                          weight_decay=args.weight_decay, epochs=args.epochs, patience=args.patience,
                          train_monitor=args.train_monitor, projection_hidden=256,
                          projection_dim=8 if mode == "sm" else 64, temperature=3 if mode == "sm" else None,
                          metadata="6b_none_first_11", context_used=False,
                          normalization="train_only_joint_dates_and_none_metadata", amp="float16",
                          job_id=budget.job_id)
            write_json(out / "config.json", config)
            torch.manual_seed(seed)
            dropout_rng = torch.Generator(device=args.device).manual_seed(seed + 10000)
            model = TemporalMLP(mode, args.dropout, norm, dropout_rng).to(args.device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                          weight_decay=args.weight_decay, fused=True)
            chosen = np.random.default_rng(seed + 1).choice(
                train_cpu, min(args.train_monitor, len(train_cpu)), replace=False)
            stream = torch.cuda.Stream(device=args.device)
            stream.wait_stream(torch.cuda.current_stream(args.device))
            states.append(dict(
                seed=seed, out=out, config=config, model=model, optimizer=optimizer,
                scaler=torch.amp.GradScaler("cuda"), stream=stream,
                rng=torch.Generator(device=args.device).manual_seed(seed),
                monitor=torch.as_tensor(chosen, device=args.device), monitor_cpu=chosen,
                best_ap=-1.0, best_epoch=0, stopped=False, run_id=str(uuid.uuid4()),
                log=stack.enter_context((out / "train_log.jsonl").open("w"))))
        for epoch in range(1, args.epochs + 1):
            active = [s for s in states if not s["stopped"]]
            if not active:
                break
            epoch_start = time.monotonic()
            for s in active:
                s["stream"].wait_stream(torch.cuda.current_stream(args.device))
                with torch.cuda.stream(s["stream"]):
                    s["model"].train()
                    s["order"] = train[torch.randperm(len(train), device=args.device, generator=s["rng"])]
                    s["sums"] = torch.zeros(4, device=args.device, dtype=torch.float64)
            for offset in range(0, len(train), args.batch_size):
                budget.check()
                for s in active:
                    with torch.cuda.stream(s["stream"]):
                        idx = s["order"][offset:offset + args.batch_size]
                        labels = y[idx]
                        s["optimizer"].zero_grad(set_to_none=True)
                        with torch.autocast("cuda", dtype=torch.float16):
                            logits, cosine = s["model"](pair[idx], meta[idx])
                        bce = F.binary_cross_entropy_with_logits(logits, labels, pos_weight=pos_weight)
                        negatives = (labels == 0).float()
                        nneg = negatives.sum()
                        lt_sum = ((1 - cosine) * negatives).sum()
                        lt = lt_sum / nneg.clamp_min(1)
                        loss = bce + lam * lt
                        s["scaler"].scale(loss).backward()
                        s["scaler"].step(s["optimizer"])
                        s["scaler"].update()
                        s["sums"] += torch.stack((bce.detach() * len(idx), lt_sum.detach(),
                                                 nneg, loss.detach() * len(idx))).double()
            torch.cuda.synchronize(args.device)
            for s in active:
                sums = s["sums"].cpu().numpy()
                if not np.isfinite(sums).all():
                    raise ValueError(f"{s['out']}: non-finite training loss")
                scores, cosine = predict(s["model"], pair, meta, val, args.eval_batch_size, budget)
                validation = eval_metrics(pool.label[val_cpu], scores, cosine, weight, lam)
                ts, tc = predict(s["model"], pair, meta, s["monitor"], args.eval_batch_size, budget)
                training = eval_metrics(pool.label[s["monitor_cpu"]], ts, tc, weight, lam)
                if validation["ap"] > s["best_ap"]:
                    s.update(best_ap=validation["ap"], best_epoch=epoch, best_metrics=validation,
                             best_state={k: v.detach().cpu().clone()
                                         for k, v in s["model"].state_dict().items()})
                record = dict(
                    epoch=epoch, loss=float(sums[3] / len(train)), train_bce=float(sums[0] / len(train)),
                    train_lt=float(sums[1] / sums[2]), train_samples=len(train),
                    val=validation, train_eval=training, best_epoch=s["best_epoch"],
                    seconds=time.monotonic() - start, epoch_group_seconds=time.monotonic() - epoch_start,
                    peak_gpu_bytes=torch.cuda.max_memory_allocated(args.device),
                    peak_gpu_reserved_bytes=torch.cuda.max_memory_reserved(args.device),
                    timing_scope="shared_setting_wall_time", job_id=budget.job_id,
                    loss_kind="weighted_BCE_plus_lambda_Lt",
                    train_lt_population="all_negative_training_pieces",
                    scaler_scale=s["scaler"].get_scale())
                s["log"].write(json.dumps(record, allow_nan=False) + "\n")
                s["log"].flush()
                s["epoch"] = epoch
                s["seconds"] = record["seconds"]
                s["stopped"] = epoch - s["best_epoch"] >= args.patience
                print(f"{s['out'].name} epoch={epoch} BCE={record['train_bce']:.5f} "
                      f"Lt={record['train_lt']:.5f} valAP={validation['ap']:.5f}", flush=True)
            budget.check(force=True)
        for s in states:
            checkpoint = dict(
                run_id=s["run_id"], config=s["config"], feature_config=args.feature_config,
                state_dict=s["best_state"], best_epoch=s["best_epoch"], best_val_ap=s["best_ap"],
                pos_weight=weight, train_images=train_images, val_images=val_images)
            torch.save(checkpoint, s["out"] / "model.tmp.pt")
            (s["out"] / "model.tmp.pt").replace(s["out"] / "model.pt")
            report = dict(
                run_id=s["run_id"], config=s["config"], best_epoch=s["best_epoch"],
                epochs_run=s["epoch"], selection="validation_AP", selection_population=s["best_metrics"],
                train=dict(n=len(train), positive=positive, negative=negative, pos_weight=weight,
                           images=len(train_images), positive_rate=positive / len(train)),
                validation_images=len(val_images), seconds=s["seconds"], job_id=budget.job_id,
                timing_scope="overlapping_seed_wall_time_do_not_sum",
                peak_gpu_bytes=torch.cuda.max_memory_allocated(args.device),
                log=str(s["out"] / "train_log.jsonl"), feature_config=args.feature_config)
            write_json(s["out"] / "val_metrics.json", report)
    return [s["out"] for s in states]


def export_scores(runs, pool, pair, meta, feature_config, args, budget):
    images = verifier_split()[1] if pool.dataset == "second_cv" else pool.images
    selected = pool.image_mask(images)
    indices = torch.as_tensor(np.flatnonzero(selected), device=args.device)
    for directory in runs:
        budget.check()
        checkpoint = torch.load(directory / "model.pt", map_location="cpu", weights_only=True)
        completed = json.loads((directory / "val_metrics.json").read_text())
        train_images, val_images = verifier_split()
        if (checkpoint["run_id"] != completed["run_id"] or checkpoint["config"] != completed["config"]
                or checkpoint["train_images"] != train_images or checkpoint["val_images"] != val_images):
            raise ValueError("Checkpoint, completed run and current split must agree")
        if checkpoint["feature_config"] != feature_config:
            raise ValueError("Training and scoring feature configurations differ")
        config = checkpoint["config"]
        norm = {name: checkpoint["state_dict"][name] for name in ("f_mean", "f_std", "m_mean", "m_std")}
        model = TemporalMLP(config["mode"], config["dropout"], norm).to(args.device)
        model.load_state_dict(checkpoint["state_dict"])
        scores, cosine = predict(model, pair, meta, indices, args.eval_batch_size, budget)
        for target, values in ((directory, scores), (directory / "cos", cosine)):
            full = np.full(len(pool), np.nan, np.float32)
            full[selected] = values
            save_scores(target, pool, full, checkpoint["run_id"])
        # This reports validation only. No test labels or metrics are accessed here.
        if pool.dataset == "second_cv":
            full = np.full(len(pool), np.nan, np.float32)
            full[selected] = scores
            report = json.loads((directory / "val_metrics.json").read_text())
            report["recall"] = grouped_metrics(pool, full, selected)
            write_json(directory / "val_metrics.json", report)
        del model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("cos", "sm"), default="cos")
    parser.add_argument("--lambdas", type=float, nargs="+", default=[0, 0.1, 0.3, 1.0])
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--out_root", type=Path, default=ROOT / "variants")
    parser.add_argument("--budget_file", type=Path, default=ROOT / "variants/h1_gpu_budget.json")
    parser.add_argument("--max_gpu_hours", type=float, default=4)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=7)
    parser.add_argument("--batch_size", type=int, default=2048)
    parser.add_argument("--eval_batch_size", type=int, default=16384)
    parser.add_argument("--train_monitor", type=int, default=300000)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight_decay", type=float, default=1e-4)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--score_test", action="store_true", help="Write test scores without computing test metrics")
    parser.add_argument("--score_runs", type=Path, nargs="+", help="Score existing checkpoints; no training")
    parser.add_argument("--dataset", choices=("second_cv", "second"), default="second")
    args = parser.parse_args()
    if (len(set(args.seeds)) != len(args.seeds) or len(set(args.lambdas)) != len(args.lambdas)
            or any(v not in (0, 0.1, 0.3, 1.0) for v in args.lambdas)
            or (args.mode == "sm" and 0 in args.lambdas)
            or min(args.seeds) < 0 or not 0 < args.max_gpu_hours <= 4
            or min(args.epochs, args.patience, args.batch_size, args.eval_batch_size, args.train_monitor) < 1
            or args.lr <= 0 or args.weight_decay < 0 or not 0 <= args.dropout < 1):
        parser.error("Invalid 6h-1 settings (sm requires --lambdas 0.1 0.3 1)")
    if not args.device.startswith("cuda") or not torch.cuda.is_available():
        parser.error("Submit this training/scoring script to one CUDA GPU")
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    torch.cuda.set_device(torch.device(args.device))
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    with Budget(args.budget_file, args.max_gpu_hours) as budget:
        if args.score_runs:
            pool = Pool(args.dataset)
            pair, meta, config = load_cache(pool, args.device, budget)
            export_scores(args.score_runs, pool, pair, meta, config, args, budget)
            return
        pool = Pool("second_cv")
        pair, meta, args.feature_config = load_cache(pool, args.device, budget)
        train_images, val_images = verifier_split()
        eligible = (pool.label >= 0) & (pool.source != SOURCES.index("pred"))
        train = torch.as_tensor(np.flatnonzero(eligible & pool.image_mask(train_images)), device=args.device)
        val = torch.as_tensor(np.flatnonzero(eligible & pool.image_mask(val_images)), device=args.device)
        if not len(train) or not len(val):
            raise ValueError("Training and validation populations must be nonempty")
        norm = fit_normalization(pair, meta, train)
        runs = []
        for lam in args.lambdas:
            budget.check()
            current = train_setting(args, args.mode, lam, pool, pair, meta, norm, train, val, budget)
            export_scores(current, pool, pair, meta, args.feature_config, args, budget)
            runs.extend(current)
        if args.score_test:
            del pair, meta, pool, train, val, norm
            torch.cuda.empty_cache()
            pool = Pool("second")
            pair, meta, config = load_cache(pool, args.device, budget, args.feature_config)
            export_scores(runs, pool, pair, meta, config, args, budget)


if __name__ == "__main__":
    main()
