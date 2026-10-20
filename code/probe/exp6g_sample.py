"""Experiment 6g sampling: CPU, conda_envs/exp5-env.

Estimated 2–4 GB RAM and 10–30 minutes on one CPU (not measured).
Pools are processed one dataset at a time; only selected rows are retained.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np

from common import load_label, load_pred
from exp4_candidates import transition_group
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import (
    ROOT, SEED, SOURCES, Pool, rows_by_image, run_dir, source_of, verifier_split, write_json,
)

HARD = {"rebuilt (building->building)", "vegetation <-> vegetation"}
SIZES = {"S1": 60, "S2": 60, "S3": 60, "S4": 40, "S5": 40, "S6": 40}


def read_scores(pool, directory, run_id):
    path = directory / pool.dataset / "scores.npz"
    with np.load(path, allow_pickle=False) as z:
        if str(z["run_id"]) != run_id:
            raise ValueError(f"{path}: scores and frozen thresholds belong to different runs")
        indices = pool.feature_indices(z["image"], z["piece_id"])
        scores = np.full(len(pool), np.nan, np.float32)
        scores[indices] = z["score"]
    return scores


def proportional(counts, size):
    """Hamilton allocation: floor proportional quotas, then largest remainders."""
    total = sum(counts.values())
    if total < size:
        raise ValueError(f"Requested {size} pieces from a population of {total}")
    quotas = {key: size * value / total for key, value in counts.items()}
    allocation = {key: int(value) for key, value in quotas.items()}
    order = sorted(counts, key=lambda key: (-(quotas[key] - allocation[key]), key))
    for key in order[:size - sum(allocation.values())]:
        allocation[key] += 1
    return allocation


def draw(pool, population, used, size, rng, stratified=False):
    available = population & ~used
    if stratified:
        groups = {
            source: available & np.isin(pool.source, [SOURCES.index(s) for s in members])
            for source, members in (
                ("B", ("B",)), ("G0.05", ("G0.05",)),
                ("C", ("C1", "C2")), ("H1", ("H1",)),
            )
        }
        counts = {key: int(mask.sum()) for key, mask in groups.items() if mask.any()}
        allocation = proportional(counts, size)
    else:
        groups = {"all": available}
        counts = {"all": int(available.sum())}
        if counts["all"] < size:
            raise ValueError(f"Requested {size} pieces, only {counts['all']} remain")
        allocation = {"all": size}
    picked, cells = [], {}
    for key, number in allocation.items():
        indices = rng.choice(np.flatnonzero(groups[key]), number, replace=False)
        picked.extend(map(int, indices))
        cells[key] = dict(population=counts[key], sampled=number,
                          weight=counts[key] / number if number else None)
    used[picked] = True
    return picked, dict(
        population=int(population.sum()), available=int(available.sum()),
        excluded_previous=int((population & ~available).sum()),
        requested=size, sampled=len(picked), cells=cells,
    )


def collect_rows(pool, selected, scores, threshold):
    """Read metadata and pixel weights only for sampled images, using saved masks."""
    by_image = {}
    for index, stratum in selected.items():
        name = pool.images[pool.image_id[index]]
        by_image.setdefault(name, {})[int(pool.piece_id[index])] = (index, stratum)
    result = []
    for name, rows in rows_by_image(pool.dataset):
        if name not in by_image:
            continue
        masks = load_piece_masks(pool.dataset, name)
        gt = load_label(pool.dataset, name)
        base = load_pred(pool.dataset, DETECTOR[pool.dataset], name)
        for row in rows:
            if row["piece_id"] not in by_image[name]:
                continue
            index, stratum = by_image[name][row["piece_id"]]
            frac = row["gt_frac"]
            expected = 1 if frac >= 0.5 else 0 if frac < 0.1 else None
            if row["kind"] != "recall" or row["label"] != expected or expected is None:
                raise ValueError(f"{pool.dataset}/{name}: invalid labeled recall piece")
            bbox, local = masks[row["piece_id"]]
            x0, y0, x1, y1 = bbox
            if bbox != row["bbox"] or int(local.sum()) != row["area"]:
                raise ValueError(f"{name}/{row['piece_id']}: mask and metadata differ")
            novel = local & ~base[y0:y1, x0:x1]
            negative_pixels = int(np.count_nonzero(novel & ~gt[y0:y1, x0:x1]))
            record = {key: row[key] for key in (
                "image", "piece_id", "label", "gt_frac", "area", "bbox", "crop", "transition",
            )}
            record.update(
                dataset=pool.dataset, stratum=stratum, source=source_of(row),
                sampling_source="C" if source_of(row) in ("C1", "C2") else source_of(row),
                none_score=float(scores[index]), is_duplicate=False, original_id=None,
                transition_group=transition_group(row["transition"]) if row["transition"] else None,
                added_pixels=int(novel.sum()), added_fp_pixels=negative_pixels,
                in_high_negative_population=bool(
                    pool.dataset == "second" and not row["label"] and scores[index] >= threshold),
            )
            result.append(record)
    if len(result) != len(selected):
        raise ValueError("Selected pool rows were not all recovered")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "label_noise/audit/samples.json")
    parser.add_argument("--verifier", type=Path, default=run_dir("none"))
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--low-fraction", type=float, default=0.10)
    args = parser.parse_args()
    if not 0 < args.low_fraction <= 1:
        parser.error("--low-fraction must be in (0, 1]")
    if args.out.exists():
        raise FileExistsError(f"{args.out}: keep the existing audit IDs and answers together")
    train, val = verifier_split()
    calibration_path = args.verifier / "thresholds_second_cv.json"
    calibration = json.loads(calibration_path.read_text())
    if calibration["validation_images"] != val:
        raise ValueError("Frozen thresholds use a different verifier validation split")
    threshold = float(calibration["recall"]["B+G0.05+H1"]["threshold"])
    if not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid frozen recall threshold")
    rng = np.random.default_rng(args.seed)
    report = dict(
        schema_version=1, seed=args.seed, thresholds_file=str(calibration_path),
        run_id=calibration["run_id"], frozen_threshold=threshold,
        documented_threshold=0.820,
        matches_documented_rounding=bool(round(threshold, 3) == 0.820),
        source_allocation="Hamilton proportional allocation over B/G0.05/C/H1; C1+C2 pooled as C; "
                          "ties use source-name order; zero allocations are retained in metadata",
        sampling_order=list(SIZES), strata={}, populations={},
        overall_estimator="test: S1/S2 source-label cells only; train: S6 label cells only; "
                          "S3-S5 overlap those populations and are diagnostic",
    )
    samples = []
    for dataset in ("second", "second_cv"):
        pool = Pool(dataset)
        scores = read_scores(pool, args.verifier, calibration["run_id"])
        eligible = (pool.source != SOURCES.index("pred")) & (pool.label >= 0)
        if dataset == "second_cv":
            eligible &= pool.image_mask(train)
        if not np.isfinite(scores[eligible]).all() or np.any(
            (scores[eligible] < 0) | (scores[eligible] > 1)
        ):
            raise ValueError(f"{dataset}: missing/invalid none scores in the sampling population")
        report["populations"][dataset] = dict(
            images=len(pool.images) if dataset == "second" else len(train),
            labeled_recall=int(eligible.sum()),
            positive=int((eligible & (pool.label == 1)).sum()),
            negative=int((eligible & (pool.label == 0)).sum()),
            split="test" if dataset == "second" else "verifier_train",
        )
        used = np.zeros(len(pool), bool)
        selected = {}
        if dataset == "second":
            positive, negative = eligible & (pool.label == 1), eligible & (pool.label == 0)
            if not positive.any():
                raise ValueError("No test positives")
            cutoff = float(np.quantile(scores[positive], args.low_fraction))
            report["low_positive"] = dict(
                fraction=args.low_fraction, cutoff=cutoff,
                tie_rule="include all scores <= the full-test positive quantile",
            )
            hard = np.zeros(len(pool), bool)
            for name, rows in rows_by_image(dataset):
                sl = pool.slices[name]
                for index, row in enumerate(rows, sl.start):
                    t = row["transition"]
                    hard[index] = row["label"] == 1 and t is not None and transition_group(t) in HARD
            populations = {
                "S1": positive, "S2": negative, "S3": negative & (scores >= threshold),
                "S4": positive & (scores <= cutoff), "S5": positive & hard,
            }
            for stratum, population in populations.items():
                picked, info = draw(pool, population, used, SIZES[stratum], rng,
                                    stratified=stratum in ("S1", "S2"))
                info["dataset"] = dataset
                report["strata"][stratum] = info
                selected.update((index, stratum) for index in picked)
        else:
            cells = {}
            for label in (0, 1):
                picked, info = draw(pool, eligible & (pool.label == label), used, 20, rng)
                cells[str(label)] = info["cells"]["all"]
                selected.update((index, "S6") for index in picked)
            report["strata"]["S6"] = dict(
                dataset=dataset, population=int(eligible.sum()), available=int(eligible.sum()),
                excluded_previous=0, requested=40, sampled=40, cells=cells,
            )
        samples.extend(collect_rows(pool, selected, scores, threshold))
        print(dataset, len(selected), "sampled", flush=True)
        del pool, scores, eligible, used
    id_rng = random.Random(args.seed + 1000)
    for row in samples:
        row["id"] = f"{id_rng.getrandbits(128):032x}"
    duplicates = random.Random(args.seed + 2000).sample(samples, 20)
    for row in duplicates:
        samples.append({**row, "id": f"{id_rng.getrandbits(128):032x}",
                        "is_duplicate": True, "original_id": row["id"]})
    random.Random(args.seed + 3000).shuffle(samples)
    report.update(original_count=sum(SIZES.values()), duplicate_count=len(duplicates), samples=samples)
    write_json(args.out, report)
    print("threshold", threshold, "matches 0.820:", report["matches_documented_rounding"])
    print(args.out, len(samples), "entries", flush=True)


if __name__ == "__main__":
    main()
