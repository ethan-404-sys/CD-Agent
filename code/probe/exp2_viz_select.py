"""Recompose the box-prompt oracle on CPU, verify totals, and select examples."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import numpy as np

from common import OUT, load_label, load_pred, names, pixel_metrics, write_json
from exp2_compose import iou
from objects import gt_label, match

DETECTORS = {"levir": "schanger_base", "whu": "schanger_small",
             "sysu": "schanger_small", "second": "mambascd_base"}
SETTINGS = ("original", "2a", "2a_strict", "2a_select", "2b", "both_select")


def read_prompts(dataset):
    grouped = defaultdict(list)
    with (OUT / "exp2" / dataset / "prompts.jsonl").open() as f:
        for line in f:
            row = json.loads(line)
            grouped[row["image"]].append(row)
    return grouped


def score_masks(masks, gt):
    scores = {}
    for setting, mask in masks.items():
        scores[setting] = pixel_metrics(int((mask & gt).sum()), int((mask & ~gt).sum()),
                                        int((~mask & gt).sum()))
    return add_deltas(scores)


def add_deltas(scores):
    for value in scores.values():
        value["delta_f1"] = value["f1"] - scores["original"]["f1"]
    return scores


def compose(name, dataset, prompts):
    """exp2_compose.process logic, restricted to one detector and variant=box.

    Keep deletion and union passes separate, including shared predicted components.
    Return masks and object facts so selection and figures use the same computation.
    """
    gt = load_label(dataset, name)
    pred = load_pred(dataset, DETECTORS[dataset], name)
    gt_labels = gt_label(dataset, name, gt)
    gl, pl, gobjs, _, _ = match(gt, pred, gt_labels)
    rows = {row["id"]: row for row in prompts}
    sam, sam_iou, facts = {}, {}, {}
    with np.load(OUT / "exp2" / dataset / "sam3" / name.replace(".png", ".npz")) as stored:
        for g, row in rows.items():
            obj = gl == g
            left, top, right, bottom = row["crop"]
            best = None
            view_iou = {}
            for view in row["views"]:
                full = np.zeros_like(gt)
                full[top:bottom, left:right] = stored[f"{g}_{view}_box"]
                view_iou[view] = iou(full, obj)
                if best is None or view_iou[view] > best[0]:
                    best = (view_iou[view], view, full)
            sam[g], sam_iou[g] = best[2], best[0]
            facts[g] = {"chosen_view": best[1], "sam_iou": best[0], "view_iou": view_iou,
                        "crop": row["crop"], "point": row["point"], "kind": row["kind"]}
    cls = {o["id"]: o for o in gobjs}
    hits = [g for g, o in cls.items() if o["class"] == "hit" and g in sam]
    misses = [g for g, o in cls.items() if o["class"] == "miss" and g in sam]
    strict = pred.copy()
    refined_hits = pred.copy()
    for g in hits:
        component = pl == cls[g]["best_pred"]
        strict &= ~component
        refined_hits &= ~(component & ~(gt & (gl != g)))
    for g in hits:
        strict |= sam[g]
        refined_hits |= sam[g]
    select = pred.copy()
    better = [g for g in hits if sam_iou[g] > cls[g]["best_iou"]]
    for g in better:
        select &= ~((pl == cls[g]["best_pred"]) & ~(gt & (gl != g)))
    for g in better:
        select |= sam[g]
    add_miss = np.zeros_like(gt)
    for g in misses:
        add_miss |= sam[g]
    masks = {"original": pred, "2a": refined_hits, "2a_strict": strict,
             "2a_select": select, "2b": pred | add_miss, "both_select": select | add_miss}
    objects = []
    for o in gobjs:
        g = o["id"]
        objects.append({**o, "det_iou": o["best_iou"], "prompted": g in sam,
                        "chosen_view": None, "sam_iou": None, **facts.get(g, {}),
                        "treatment": {"2a": "replace" if g in hits else "keep",
                                      "2a_select": "replace" if g in better else "keep",
                                      "2b": "add" if g in misses else "none"}})
    return {"gt": gt, "pred": pred, "gl": gl, "pl": pl, "sam": sam,
            "objects": objects, "masks": masks, "settings": score_masks(masks, gt)}


def per_image(item, dataset):
    name, prompts = item
    if prompts:
        result = compose(name, dataset, prompts)
        gt_pixels = int(result["gt"].sum())
        scores = result["settings"]
    else:
        # exp2_compose.main treats every image absent from prompts as FP-only.
        gt_pixels = int(load_label(dataset, name).sum())
        fp = int(load_pred(dataset, DETECTORS[dataset], name).sum())
        scores = add_deltas({s: pixel_metrics(0, fp, 0) for s in SETTINGS})
    return {"image": name, "gt_pixels": gt_pixels, "has_prompts": bool(prompts),
            "settings": scores}


def verify_totals(dataset, rows):
    path = OUT / "exp2" / dataset / "metrics.json"
    reference = json.loads(path.read_text())["pixel"]
    comparisons, failures = {}, []
    for setting in SETTINGS:
        key = f"{DETECTORS[dataset]}|box|{setting}"
        sums = [sum(r["settings"][setting][k] for r in rows) for k in ("tp", "fp", "fn")]
        actual = pixel_metrics(*sums)
        expected = reference[key]
        ok = all(actual[k] == expected[k] for k in ("tp", "fp", "fn"))
        ok = ok and all(math.isclose(actual[k], expected[k], rel_tol=0, abs_tol=1e-12)
                        for k in ("f1", "precision", "recall", "iou"))
        print(f"{key}: recomputed={actual} stored={expected} {'OK' if ok else 'MISMATCH'}", flush=True)
        comparisons[setting] = {"recomputed": actual, "stored": expected, "match": ok}
        if not ok:
            failures.append(key)
    if failures:
        raise RuntimeError("metrics.json mismatch: " + ", ".join(failures))
    return comparisons


def select_images(dataset, rows, comparisons):
    eligible = sorted((r for r in rows if r["gt_pixels"] >= 1000), key=lambda r: r["image"])
    if not eligible:
        raise ValueError(f"{dataset}: no image has >= 1000 GT change pixels")
    selections = {}
    for role, setting, sign in (("help", "2a_select", -1), ("hurt", "2a", 1), ("fill", "2b", -1)):
        row = min(eligible, key=lambda r: sign * r["settings"][setting]["delta_f1"])
        selections[role] = {**row, "target_setting": setting,
                            "delta_f1": row["settings"][setting]["delta_f1"]}
    if dataset == "whu":
        ordered = sorted(rows, key=lambda r: r["image"])
        preferred = next((r for r in ordered if Path(r["image"]).stem == "test_255"), None)
        row = preferred if preferred is not None else max(
            ordered, key=lambda r: r["settings"]["2a"]["tp"] - r["settings"]["2a_strict"]["tp"])
        selections["strict"] = {**row, "tp_lost": row["settings"]["2a"]["tp"] - row["settings"]["2a_strict"]["tp"]}
    roles_by_image = defaultdict(list)
    for role, row in selections.items():
        roles_by_image[row["image"]].append(role)
        print(f"{dataset} {role}: {row['image']} "
              + " ".join(f"{s} dF1={100 * v['delta_f1']:+.4f}pp" for s, v in row["settings"].items()), flush=True)
    duplicates = {name: roles for name, roles in roles_by_image.items() if len(roles) > 1}
    if duplicates:
        print(f"Repeated selections allowed: {duplicates}", flush=True)
    return {"dataset": dataset, "detector": DETECTORS[dataset], "variant": "box",
            "units": {"f1": "fraction", "delta_f1": "fraction; multiply by 100 for percentage points"},
            "rules": {"min_gt_pixels": 1000, "help": "max delta_f1 of 2a_select",
                      "hurt": "min delta_f1 of 2a", "fill": "max delta_f1 of 2b",
                      "ties": "filename ascending", "duplicates": "allowed and recorded",
                      "strict": "WHU: test_255 if present; else max TP(2a)-TP(2a_strict), filename ascending; no area filter",
                      "no_prompts": "same as compose: [TP=0, FP=pred.sum(), FN=0] for every setting"},
            "image_count": len(rows), "eligible_count": len(eligible),
            "totals": comparisons, "selections": selections, "duplicates": duplicates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(DETECTORS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    prompts = read_prompts(args.dataset)
    items = [(name, prompts.get(name, [])) for name in sorted(set(names(args.dataset)) | set(prompts))]
    run = partial(per_image, dataset=args.dataset)
    if args.workers == 1:
        rows = list(map(run, items))
    else:
        with ProcessPoolExecutor(args.workers) as pool:
            rows = list(pool.map(run, items, chunksize=4))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "per_image.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    comparisons = verify_totals(args.dataset, rows)
    write_json(args.out / "selection.json", select_images(args.dataset, rows, comparisons))


if __name__ == "__main__":
    main()
