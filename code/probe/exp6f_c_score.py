"""Score evaluation Pool rows for batch6; exp5-env, torch 2.5.1/torchvision 0.20.1.

Estimate: 2–5 GiB CPU RAM (request 8 GiB), 1–4 GiB GPU at batch 512.
Optional --cache reads uint8 shards; otherwise crops are streamed by image.
second_cv scores validation images from fit/all caches; other rows remain NaN.
Other datasets require all-cache coverage of the complete Pool.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from exp6_pool_io import DETECTOR
from exp6b_common import Pool, save_npz, verifier_split, write_json
from exp6f_common import save_scores
from exp6f_c_common import CropCache, crop_batches, loader
from exp6f_c_model import SmallCNN, prepare_inputs


def cached_batches(cache, pool, images, batch_size, workers):
    selected = pool.image_mask(images)
    cache.require_coverage(np.flatnonzero(selected))
    positions = np.flatnonzero(selected[cache.rows])
    for crops, rows in loader(cache, positions, batch_size, workers):
        yield rows.numpy(), crops


@torch.inference_mode()
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", choices=tuple(DETECTOR), required=True)
    parser.add_argument("--cache", type=Path,
                        help="Optional --split fit/all cache for second_cv; all for other datasets")
    parser.add_argument("--calibration_dir", type=Path, help="Default: --run; batch6 validation output")
    parser.add_argument("--batch_size", type=int, default=512)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max_steps", type=int, default=0, help="Smoke: only diagnostic score file")
    parser.add_argument("--limit_images", type=int, default=0, help="Smoke: first N evaluation images")
    parser.add_argument("--smoke_out", type=Path, help="Required diagnostics directory for smoke scoring")
    args = parser.parse_args()
    if args.batch_size < 1 or min(args.workers, args.max_steps, args.limit_images) < 0:
        parser.error("Invalid batch size, workers or smoke limits")
    smoke = bool(args.max_steps or args.limit_images)
    if smoke != bool(args.smoke_out):
        parser.error("Use --smoke_out together with --max_steps or --limit_images")
    out = args.smoke_out if smoke else args.run
    if not torch.cuda.is_available():
        raise RuntimeError("Single CUDA GPU required")
    started = time.monotonic()
    torch.set_num_threads(1)
    torch.cuda.reset_peak_memory_stats()
    prefix = "smoke_scores" if smoke else "scores"
    for suffix in ("npy", "npz", "json"):
        if (out / f"{prefix}_{args.dataset}.{suffix}").exists():
            raise FileExistsError(f"{prefix}_{args.dataset}.{suffix}: scoring already exists")
    checkpoint = torch.load(args.run / "model.pt", map_location="cpu", weights_only=True)
    completed = json.loads((args.run / "val_metrics.json").read_text())
    if completed["smoke"] or completed["run_id"] != checkpoint["run_id"]:
        raise ValueError("Expected a completed non-smoke training run")
    train, val = verifier_split()
    if train != checkpoint["train_images"] or val != checkpoint["val_images"]:
        raise ValueError("Verifier split changed since training")
    if args.dataset != "second_cv":
        calibration_dir = args.calibration_dir or args.run
        calibration = json.loads((calibration_dir / "thresholds_second_cv.json").read_text())
        if calibration["run_id"] != checkpoint["run_id"] or calibration["validation_images"] != val:
            raise ValueError("Freeze validation choices for this run before test scoring")
    previous_seconds = completed["seconds"]
    for path in args.run.glob("scores_*.json"):
        previous = json.loads(path.read_text())
        if previous["run_id"] != checkpoint["run_id"]:
            raise ValueError(f"{path}: scoring report belongs to another model")
        previous_seconds += previous["seconds"]
    deadline = started + 12 * 3600 - previous_seconds
    if deadline <= started:
        raise RuntimeError("Variant GPU budget exhausted; report before further scoring")
    pool = Pool(args.dataset)
    images = val if args.dataset == "second_cv" else pool.images
    if args.limit_images:
        images = images[:args.limit_images]
    selected = pool.image_mask(images)
    requested_rows = int(selected.sum())
    if args.cache:
        cache = CropCache(args.cache, pool)
        splits = ("fit", "all") if args.dataset == "second_cv" else ("all",)
        if cache.config != checkpoint["crop_config"] or cache.spec["split"] not in splits:
            raise ValueError(f"Scoring cache must use split {splits} and the trained crop configuration")
        if cache.smoke and not smoke:
            raise ValueError("Smoke cache cannot be used for formal scoring")
        batches = cached_batches(cache, pool, images, args.batch_size, args.workers)
    else:
        batches = crop_batches(pool, images, checkpoint["crop_config"], args.batch_size)
    model = SmallCNN(checkpoint["architecture"], checkpoint["dropout"]).cuda()
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    scores = np.full(len(pool), np.nan, np.float32)
    step, count = 0, 0
    for rows, crops in batches:
        if time.monotonic() >= deadline:
            raise TimeoutError("Variant reached 12 GPU hours; partial scores are not published")
        if isinstance(crops, np.ndarray):
            crops = torch.from_numpy(crops)
        x = prepare_inputs(crops.to("cuda", non_blocking=True))
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            values = model(x)
        probabilities = values.float().sigmoid().cpu().numpy()
        if not np.isfinite(probabilities).all():
            raise ValueError("Non-finite model probabilities")
        if np.isfinite(scores[rows]).any():
            raise ValueError("Duplicate scored Pool rows")
        scores[rows] = probabilities
        count += len(rows)
        step += 1
        if step % 100 == 0:
            elapsed = time.monotonic() - started
            projected = previous_seconds + elapsed * requested_rows / count
            print(f"scored={count}/{requested_rows} seconds={elapsed:.1f} "
                  f"projected_total_gpu_hours={projected / 3600:.2f}", flush=True)
            if not smoke and projected > 12 * 3600:
                write_json(args.run / f"time_estimate_{args.dataset}.json",
                           dict(run_id=checkpoint["run_id"], scored=count,
                                projected_total_gpu_hours=projected / 3600,
                                prior_gpu_seconds=previous_seconds))
                raise RuntimeError("Projected variant exceeds 12 GPU hours; report before continuing")
        if args.max_steps and step >= args.max_steps:
            break
    if not count:
        raise ValueError("No crops scored")
    if smoke:
        rows = np.flatnonzero(np.isfinite(scores))
        save_npz(out / f"{prefix}_{args.dataset}.npz", row=rows,
                 image=np.asarray(pool.images)[pool.image_id[rows]],
                 piece_id=pool.piece_id[rows], score=scores[rows],
                 dataset=args.dataset, run_id=checkpoint["run_id"], smoke=True)
    else:
        if (count != requested_rows or not np.isfinite(scores[selected]).all()
                or not np.isnan(scores[~selected]).all()):
            raise ValueError("Formal scores require all evaluation rows, including null/pred, and NaN elsewhere")
        save_scores(args.run, pool, scores, checkpoint["run_id"])
    write_json(out / f"{prefix}_{args.dataset}.json",
               dict(run_id=checkpoint["run_id"], dataset=args.dataset, smoke=smoke,
                    images=len(np.unique(pool.image_id[np.isfinite(scores)])),
                    requested_images=len(images), requested_rows=requested_rows,
                    scored_rows=count, pool_rows=len(pool), steps=step,
                    cache=str(args.cache) if args.cache else None,
                    seconds=time.monotonic() - started, prior_gpu_seconds=previous_seconds,
                    peak_gpu_gib=torch.cuda.max_memory_allocated() / 2**30))
    print(f"saved {prefix}_{args.dataset} in {out}", flush=True)


if __name__ == "__main__":
    main()
