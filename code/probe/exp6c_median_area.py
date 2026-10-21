"""Estimate experiment 6c train geometry and check frozen SECOND geometry (CPU, exp5-env).

SECOND uses saved probabilities >0.5 and exp4's transition-aware missed GT objects.
second_cv reports all train images plus verifier train/val separately; second requires
--check_frozen and only checks the legacy test median. Neither changes SECOND geometry.
Other datasets use all train GT objects unless --train_predictions supplies a mask
directory with sibling prob/*.npy files, used to select misses by the same exp4 rule.
Estimated CPU resources: 1 core, 2 GiB RAM, 5-30 minutes per SECOND split (not measured;
shared storage may take longer). Images are processed one at a time; no GPU is needed.
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
from PIL import Image

from common import (DATASETS, OUT, PRED, TRAIN_DATASETS, load_label, load_pred,
                    load_prob, names, write_json)
from exp4_candidates import MIN_TARGET
from objects import gt_label, match

EXPECTED = {"levir": 445, "whu": 1260, "lsscd": 1120, "scscd7": 1400,
            "second_cv": 2968, "second": 1694}
DETECTOR = {"levir": "schanger_base", "whu": "schanger_base",
            "lsscd": "mambascd_base", "scscd7": "mambascd_base",
            "second_cv": "mambascd_base", "second": "mambascd_base"}


def missed_areas(gt, pred, gt_labels):
    """Same objects and selection as exp4_candidates.process()."""
    _, _, objects, _, _ = match(gt, pred, gt_labels)
    return [o["area"] for o in objects
            if o["coverage"] < 0.1 and o["area"] >= MIN_TARGET]


def area_summary(areas, images):
    values = np.asarray(areas)
    if not len(values):
        raise ValueError("No missed GT objects under the exp4 selection rule")
    p25, p50, p75 = np.percentile(values, [25, 50, 75]).tolist()
    return {
        "images": images, "objects": len(areas), "median_area": p50,
        "quantiles": {"p25": p25, "p50": p50, "p75": p75},
        # Use the requested literal bounds: area == 1000 belongs to the middle bin.
        "area_buckets": {"<100": int((values < 100).sum()),
                         "100-1000": int(((values >= 100) & (values <= 1000)).sum()),
                         ">1000": int((values > 1000).sum())},
    }


def second_report(dataset):
    items = names(dataset)
    if len(items) != EXPECTED[dataset]:
        raise ValueError(f"{dataset}: expected {EXPECTED[dataset]} images, found {len(items)}")
    is_train = dataset == "second_cv"
    # Equivalent to exp6_prepare_folds.py with seed=1016 and val fraction=0.10.
    val_images = set(random.Random(1017).sample(items, 297)) if is_train else set()
    groups = {"all": []}
    image_counts = {"all": len(items)}
    if is_train:
        groups.update(verifier_train=[], verifier_val=[])
        image_counts.update(verifier_train=len(items) - len(val_images),
                            verifier_val=len(val_images))
    per_image = []
    different_images = different_pixels = mask_only_pixels = prob_only_pixels = 0
    for index, name in enumerate(items, 1):
        gt = load_label(dataset, name)
        pred = load_prob(dataset, DETECTOR[dataset], name) > 0.5
        mask = load_pred(dataset, DETECTOR[dataset], name)
        if pred.shape != gt.shape or mask.shape != gt.shape:
            raise ValueError(f"{name}: probability or mask shape differs from GT {gt.shape}")
        gt_labels = gt_label(dataset, name, gt)
        retained = missed_areas(gt, pred, gt_labels)
        groups["all"].extend(retained)
        subset = ("verifier_val" if name in val_images else "verifier_train") if is_train else "test"
        if is_train:
            groups[subset].extend(retained)
        different = int(np.count_nonzero(mask != pred))
        different_images += int(different > 0)
        different_pixels += different
        mask_only_pixels += int(np.count_nonzero(mask & ~pred))
        prob_only_pixels += int(np.count_nonzero(pred & ~mask))
        per_image.append({"image": name, "subset": subset, "objects": gt_labels[1],
                          "selected_objects": len(retained), "mask_prob_different_pixels": different})
        if index % 100 == 0:
            print(f"{dataset}: {index}/{len(items)} images", flush=True)
    summaries = {key: area_summary(values, image_counts[key]) for key, values in groups.items()}
    spec = DATASETS[dataset]
    report = {
        "dataset": dataset, "split": "train" if is_train else "test",
        "root": str(spec["root"]), "dirs": spec["dirs"], "prefix": spec["prefix"],
        **summaries["all"], "subsets": summaries,
        "method": "train_missed_gt" if is_train else "test_missed_gt_check",
        "min_area": MIN_TARGET, "max_coverage_exclusive": 0.1,
        "object_definition": "8_connected_by_transition", "detector": DETECTOR[dataset],
        "probabilities": str(PRED / dataset / DETECTOR[dataset] / "prob"),
        "prediction_rule": "common.load_prob > 0.5",
        "area_bucket_bounds": "<100; 100<=area<=1000; >1000",
        "verifier_split": ({"seed": 1017, "validation_images": 297,
                            "rule": "random.Random(1017).sample(names('second_cv'), 297)"}
                           if is_train else None),
        "mask_prob_comparison": {
            "images_checked": len(items), "different_images": different_images,
            "different_pixels": different_pixels, "mask_only_pixels": mask_only_pixels,
            "prob_only_pixels": prob_only_pixels, "equivalent": different_pixels == 0,
            "note": "Masks are diagnostic only; targets always use saved probabilities >0.5.",
        },
        "test_gt_used": not is_train, "comparison_only": True, "per_image": per_image,
    }
    if not is_train:
        report["frozen_check"] = {"expected_median_area": 298.0,
                                  "matches": report["median_area"] == 298.0,
                                  "purpose": "Legacy consistency check only; no method selection."}
    filename = "second_cv_train.json" if is_train else "second_check_frozen.json"
    out = OUT / "exp6/geometry" / filename
    write_json(out, report)
    for subset, summary in summaries.items():
        print(f"{dataset}/{subset}: {summary['images']} images, {summary['objects']} objects, "
              f"median={summary['median_area']}, quantiles={summary['quantiles']}, "
              f"buckets={summary['area_buckets']}", flush=True)
    print(f"mask vs prob>0.5: {different_images} different images, "
          f"{different_pixels} different pixels; report={out}", flush=True)
    if not is_train and not report["frozen_check"]["matches"]:
        raise ValueError(f"SECOND frozen check failed: expected 298, got {report['median_area']}; {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(EXPECTED), required=True)
    parser.add_argument("--train_predictions", type=Path,
                        help="Complete train PNG mask directory with sibling prob/*.npy; "
                             "missed objects use saved probabilities >0.5 (non-SECOND only)")
    parser.add_argument("--check_frozen", action="store_true",
                        help="Only with --dataset second: recompute the test median and require 298")
    args = parser.parse_args()
    if args.check_frozen != (args.dataset == "second"):
        parser.error("--dataset second requires --check_frozen; --check_frozen is only for second")
    if args.dataset in ("second_cv", "second"):
        if args.train_predictions is not None:
            parser.error("SECOND uses the fixed preds/<dataset>/mambascd_base directory")
        second_report(args.dataset)
        return
    spec = TRAIN_DATASETS[args.dataset]
    root = spec["root"]
    items = sorted(p.name for p in (root / spec["dirs"][0]).glob(spec["prefix"] + "*.png"))
    if len(items) != EXPECTED[args.dataset]:
        raise ValueError(f"{args.dataset}: expected {EXPECTED[args.dataset]} train pairs, found {len(items)}")
    probabilities = None
    if args.train_predictions is not None:
        probabilities = args.train_predictions.parent / "prob"
        missing = [n for n in items if not (args.train_predictions / n).is_file()
                   or not (probabilities / (Path(n).stem + ".npy")).is_file()]
        if missing:
            raise FileNotFoundError(f"Train prediction mask/prob directories are incomplete: {missing[:5]}")
    out = OUT / "exp6/geometry" / f"{args.dataset}.json"
    out.unlink(missing_ok=True)
    areas, per_image = [], []
    for name in items:
        with Image.open(root / spec["dirs"][2] / name) as image:
            gt = np.asarray(image) > 127
        # Same transition-aware 8-connectivity as 6a. LEVIR/WHU load_classes returns None.
        gt_labels = gt_label(args.dataset, name, gt)
        labels, count, _ = gt_labels
        if probabilities is not None:
            prob = np.load(probabilities / (Path(name).stem + ".npy")).astype(np.float32)
            pred = prob > 0.5
            if pred.shape != gt.shape:
                raise ValueError(f"{name}: prediction {pred.shape} differs from GT {gt.shape}")
            retained = missed_areas(gt, pred, gt_labels)
        else:
            object_areas = np.bincount(labels.ravel(), minlength=count + 1)[1:]
            retained = object_areas[object_areas >= MIN_TARGET].tolist()
        areas.extend(retained)
        per_image.append({"image": name, "objects": count, "selected_objects": len(retained)})
    if not areas:
        raise ValueError(f"{args.dataset}: no train objects under the selected rule")
    method = "train_missed_gt" if args.train_predictions is not None else "train_all_gt"
    report = {
        "dataset": args.dataset, "split": "train", "root": str(root), "dirs": spec["dirs"],
        "prefix": spec["prefix"], "images": len(items), "objects": len(areas),
        "method": method, "min_area": MIN_TARGET, "median_area": float(np.median(areas)),
        "object_definition": ("8_connected_by_transition" if "class1" in spec else "8_connected_binary"),
        "detector": DETECTOR[args.dataset],
        "train_predictions": str(args.train_predictions) if args.train_predictions is not None else None,
        "probabilities": str(probabilities) if probabilities is not None else None,
        "prediction_rule": "saved probability > 0.5" if probabilities is not None else None,
        "max_coverage_exclusive": 0.1 if args.train_predictions is not None else None,
        "test_gt_used": False, "per_image": per_image,
        "second_difference": (
            "SECOND uses frozen legacy test missed-object area 298; this uses train missed objects."
            if args.train_predictions is not None else
            "SECOND uses frozen legacy test missed-object area 298; this uses all train GT objects >=20 px."
        ),
    }
    write_json(out, report)
    print(f"{args.dataset}: {len(items)} train images, {len(areas)} objects, "
          f"median={report['median_area']}, method={method}, report={out}", flush=True)


if __name__ == "__main__":
    main()
