"""Train 6f-C on one GPU; exp5-env (torch 2.5.1, torchvision 0.20.1).

Estimate: 3–6 GiB CPU RAM with 4 workers (request 8 GiB); 2–7 GiB GPU at batch 256.
Only second_cv verifier_train/val images are used. --max_steps/--limit_images
are smoke modes and never save a checkpoint. No detector or backbone training.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
import uuid
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from exp6b_common import Pool, SEED, binary_metrics, verifier_split, write_json
from exp6f_c_common import CropCache, labeled_recall, loader
from exp6f_c_model import SmallCNN, prepare_inputs


@torch.inference_mode()
def evaluate(model, batches, labels, pos_weight, deadline):
    started = time.monotonic()
    model.eval()
    losses, count, truth, predictions = 0.0, 0, [], []
    for crops, rows in batches:
        if time.monotonic() >= deadline:
            raise TimeoutError("Training GPU time budget exhausted during evaluation")
        y = torch.as_tensor(labels[rows.numpy()], device="cuda", dtype=torch.float32)
        x = prepare_inputs(crops.to("cuda", non_blocking=True))
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            logits = model(x)
        loss = F.binary_cross_entropy_with_logits(logits.float(), y, pos_weight=pos_weight,
                                                  reduction="sum")
        losses += float(loss)
        count += len(rows)
        truth.append(y.cpu().numpy())
        predictions.append(logits.float().sigmoid().cpu().numpy())
    if not count:
        raise ValueError("Empty evaluation population")
    result = binary_metrics(np.concatenate(truth), np.concatenate(predictions))
    result.update(loss=losses / count, seconds=time.monotonic() - started)
    return result


def fixed_subset(positions, count, seed):
    if count == 0 or len(positions) <= count:
        return np.sort(positions)
    return np.sort(np.random.default_rng(seed).choice(positions, count, replace=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--architecture", choices=("early", "siamese"), default="early")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch_size", type=int, default=256)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight_decay", type=float, default=0.05)
    parser.add_argument("--dropout", type=float, default=0.5)
    parser.add_argument("--label_smoothing", type=float, default=0.02)
    parser.add_argument("--eval_every", type=int, default=200)
    parser.add_argument("--val_monitor", type=int, default=16384, help="0 uses all labeled validation recall rows")
    parser.add_argument("--train_monitor", type=int, default=8192)
    parser.add_argument("--patience", type=int, default=8, help="Non-improving fixed-validation checks")
    parser.add_argument("--min_delta", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--max_steps", type=int, default=0, help="Smoke only: no checkpoint")
    parser.add_argument("--limit_images", type=int, default=0, help="Smoke: first N train and N val images")
    parser.add_argument("--max_hours", type=float, default=9.0, help="Training wall/GPU budget; <=9 by default")
    parser.add_argument("--score_reserve_hours", type=float, default=2.0)
    args = parser.parse_args()
    if (min(args.epochs, args.batch_size, args.eval_every, args.patience, args.train_monitor) < 1
            or min(args.workers, args.val_monitor, args.max_steps, args.limit_images, args.seed) < 0
            or not all(math.isfinite(v) for v in (args.lr, args.weight_decay, args.dropout,
                                                 args.label_smoothing, args.min_delta,
                                                 args.max_hours, args.score_reserve_hours))
            or args.lr <= 0 or args.weight_decay < 0 or args.min_delta < 0
            or not 0 <= args.dropout < 1 or not 0 <= args.label_smoothing < 1
            or args.max_hours <= 0 or args.score_reserve_hours <= 0
            or args.max_hours + args.score_reserve_hours > 12):
        parser.error("Invalid hyperparameters or training + scoring budgets exceed 12 GPU hours")
    if not torch.cuda.is_available():
        raise RuntimeError("Single CUDA GPU required")
    started = time.monotonic()
    deadline = started + args.max_hours * 3600
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    torch.set_num_threads(1)
    torch.backends.cudnn.benchmark = False
    torch.cuda.reset_peak_memory_stats()
    pool = Pool("second_cv")
    train_images, val_images = verifier_split()
    cache = CropCache(args.cache, pool)
    if cache.spec["split"] != "fit":
        raise ValueError("Training requires a fit cache with its documented sampling policy")
    if (cache.spec["train_images"] != train_images or cache.spec["val_images"] != val_images):
        raise ValueError("Cache verifier split changed")
    if cache.smoke and not args.limit_images:
        raise ValueError("Limited cache requires explicit --limit_images smoke training")
    smoke = bool(args.max_steps or args.limit_images)
    active_train = train_images[:args.limit_images] if args.limit_images else train_images
    active_val = val_images[:args.limit_images] if args.limit_images else val_images
    # Validation coverage includes unlabeled/pred rows, though loss uses labeled recall only.
    cache.require_coverage(np.flatnonzero(pool.image_mask(active_val)))
    train_mask, val_mask = (labeled_recall(pool, images) for images in (active_train, active_val))
    train_positions = np.flatnonzero(train_mask[cache.rows])
    val_positions = np.flatnonzero(val_mask[cache.rows])
    if not len(train_positions) or not len(val_positions):
        raise ValueError("Empty train or validation set")
    for positions in (train_positions, val_positions):
        if len(np.unique(pool.label[cache.rows[positions]])) != 2:
            raise ValueError("Train and validation require both classes")
    # Every selected train image must be represented, except an image with no labeled recall.
    required_images = np.unique(pool.image_id[np.flatnonzero(train_mask)])
    if not np.array_equal(np.unique(pool.image_id[cache.rows[train_positions]]), required_images):
        raise ValueError("Missing train images in cache")
    positives = int((pool.label[cache.rows[train_positions]] == 1).sum())
    negatives = len(train_positions) - positives
    if positives != int((pool.label[train_mask] == 1).sum()):
        raise ValueError("Training cache must preserve every eligible positive")
    weight = negatives / positives
    pos_weight = torch.tensor(weight, device="cuda")
    monitor_positions = fixed_subset(train_positions, args.train_monitor, args.seed + 1)
    check_positions = fixed_subset(val_positions, args.val_monitor, args.seed + 2)
    if len(np.unique(pool.label[cache.rows[check_positions]])) != 2:
        raise ValueError("Fixed validation monitor requires both classes; increase --val_monitor")
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise FileExistsError(f"{args.out}: training output must be empty")
    run_id = str(uuid.uuid4())
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    write_json(args.out / "config.json", dict(run_id=run_id, **config, smoke=smoke,
                                             crop_config=cache.config, selection=cache.spec))
    np.savez(args.out / "monitor_rows.npz", train=cache.rows[monitor_positions],
             validation=cache.rows[check_positions])
    make_loader = lambda positions: loader(cache, positions, args.batch_size, args.workers)
    train_loader = loader(cache, train_positions, args.batch_size, args.workers, True, args.seed)
    monitor_loader, check_loader, full_loader = map(
        make_loader, (monitor_positions, check_positions, val_positions))
    model = SmallCNN(args.architecture, args.dropout).cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scaler = torch.amp.GradScaler("cuda")
    steps_per_epoch = len(train_loader)
    planned_steps = steps_per_epoch * args.epochs
    best_ap, best_step, best_epoch, best_state, bad_checks = -1.0, 0, 0, None, 0
    best_monitor = None
    step, last_check, interval_loss, interval_count = 0, -1, 0.0, 0
    forecast_written = False
    stop_reason = "epochs"

    with (args.out / "train_log.jsonl").open("w") as log:
        def record(value):
            value.update(step=step, seconds=time.monotonic() - started,
                         peak_gpu_gib=torch.cuda.max_memory_allocated() / 2**30,
                         best_epoch=best_epoch, best_step=best_step)
            log.write(json.dumps(value, allow_nan=False) + "\n")
            log.flush()
            print(f"step={step} event={value['event']} loss={value['loss']:.5f} "
                  f"val_AP={value['val']['ap']:.5f}", flush=True)

        def check(epoch_progress):
            nonlocal best_ap, best_step, best_epoch, best_state, bad_checks, best_monitor
            nonlocal last_check, interval_loss, interval_count
            val = evaluate(model, check_loader, pool.label, pos_weight, deadline)
            train_eval = evaluate(model, monitor_loader, pool.label, pos_weight, deadline)
            if val["ap"] > best_ap + args.min_delta:
                best_ap, best_step, best_epoch = val["ap"], step, epoch_progress
                best_monitor = val
                best_state = {key: value.detach().cpu().clone()
                              for key, value in model.state_dict().items()}
                bad_checks = 0
            else:
                bad_checks += 1
            record(dict(event="step", epoch=epoch_progress,
                        loss=interval_loss / interval_count, train_samples=interval_count,
                        val=val, val_scope="fixed_monitor", train_eval=train_eval,
                        bad_checks=bad_checks))
            last_check, interval_loss, interval_count = step, 0.0, 0
            return train_eval

        for epoch in range(1, args.epochs + 1):
            epoch_loss, epoch_count = 0.0, 0
            for batch_index, (crops, rows) in enumerate(train_loader):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Training GPU time budget exhausted; report before continuing")
                model.train()
                x = prepare_inputs(crops.to("cuda", non_blocking=True), augment=True)
                y = torch.as_tensor(pool.label[rows.numpy()], device="cuda", dtype=torch.float32)
                targets = y * (1 - args.label_smoothing) + args.label_smoothing / 2
                # Linear warmup followed by cosine decay, without changing the sample population.
                warmup = min(200, max(1, planned_steps // 10))
                progress = max(0.0, (step - warmup) / max(1, planned_steps - warmup))
                factor = min(1.0, (step + 1) / warmup) * (0.1 + 0.9 * (1 + math.cos(math.pi * progress)) / 2)
                for group in optimizer.param_groups:
                    group["lr"] = args.lr * factor
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(device_type="cuda", dtype=torch.float16):
                    logits = model(x)
                    loss = F.binary_cross_entropy_with_logits(logits, targets, pos_weight=pos_weight)
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite training loss")
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                step += 1
                value = float(loss.detach()) * len(rows)
                interval_loss += value
                interval_count += len(rows)
                epoch_loss += value
                epoch_count += len(rows)
                epoch_progress = epoch - 1 + (batch_index + 1) / steps_per_epoch
                if step % args.eval_every == 0:
                    train_eval = check(epoch_progress)
                if args.max_steps and step >= args.max_steps:
                    stop_reason = "smoke_max_steps"
                    break
                if bad_checks >= args.patience:
                    stop_reason = "validation_ap_patience"
                    break
            if last_check != step:
                train_eval = check(epoch_progress)
            full_val = evaluate(model, full_loader, pool.label, pos_weight, deadline)
            record(dict(event="epoch_end", epoch=epoch_progress,
                        loss=epoch_loss / epoch_count, train_samples=epoch_count,
                        val=full_val, val_scope="full_validation", train_eval=train_eval))
            if not forecast_written:
                elapsed = time.monotonic() - started
                forecast = elapsed / step * planned_steps + args.score_reserve_hours * 3600
                write_json(args.out / "time_estimate.json",
                           dict(observed_steps=step, observed_seconds=elapsed,
                                planned_steps=planned_steps, projected_total_gpu_hours=forecast / 3600,
                                score_reserve_hours=args.score_reserve_hours,
                                method="observed epoch including monitors, extrapolated without early stopping"))
                forecast_written = True
                if not smoke and stop_reason == "epochs" and forecast > 12 * 3600:
                    raise RuntimeError("Projected variant exceeds 12 GPU hours; see time_estimate.json and report")
            if stop_reason != "epochs" or bad_checks >= args.patience:
                if bad_checks >= args.patience and stop_reason == "epochs":
                    stop_reason = "validation_ap_patience"
                break
        if best_state is None:
            raise ValueError("No validation-selected model")
        model.load_state_dict(best_state)
        best_full_val = evaluate(model, full_loader, pool.label, pos_weight, deadline)
    elapsed = time.monotonic() - started
    report = dict(run_id=run_id, architecture=args.architecture, smoke=smoke,
                  selection="AP_on_fixed_validation_monitor_at_step_checks",
                  best_epoch=best_epoch, best_step=best_step, best_val_ap=best_ap,
                  selection_population=best_monitor, full_validation=best_full_val,
                  steps=step, epochs_run=epoch, stop_reason=stop_reason,
                  train=dict(images=len(active_train), n=len(train_positions), positive=positives,
                             negative=negatives, pos_weight=weight),
                  validation_images=len(active_val), validation_n=len(val_positions),
                  seconds=elapsed, peak_gpu_gib=torch.cuda.max_memory_allocated() / 2**30,
                  parameters=sum(parameter.numel() for parameter in model.parameters()))
    if not smoke:
        checkpoint = dict(run_id=run_id, architecture=args.architecture, dropout=args.dropout,
                          state_dict=best_state, config=config, crop_config=cache.config,
                          train_images=train_images, val_images=val_images,
                          best_step=best_step, best_val_ap=best_ap, seconds=elapsed)
        temporary = args.out / "model.tmp.pt"
        torch.save(checkpoint, temporary)
        temporary.replace(args.out / "model.pt")
    write_json(args.out / "val_metrics.json", report)
    print(f"completed {args.out}; smoke={smoke}; seconds={elapsed:.1f}", flush=True)


if __name__ == "__main__":
    main()
