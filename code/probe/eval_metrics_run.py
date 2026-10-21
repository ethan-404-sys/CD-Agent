"""Reconstruct SECOND test masks and evaluate A-E in exp5-env, CPU only.

Two passes: first reproduce historical pixel counts/F1, fail on mismatch; then
compute full metrics. Fork workers share the read-only Pool and saved scores.
Estimate: 4 CPUs, 12 GB total, 2-8 h for 1694 images and 20 rows (unmeasured).
No model inference, no new thresholds, no dataset subsampling. Install in probe/.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# run.sbatch exports the requested CPU count; each image worker uses one thread.
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np
from scipy import ndimage

from common import OUT, load_prob, load_rgb, names
from exp6_pool_io import DETECTOR, load_piece_masks
from exp6b_common import Pool, SOURCES, write_json
from exp6b_eval import image_maps
from exp6f_common import load_scores
from exp6f_eval import budget_maps, dataset_mean_prediction
from metrics_cd import aggregate, distribution, image_metrics, pixel_counts, pixel_f1
from exp7_common import (ROOT as REFINE_ROOT, cache_dir, compose, diagnostic_summary,
                         sam_diagnostics, selected_raw, verify_regions)

DATASET = "second"
BG, BGH = "B+G0.05", "B+G0.05+H1"
COMBOS = {"BG": BG, "BGH1": BGH}
ROW_TITLES = {"1": "检测器保存的 0.5 预测", "3": "oracle 切块去误检",
              "5": "V1 none，B+G0.05+H1", "6": "A2 DINOv3，B+G0.05，k=0.002"}
for _row, _title in (("2", "oracle 补漏"), ("2a", "oracle 补漏，软边界"),
                     ("2b", "oracle 补漏，开闭运算"), ("4", "oracle 两路"),
                     ("4a", "oracle 两路，软边界"), ("4b", "oracle 两路，开闭运算"),
                     ("2c", "oracle 补漏，SAM3 P2 t2 clip5"), ("4c", "oracle 两路，SAM3 P2 t2 clip5")):
    for _tag, _combo in COMBOS.items():
        ROW_TITLES[f"{_row}_{_tag}"] = f"{_title}，{_combo}"
ROW_ORDER = ("1", "2_BG", "2_BGH1", "2a_BG", "2a_BGH1", "2b_BG", "2b_BGH1", "3",
             "2c_BG", "2c_BGH1", "4_BG", "4_BGH1", "4a_BG", "4a_BGH1", "4b_BG", "4b_BGH1",
             "4c_BG", "4c_BGH1", "5", "6")
PIXEL_TOLERANCE = 1e-10  # fraction, exact JSON references; also compare integer counts
ROUNDING_TOLERANCE_PP = 0.0051  # reports round to .01 percentage points
DISK2 = np.indices((5, 5)) - 2
DISK2 = (DISK2 ** 2).sum(axis=0) <= 4
STATE = {}


def read_json(path):
    return json.loads(Path(path).read_text())


def counts_add(a, b, minus):
    return {k: a[k] + b[k] - minus[k] for k in ("tp", "fp", "fn")}


def initialize(refine_root=REFINE_ROOT):
    """Read existing completed runs and frozen validation parameters only."""
    root = OUT / "exp6"
    pool = Pool(DATASET)
    if len(pool.images) != 1694 or pool.images != names(DATASET):
        raise ValueError("Expected the complete 1694-image SECOND test split")
    for tag in COMBOS:
        complete = read_json(cache_dir(refine_root, DATASET, 'O', tag, 'sam3', 'P2', 't2') / 'complete.json')
        config = complete['config']
        expected = dict(version=2, dataset=DATASET, setting='O', combo=tag, model='sam3', prompt='P2', view='t2',
                        threshold=None, run_id=None, crop_scale=3.0, crop_min=128, connectivity=8, images=pool.images)
        if complete['images'] != pool.images or config != expected:
            raise ValueError(f'{tag}: incomplete or incompatible SAM3 refinement cache')
    oracle_path = root / "pool/analysis_6a.json"
    oracle = read_json(oracle_path)["oracle"]
    removal_path = OUT / "exp6e/second/oracle.json"
    removal = read_json(removal_path)
    v1_dir = root / "verifier/none"
    v1_report = read_json(v1_dir / "eval_second.json")
    v1_cal = read_json(v1_dir / "thresholds_second_cv.json")
    threshold = v1_cal["recall"][BGH]["threshold"]
    if (v1_report["run_id"] != v1_cal["run_id"] or v1_report["images"] != 1694
            or round(threshold, 3) != 0.820
            or v1_report["applied_thresholds"]["recall"][BGH] != threshold):
        raise ValueError("V1 report/frozen validation threshold differs from the requested main result")
    scores = load_scores(v1_dir, pool, v1_report["run_id"])
    refs = {"1": oracle["metrics"]["original"], "2_BG": oracle["metrics"]["recall_BG"],
            "2_BGH1": oracle["metrics"]["recall_BGH1"], "3": removal["oracle_piece"],
            "5": v1_report["pixels"]["recall/" + BGH]}
    for other in (removal["original"], v1_report["pixels"]["baseline"]):
        check_counts("baseline reference", refs["1"], other)
    check_counts("oracle removal reference", refs["3"], oracle["metrics"]["remove_pred"])
    for tag in COMBOS:
        counts = counts_add(refs[f"2_{tag}"], refs["3"], refs["1"])
        refs[f"4_{tag}"] = dict(**counts, f1=pixel_f1(counts))
    # These are the rounded values in the required reports, in percentage points.
    for row, expected in (("2_BG", 6.96), ("2_BGH1", 8.50), ("3", 5.74), ("5", -1.31)):
        actual = 100 * (refs[row]["f1"] - refs["1"]["f1"])
        if abs(actual - expected) > ROUNDING_TOLERANCE_PP:
            raise ValueError(f"{row}: reference JSON no longer matches the report: {actual}")
    if abs(100 * refs["1"]["f1"] - 74.17) > ROUNDING_TOLERANCE_PP:
        raise ValueError("Baseline reference no longer rounds to 74.17")

    a2_dir = root / "variants/a2_dinov3"
    a2_paths = [a2_dir / "eval_second.json", a2_dir / "thresholds_second_cv.json",
                root / "verifier/dinov3/second/scores.npz"]
    missing = [str(p) for p in a2_paths if not p.is_file()]
    skipped, a2_scores, a2_k, empty_base = {}, None, None, None
    if missing:
        skipped["6"] = "缺少可复现产物：" + "; ".join(missing)
        print(skipped["6"], flush=True)
    else:
        report, calibration = map(read_json, a2_paths[:2])
        a2_k = calibration["recall"][BG]["k"]
        score_dir = Path(report["feature_config"]["scores_dir"])
        if (report["rule"] != "budget" or calibration["rule"] != "budget"
                or report["run_id"] != calibration["run_id"] or report["images"] != 1694
                or report["applied_budgets"][BG] != a2_k or a2_k != 0.002
                or score_dir != root / "verifier/dinov3"):
            raise ValueError("A2 saved budget/score source differs from the requested setting")
        a2_scores = load_scores(score_dir, pool, report["run_id"])
        empty_base = dataset_mean_prediction(pool)
        if abs(empty_base - report["pixels"]["empty_prediction_base"]) > 1e-8:
            raise ValueError("A2 current test mean prediction area differs from saved report")
        refs["6"] = report["pixels"]["recall/" + BG]
        if (abs(100 * (refs["6"]["f1"] - refs["1"]["f1"]) + 0.00954) > 0.0000051
                or refs["6"]["added_tp"] + refs["6"]["added_fp"] <= 0):
            raise ValueError("A2 does not reproduce the reported nonzero-addition setting")
    STATE.update(pool=pool, scores=scores, threshold=threshold, a2_scores=a2_scores,
                 a2_k=a2_k, empty_base=empty_base, refine_root=refine_root)
    metadata = dict(dataset=DATASET, images=1694, detector=DETECTOR[DATASET],
                    prediction="saved_0.5_mask (common.load_pred without threshold)",
                    oracle_reference=str(oracle_path), removal_reference=str(removal_path),
                    v1_directory=str(v1_dir), v1_threshold=threshold,
                    a2_directory=str(a2_dir), a2_k=a2_k, a2_empty_prediction_base=empty_base,
                    pixel_f1_tolerance=PIXEL_TOLERANCE,
                    rounded_report_tolerance_pp=ROUNDING_TOLERANCE_PP,
                    combined_reference="counts(recall) + counts(removal) - counts(baseline)",
                    soft_edge="(accepted_piece & (prob > .05)) & dilation(accepted_piece, disk2); union pieces",
                    smooth_edge="binary opening then closing of final mask, disk radius 2; outside=0",
                    object_rule="binary 8-connectivity; no semantic-transition split",
                    new_false_objects="per-image max(0, zero-overlap CC after - zero-overlap CC before)",
                    units="ratios and delta_f1 are fractions; report displays percentages/percentage points",
                    skipped=skipped, refine_root=str(refine_root), sam_view='t2', sam_prompt='P2',
                    sam_merge='clip5', sam_area='matched GT union area; no overlap separately reported')
    return pool.images, refs, metadata


def smooth(mask):
    return ndimage.binary_closing(ndimage.binary_opening(mask, DISK2, border_value=0),
                                 DISK2, border_value=0)


def reconstruct(name):
    pool = STATE["pool"]
    # Reuse the exact max-score union and saved-mask reconstruction check from 6b.
    _, gt, base, scored, _ = next(image_maps(pool, STATE["scores"], [name]))
    prob = load_prob(DATASET, DETECTOR[DATASET], name)
    masks = load_piece_masks(DATASET, name)
    added = {tag: np.zeros_like(base) for tag in COMBOS}
    removed = np.zeros_like(base)
    sl = pool.slices[name]
    for i in range(sl.start, sl.stop):
        (x0, y0, x1, y1), local = masks[int(pool.piece_id[i])]
        region = np.s_[y0:y1, x0:x1]
        source, label = SOURCES[pool.source[i]], int(pool.label[i])
        area = int(local.sum())
        fraction = float(gt[region][local].mean())
        actual_label = 1 if fraction >= 0.5 else 0 if fraction < 0.1 else -1
        if label != actual_label:
            raise ValueError(f"{name}/{pool.piece_id[i]}: stored piece label differs from current GT")
        if source == "pred":
            if area >= 10 and fraction < 0.1:
                removed[region] |= local
        elif label == 1:
            for tag, sources in (("BG", ("B", "G0.05")), ("BGH1", ("B", "G0.05", "H1"))):
                if source in sources:
                    added[tag][region] |= local
    cleaned = base & ~removed
    rows = {"1": base, "3": cleaned, "5": base | (scored[BGH] >= STATE["threshold"])}
    for tag, union in added.items():
        # Literal task wording: first remain INSIDE the pieces, then intersect
        # their two-pixel expansion. Dilation distributes over union; since the
        # union is contained in its dilation, this cannot add outside pixels.
        soft = (union & (prob > 0.05)) & ndimage.binary_dilation(union, DISK2)
        rows[f"2_{tag}"] = base | union
        rows[f"4_{tag}"] = cleaned | (union & ~base)
        rows[f"2a_{tag}"] = base | soft
        rows[f"4a_{tag}"] = cleaned | (soft & ~base)
        rows[f"2b_{tag}"] = smooth(rows[f"2_{tag}"])
        rows[f"4b_{tag}"] = smooth(rows[f"4_{tag}"])
        raw = list(selected_raw(STATE['refine_root'], DATASET, 'O', tag, 'sam3', 'P2', 't2', name))
        verify_regions(union, raw)
        refined = compose(base.shape, raw, 'clip5')
        rows[f'2c_{tag}'] = base | refined
        rows[f'4c_{tag}'] = cleaned | (refined & ~base)
    if STATE["a2_scores"] is not None:
        activation = budget_maps(pool, STATE["a2_scores"], name, base, STATE["empty_base"])[BG]
        rows["6"] = base | (activation < STATE["a2_k"])
    return gt, base, rows, int(np.count_nonzero(base != (prob > 0.5)))


def check_counts(row, actual, expected):
    if any(actual[k] != expected[k] for k in ("tp", "fp", "fn")):
        raise ValueError(f"{row}: TP/FP/FN mismatch: {actual} vs {expected}")
    error = abs(pixel_f1(actual) - expected.get("f1", pixel_f1(expected)))
    if error > PIXEL_TOLERANCE:
        raise ValueError(f"{row}: F1 difference {error} exceeds {PIXEL_TOLERANCE}")


def pixel_job(name):
    gt, _, rows, different = reconstruct(name)
    return dict(image=name, rows={k: pixel_counts(v, gt) for k, v in rows.items()},
                saved_vs_prob_pixels=different)


def metrics_job(name):
    gt, base, rows, _ = reconstruct(name)
    records = []
    baseline_f1 = pixel_f1(pixel_counts(base, gt))
    for key in ROW_ORDER:
        if key not in rows:
            continue
        result = image_metrics(rows[key], gt, before=base)
        result.update(image=name, row=key, before_f1=baseline_f1,
                      delta_f1=result["metrics"]["pixel_f1"] - baseline_f1)
        if key.startswith('2c_'):
            tag = key.split('_', 1)[1]
            raw = list(selected_raw(STATE['refine_root'], DATASET, 'O', tag, 'sam3', 'P2', 't2', name))
            result['sam_regions'] = sam_diagnostics(gt, raw)
        records.append(result)
    example = None
    if name == "test_987.png":
        example = dict(gt=gt, before=base, t2=load_rgb(DATASET, name, "t2"),
                       **{f"mask_{k}": rows[k] for k in ("1", "4_BGH1", "4a_BGH1", "4b_BGH1", "4c_BGH1")})
    return records, example


def batches(executor, job, images, workers):
    # Bound in-flight tasks/results; do not queue all 1694 full-image results.
    for start in range(0, len(images), 4 * workers):
        yield from executor.map(job, images[start:start + 4 * workers], chunksize=1)
        print(job.__name__, min(start + 4 * workers, len(images)), "/", len(images), flush=True)


def delta_summary(records):
    values = np.asarray([r["delta_f1"] for r in records])
    return dict(**distribution(values), improve_fraction=float(np.mean(values > 0)),
                decline_fraction=float(np.mean(values < 0)), unchanged_fraction=float(np.mean(values == 0)),
                worst5=[dict(image=r["image"], delta_f1=r["delta_f1"])
                        for r in sorted(records, key=lambda r: (r["delta_f1"], r["image"]))[:5]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, default=OUT / "eval_metrics_v2")
    parser.add_argument('--refine-root', type=Path, default=REFINE_ROOT)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if (args.out / "summary.json").exists():
        raise FileExistsError(f"{args.out}/summary.json already completed; use a new --out to rerun")
    started = time.monotonic()
    images, refs, config = initialize(args.refine_root)
    order = [k for k in ROW_ORDER if k not in config["skipped"]]
    args.out.mkdir(parents=True, exist_ok=True)
    totals = {k: dict(tp=0, fp=0, fn=0) for k in order}
    pixels, results = {}, {k: [] for k in order}
    with ProcessPoolExecutor(args.workers, mp_context=mp.get_context("fork")) as executor:
        for item in batches(executor, pixel_job, images, args.workers):
            pixels[item["image"]] = item
            for row, counts in item["rows"].items():
                for key, count in counts.items():
                    totals[row][key] += count
        checks = {}
        for row in order:
            if row in refs:
                check_counts(row, totals[row], refs[row])
            checks[row] = dict(counts=totals[row], f1=pixel_f1(totals[row]),
                               reference=refs.get(row),
                               status="matched" if row in refs else "new_edge_variant_no_historical_reference")
        write_json(args.out / "pixel_checks.json", dict(rows=checks, config=config,
                   saved_vs_prob_pixels=sum(p["saved_vs_prob_pixels"] for p in pixels.values())))
        print("All historical pixel counts/F1 matched; starting full metrics", flush=True)
        temp = args.out / "per_image.jsonl.tmp"
        with temp.open("w") as handle:
            for records, example in batches(executor, metrics_job, images, args.workers):
                for record in records:
                    check_counts(record["row"], record["counts"],
                                 pixels[record["image"]]["rows"][record["row"]])
                    results[record["row"]].append(record)
                    handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                if example is not None:
                    np.savez(args.out / "test_987.npz", **example)
        temp.replace(args.out / "per_image.jsonl")
    summaries = {}
    for row, records in results.items():
        if [r["image"] for r in records] != images:
            raise ValueError(f"{row}: incomplete image coverage")
        summaries[row] = dict(title=ROW_TITLES[row], **aggregate(records))
        # Preserve the original E field names for downstream consumers.
        summaries[row]['delta_f1'] = delta_summary(records)
        if row.startswith('2c_'):
            summaries[row]['sam'] = diagnostic_summary(x for r in records for x in r['sam_regions'])
    config.update(workers=args.workers, seconds=time.monotonic() - started)
    write_json(args.out / "summary.json", dict(config=config, row_order=order, rows=summaries,
                                              pixel_checks=str(args.out / "pixel_checks.json")))
    print(f"Completed: {args.out / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
