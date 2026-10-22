"""Render existing experiment-2 box-prompt results on CPU."""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
import numpy as np
from PIL import Image

from common import OUT, load_rgb, write_json
from exp2_viz_select import DETECTORS, compose, read_prompts

COLOURS = {"hit": "#44bb88", "partial": "#ffc04d", "miss": "#b97bea"}
ERROR_COLOURS = {"TP": "#dddddd", "FP": "#ed4b50", "FN": "#408bff", "TN": "#20242c"}
PANEL_RULES = {
    "hit_gain": "hit, SAM IoU > detector IoU; largest gain",
    "hit_loss": "hit, SAM IoU < detector IoU; largest loss",
    "miss_filled": "miss, SAM IoU >= 0.5; largest GT area",
    "miss_failed": "miss, SAM IoU < 0.5; largest GT area",
}


def rgb_colour(value):
    return np.asarray(matplotlib.colors.to_rgb(value)) * 255


def errors(pred, gt):
    out = np.empty((*gt.shape, 3), dtype=np.uint8)
    out[:] = rgb_colour(ERROR_COLOURS["TN"])
    for mask, colour in ((pred & gt, "TP"), (pred & ~gt, "FP"), (~pred & gt, "FN")):
        out[mask] = rgb_colour(ERROR_COLOURS[colour])
    return out


def tint(rgb, mask, colour):
    out = rgb.astype(float).copy()
    out[mask] = 0.4 * out[mask] + 0.6 * rgb_colour(colour)
    return out.astype(np.uint8)


def show(ax, array, title):
    ax.imshow(array, cmap="gray", vmin=0, vmax=1 if array.dtype == bool else 255,
              interpolation="nearest")
    ax.set_title(title, fontsize=9)
    ax.axis("off")


def bbox(ax, box, colour="yellow", label=None):
    x0, y0, x1, y1 = box
    ax.add_patch(Rectangle((x0 - 0.5, y0 - 0.5), x1 - x0, y1 - y0,
                           fill=False, edgecolor=colour, linewidth=0.8))
    if label is not None:
        ax.text(x0, y0, str(label), color="white", fontsize=7,
                bbox={"facecolor": "black", "alpha": 0.65, "pad": 1, "edgecolor": "none"})


def legend(fig, colours):
    fig.legend(handles=[Patch(facecolor=c, label=k) for k, c in colours.items()],
               loc="lower center", ncol=len(colours), frameon=False, fontsize=8)


def save(fig, path):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    with Image.open(buffer) as image:
        rgb = image.convert("RGB")
    encoded = io.BytesIO()
    for quality in (84, 76, 68, 60):
        encoded.seek(0)
        encoded.truncate()
        rgb.save(encoded, format="JPEG", quality=quality, optimize=True, dpi=(110, 110))
        if encoded.tell() < 300_000:
            break
    while encoded.tell() >= 300_000:
        rgb = rgb.resize((int(rgb.width * 0.85), int(rgb.height * 0.85)), Image.Resampling.LANCZOS)
        encoded.seek(0)
        encoded.truncate()
        rgb.save(encoded, format="JPEG", quality=76, optimize=True, dpi=(110, 110))
    path.write_bytes(encoded.getvalue())


def choose_objects(objects):
    available = sorted((o for o in objects if o["prompted"]), key=lambda o: o["id"])
    pools = {
        "hit_gain": [o for o in available if o["class"] == "hit" and o["sam_iou"] > o["det_iou"]],
        "hit_loss": [o for o in available if o["class"] == "hit" and o["sam_iou"] < o["det_iou"]],
        "miss_filled": [o for o in available if o["class"] == "miss" and o["sam_iou"] >= 0.5],
        "miss_failed": [o for o in available if o["class"] == "miss" and o["sam_iou"] < 0.5],
    }
    selected, missing = {}, []
    for role, pool in pools.items():
        if not pool:
            missing.append(role)
            continue
        if role == "hit_gain":
            winner = max(pool, key=lambda o: o["sam_iou"] - o["det_iou"])
        elif role == "hit_loss":
            winner = max(pool, key=lambda o: o["det_iou"] - o["sam_iou"])
        else:
            winner = max(pool, key=lambda o: o["area"])
        selected[winner["id"]] = role
    # If a category is absent, show up to two real objects without inventing its example.
    for o in sorted(available, key=lambda o: (-o["area"], o["id"])):
        if len(selected) >= 2:
            break
        selected.setdefault(o["id"], "largest_remaining")
    return selected, missing


def check_objects(dataset, name, objects, stored):
    for o in objects:
        if not o["prompted"]:
            continue
        ref = stored[(name, o["id"])]
        det = ref["detector"][DETECTORS[dataset]]
        if (o["chosen_view"] != ref["chosen_view"] or o["class"] != det["class"]
                or not math.isclose(o["sam_iou"], ref["sam_iou"], rel_tol=0, abs_tol=1e-12)
                or not math.isclose(o["det_iou"], det["det_iou"], rel_tol=0, abs_tol=1e-12)):
            raise RuntimeError(f"objects.jsonl mismatch: {dataset}/{name} object {o['id']}")


def object_panel(o, result, rgb, path, role):
    g = o["id"]
    left, top, right, bottom = o["crop"]
    sl = np.s_[top:bottom, left:right]
    crop = rgb[o["chosen_view"]][sl]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.8))
    show(axes[0], crop, f"SAM3 input: {o['chosen_view']} (box)")
    x0, y0, x1, y1 = o["bbox"]
    bbox(axes[0], [x0 - left, y0 - top, x1 - left, y1 - top])
    px, py = o["point"]
    axes[0].scatter([px - left], [py - top], s=35, c="#36ff80", marker="+", linewidths=1.5)
    show(axes[1], tint(crop, result["sam"][g][sl], "#30d5e8"), f"SAM3 mask | IoU={o['sam_iou']:.4f}")
    show(axes[2], result["pred"][sl], f"Detector crop | object IoU={o['det_iou']:.4f}")
    show(axes[3], (result["gl"] == g)[sl], f"GT object {g} | area={o['area']}")
    treatment = " / ".join(f"{s}: {t}" for s, t in o["treatment"].items())
    fig.suptitle(f"Object {g}: {o['class']} | {role} | {treatment}", fontsize=10)
    fig.text(0.5, 0.025, "Yellow: GT box. Green +: GT point (reference only; NOT used in box variant).\n"
             "Detector IoU uses the full matched component; detector crop shows all original predicted pixels.",
             ha="center", fontsize=8)
    fig.subplots_adjust(left=0.015, right=0.985, top=0.80, bottom=0.17, wspace=0.08)
    save(fig, path)


def strict_panel(result, rgb, path):
    gt, gl, pl, masks = (result[k] for k in ("gt", "gl", "pl", "masks"))
    lost = gt & masks["2a"] & ~masks["2a_strict"]
    candidates = []
    components = sorted({o["best_pred"] for o in result["objects"] if o["treatment"]["2a"] == "replace"})
    for p in components:
        component = pl == p
        ids = [int(g) for g in np.unique(gl[component & gt])]
        if len(ids) >= 2:
            candidates.append({"component": p, "gt_ids": ids, "tp_lost": int((lost & component).sum())})
    chosen = max(candidates, key=lambda c: c["tp_lost"]) if candidates else None
    component = pl == chosen["component"] if chosen else np.zeros_like(gt)
    fig, axes = plt.subplots(1, 5, figsize=(14, 3.7))
    show(axes[0], tint(rgb["t2"], component, "#ffcc40"),
         f"Pred component {chosen['component']}" if chosen else "No shared hit component")
    gt_rgb = np.zeros((*gt.shape, 3), dtype=np.uint8)
    selected = [o for o in result["objects"] if chosen and o["id"] in chosen["gt_ids"]]
    for o in selected:
        gt_rgb[gl == o["id"]] = rgb_colour(COLOURS[o["class"]])
        bbox(axes[0], o["bbox"], label=o["id"])
    show(axes[1], gt_rgb, "GT objects under component")
    for o in selected:
        bbox(axes[1], o["bbox"], label=o["id"])
    for ax, setting in zip(axes[2:4], ("2a_strict", "2a")):
        show(ax, errors(masks[setting], gt), f"{setting} | F1={100 * result['settings'][setting]['f1']:.2f}%")
    show(axes[4], lost, f"TP lost by strict: {int(lost.sum()):,}")
    fig.suptitle("Strict deletion removes the whole shared predicted component", fontsize=11)
    legend(fig, ERROR_COLOURS)
    fig.subplots_adjust(left=0.015, right=0.985, top=0.80, bottom=0.14, wspace=0.09)
    save(fig, path)
    return {"tp_lost": int(lost.sum()), "tp_difference": result["settings"]["2a"]["tp"]
            - result["settings"]["2a_strict"]["tp"], "shared_component": chosen}


def render(dataset, entry, roles, prompts, stored, out):
    name = entry["image"]
    result = compose(name, dataset, prompts)
    for setting, score in result["settings"].items():
        if any(score[k] != entry["settings"][setting][k] for k in ("tp", "fp", "fn", "f1", "delta_f1")):
            raise RuntimeError(f"selection.json mismatch: {dataset}/{name}/{setting}")
    check_objects(dataset, name, result["objects"], stored)
    rgb = {v: load_rgb(dataset, name, v) for v in ("t1", "t2")}
    out.mkdir(parents=True, exist_ok=True)
    gt, pred = result["gt"], result["pred"]
    fig, axes = plt.subplots(1, 5, figsize=(14, 3.6))
    original_f1 = 100 * result["settings"]["original"]["f1"]
    for ax, array, title in zip(axes, (rgb["t1"], rgb["t2"], gt, pred, errors(pred, gt)),
                                ("t1", "t2", "GT change", "Detector (0.5)", f"Detector | F1={original_f1:.2f}%")):
        show(ax, array, title)
    fig.suptitle(f"{dataset} / {name} / {DETECTORS[dataset]}", fontsize=11)
    legend(fig, ERROR_COLOURS)
    fig.subplots_adjust(left=0.015, right=0.985, top=0.80, bottom=0.13, wspace=0.08)
    save(fig, out / "overview.jpg")

    fig, ax = plt.subplots(figsize=(7, 7))
    classified = rgb["t2"].copy()
    for cls, colour in COLOURS.items():
        ids = [o["id"] for o in result["objects"] if o["class"] == cls]
        classified = tint(classified, np.isin(result["gl"], ids), colour)
    show(ax, classified, "GT objects: hit >= 0.5; partial [0.1, 0.5); miss < 0.1")
    for o in result["objects"]:
        bbox(ax, o["bbox"], COLOURS[o["class"]], o["id"])
    legend(fig, COLOURS)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.07)
    save(fig, out / "objects.jpg")

    fig, axes = plt.subplots(1, 4, figsize=(12, 3.6))
    for ax, setting in zip(axes, ("2a", "2a_select", "2b", "both_select")):
        score = result["settings"][setting]
        show(ax, errors(result["masks"][setting], gt),
             f"{setting}\nF1={100 * score['f1']:.2f}% | dF1={100 * score['delta_f1']:+.2f} pp")
    legend(fig, ERROR_COLOURS)
    fig.subplots_adjust(left=0.015, right=0.985, top=0.80, bottom=0.13, wspace=0.09)
    save(fig, out / "compose.jpg")

    selected, missing = choose_objects(result["objects"])
    for o in result["objects"]:
        if o["id"] in selected:
            o["panel_role"] = selected[o["id"]]
            o["panel"] = f"obj_{o['id']}.jpg"
            object_panel(o, result, rgb, out / o["panel"], o["panel_role"])
    strict = strict_panel(result, rgb, out / "strict.jpg") if "strict" in roles else None
    meta = {"dataset": dataset, "image": name, "detector": DETECTORS[dataset], "variant": "box",
            "roles": roles, "gt_pixels": int(gt.sum()), "settings": result["settings"],
            "objects": result["objects"], "representative_rules": {**PANEL_RULES,
                "ties": "object id ascending", "largest_remaining": "if fewer than 2 representatives, add largest remaining prompted objects, up to 2"},
            "missing_panel_roles": missing, "strict": strict,
            "figures": {"overview": "overview.jpg", "objects": "objects.jpg", "compose": "compose.jpg",
                        **({"strict": "strict.jpg"} if strict is not None else {})},
            "notes": ["F1 and delta_f1 are fractions; figures use percent and percentage points.",
                      "Point is shown for reference only; SAM3 masks use box prompts only.",
                      "Detector IoU is best full-component IoU from objects.match, not crop IoU.",
                      "Treatments describe each object's rule, not interactions from other objects' unions/deletions.",
                      "Unprompted objects have no SAM IoU or view and no direct SAM edit."]}
    write_json(out / "meta.json", meta)
    print(f"{dataset}/{name}: roles={roles}, panels={selected}, absent={missing}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(DETECTORS), required=True)
    parser.add_argument("--sel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.sel.read_text())
    if (selection["dataset"] != args.dataset or selection["detector"] != DETECTORS[args.dataset]
            or selection["variant"] != "box"):
        raise ValueError("selection dataset/detector/variant does not match this run")
    entries, roles = {}, {}
    for role, entry in selection["selections"].items():
        name = entry["image"]
        entries[name] = entry
        roles.setdefault(name, []).append(role)
    prompts = read_prompts(args.dataset)
    stored = {}
    with (OUT / "exp2" / args.dataset / "objects.jsonl").open() as f:
        for line in f:
            row = json.loads(line)
            if row["image"] in entries and row["variant"] == "box":
                stored[(row["image"], row["id"])] = row
    base = args.out / args.dataset
    for name, entry in entries.items():
        render(args.dataset, entry, roles[name], prompts[name], stored, base / Path(name).stem)
    write_json(base / "selection.json", selection)


if __name__ == "__main__":
    main()
