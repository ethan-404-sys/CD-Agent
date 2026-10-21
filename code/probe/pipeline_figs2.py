"""CPU figures and metrics for three SECOND demonstrations (exp5).

Saved piece masks and batch14 reconstruction are authoritative. Extra full-test
removal pass computes the two decline fractions absent from batch14's 16 rows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import colormaps, pyplot as plt
import numpy as np
from PIL import Image
from scipy import ndimage

import eval_metrics_run as em
import exp4_candidates as e4
from common import OUT, load_prob, load_rgb
from exp6a_pieces import candidates
from exp6_pool_io import load_piece_masks
from exp6_pipeline_figs import errors, outline
from exp6b_common import source_of, write_json
from exp6b_eval import image_maps
from metrics_cd import pixel_counts, pixel_f1, six_numbers
from pipeline_select import DEFAULT_OUT, V1, completed_metrics, read_json, selected_rows

COLOURS = {"H1": (226, 130, 37), "B": (159, 83, 206), "G0.05": (0, 184, 214), "C": (235, 72, 135)}
LABEL_COLOURS = {1: (0, 210, 80), 0: (230, 40, 40), None: (155, 155, 155)}
ELIGIBLE = ("B", "G0.05", "H1")


def save(path, array):
    Image.fromarray(np.asarray(array, np.uint8)).save(path)


def edge(mask):
    return mask & ~ndimage.binary_erosion(mask, np.ones((3, 3), bool), border_value=0)


def blank(shape):
    return np.zeros((*shape, 4), np.uint8)


def composite(base, *layers):
    image = Image.fromarray(np.asarray(base, np.uint8)).convert("RGBA")
    for layer in layers:
        image = Image.alpha_composite(image, Image.fromarray(layer))
    return np.array(image.convert("RGB"))


def local_layer(layer, masks, piece_id, colour, alpha=135, boundary=False):
    (x0, y0, x1, y1), mask = masks[piece_id]
    selected = edge(mask) if boundary else mask
    layer[y0:y1, x0:x1][selected] = (*colour, alpha)


def score_layer(rows, masks, scores, shape, threshold, *, deletion=False, eligible=None):
    values = np.full(shape, np.nan)
    accepted = blank(shape)
    for row, value in zip(rows, scores):
        (x0, y0, x1, y1), mask = masks[row["piece_id"]]
        view = values[y0:y1, x0:x1]
        # Recall overlaps use max, as in exp6b_eval.image_maps.
        view[mask] = np.fmax(view[mask], value)
        selected = value < threshold if deletion else value >= threshold
        if selected and (eligible is None or source_of(row) in eligible):
            local_layer(accepted, masks, row["piece_id"], (255, 255, 255), 255, boundary=True)
    layer = blank(shape)
    valid = np.isfinite(values)
    layer[valid, :3] = (colormaps["viridis"](values[valid])[:, :3] * 255).astype(np.uint8)
    layer[valid, 3] = 180
    return layer, accepted


def colourbar(path, threshold):
    fig, ax = plt.subplots(figsize=(5.1, 0.8))
    ax.imshow(np.linspace(0, 1, 512)[None, :], aspect="auto", cmap="viridis", extent=[0, 1, 0, 1])
    ax.axvline(threshold, color="white", linewidth=2)
    ax.set(yticks=[], xlabel=f"score (0–1); threshold = {threshold:.9g}")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def removal_summary(pool, scores, calibration):
    totals = {key: dict(tp=0, fp=0, fn=0) for key in ("verifier", "detector_mean")}
    declines = Counter()
    for _, gt, base, _, remove in image_maps(pool, scores, pool.images):
        before = pixel_f1(pixel_counts(base, gt))
        for key in totals:
            after = base & ~(remove[key] < calibration["remove"][key]["threshold"])
            counts = pixel_counts(after, gt)
            declines[key] += pixel_f1(counts) < before
            for item in totals[key]:
                totals[key][item] += counts[item]
    report = read_json(V1 / "eval_second.json")
    result = {}
    for key in totals:
        em.check_counts("remove/" + key, totals[key], report["pixels"]["remove/" + key])
        result[key] = dict(counts=totals[key], decline_fraction=declines[key] / len(pool.images),
                           images=len(pool.images), threshold=calibration["remove"][key]["threshold"])
    return dict(rows=result, source="exp6/verifier/none/{second/scores.npz,thresholds_second_cv.json}",
                check="full test pixel counts match eval_second.json pixels.remove/*",
                note="batch14 has no rows for these two actual removal branches; separate full-test pass")


def zoom_box(mask, smoothed, side=128):
    """Find window with most boundary pixels changed by the prescribed smoothing."""
    changed = edge(mask) ^ edge(smoothed)
    best, box = -1, None
    h, w = mask.shape
    side = min(side, h, w)
    for y in sorted(set(range(0, h - side + 1, 16)) | {h - side}):
        for x in sorted(set(range(0, w - side + 1, 16)) | {w - side}):
            count = int(changed[y:y + side, x:x + side].sum())
            if count > best:
                best, box = count, [x, y, x + side, y + side]
    return box, best


def render(example, rows, summary, v2, v2_meta, removal, calibration, out, expected_candidates):
    name = example["image"]
    folder = out / Path(name).stem
    folder.mkdir(parents=True, exist_ok=True)
    gt, base, maps, different = em.reconstruct(name)
    pool, scores = em.STATE["pool"], em.STATE["scores"]
    t1, t2 = [load_rgb("second", name, view) for view in ("t1", "t2")]
    prob = load_prob("second", "mambascd_base", name)
    masks = load_piece_masks("second", name)
    sl = pool.slices[name]
    if [r["piece_id"] for r in rows] != pool.piece_id[sl].tolist():
        raise ValueError(f"{name}: piece order differs")
    recall = [r for r in rows if r["kind"] == "recall"]
    pred = [r for r in rows if r["kind"] == "pred"]
    indices = {int(pool.piece_id[i]): i for i in range(sl.start, sl.stop)}
    v1_scores = [float(scores[indices[r["piece_id"]]]) for r in recall]
    v2_delta = [v2[(name, r["piece_id"])]["logit_diff"] for r in recall]
    v2_scores = [v2[(name, r["piece_id"])]["score"] for r in recall]
    threshold2 = v2_meta["models"]["a"]["threshold"]
    threshold2_prob = float(1 / (1 + np.exp(-threshold2)))
    for view, rgb in (("t1", t1), ("t2", t2)):
        save(folder / f"{view}.png", rgb)
    save(folder / "gt.png", gt.astype(np.uint8) * 255)
    save(folder / "base_mask.png", base.astype(np.uint8) * 255)
    save(folder / "prob.png", colormaps["inferno"](prob)[..., :3] * 255)
    gt_layer = blank(gt.shape)
    gt_layer[edge(gt)] = (255, 225, 0, 255)
    save(folder / "layer_gt.png", gt_layer)

    # Candidate filtering follows the pool builder: quantized probability > .5,
    # while the downstream detector mask is the saved PNG.
    raw = {s: [] for s in COLOURS}
    count_check = Counter()
    recall_pred = prob > 0.5
    for source, subsource, mask, _ in candidates("second", name, prob, recall_pred):
        if mask.sum() >= e4.MIN_CAND and recall_pred[mask].mean() <= e4.MAX_PRED_FRAC:
            raw[source].append(mask)
            count_check["recall/" + (subsource or source)] += 1
    expected = {k: v for k, v in expected_candidates.items() if k.startswith("recall/") and v}
    if dict(count_check) != expected:
        raise ValueError(f"{name}: rebuilt candidate counts differ from pool summary")
    source_stats, source_layers = {}, []
    union = np.zeros_like(gt)
    for source, colour in COLOURS.items():
        items = raw[source]
        layer = blank(gt.shape)
        src_union = np.zeros_like(gt)
        for mask in items:
            src_union |= mask
        layer[src_union] = (*colour, 85)
        for mask in items:
            layer[edge(mask)] = (*colour, 235)
        source_layers.append(layer)
        save(folder / f"layer_source_{source}.png", layer)
        save(folder / f"source_{source}.png", composite(t2, layer, gt_layer))
        area = sum(int(m.sum()) for m in items)
        true = sum(int((m & gt).sum()) for m in items)
        source_stats[source] = dict(candidates=len(items), area_sum=area,
                                   purity=true / area if area else None,
                                   union_area=int(src_union.sum()),
                                   union_purity=float(gt[src_union].mean()) if src_union.any() else None)
        union |= src_union
    # Redraw all source edges over the combined fills to retain overlapping sources.
    combined = composite(t2, *source_layers)
    for source, colour in COLOURS.items():
        for mask in raw[source]:
            combined[edge(mask)] = colour
    save(folder / "candidates_all.png", composite(combined, gt_layer))
    candidate_all = dict(count=sum(len(v) for v in raw.values()), union_area=int(union.sum()),
                         union_fraction=float(union.mean()),
                         union_purity=float(gt[union].mean()) if union.any() else None)

    label_layer, boundaries = blank(gt.shape), blank(gt.shape)
    for row in recall:
        local_layer(label_layer, masks, row["piece_id"], LABEL_COLOURS[row["label"]])
        local_layer(boundaries, masks, row["piece_id"], (255, 255, 255), 210, boundary=True)
    save(folder / "layer_labels.png", label_layer)
    save(folder / "layer_boundaries.png", boundaries)
    save(folder / "pieces_all.png", composite(t2, label_layer, boundaries, gt_layer))
    counts = Counter(r["label"] for r in recall)
    labeled = counts[0] + counts[1]
    piece_stats = dict(total=len(recall), positive=counts[1], negative=counts[0], unlabeled=counts[None],
                       positive_rate_labeled=counts[1] / labeled if labeled else None,
                       positive_rate_all=counts[1] / len(recall) if recall else None)
    for tag, values, threshold in (("v1", v1_scores, em.STATE["threshold"]),
                                   ("v2a", v2_scores, threshold2_prob)):
        layer, accepted = score_layer(recall, masks, values, gt.shape, threshold, eligible=ELIGIBLE)
        save(folder / f"layer_{tag}.png", layer)
        save(folder / f"layer_{tag}_accepted.png", accepted)
        save(folder / f"{tag}_heat.png", composite(t2, layer, accepted, gt_layer))
        colourbar(folder / f"{tag}_colourbar.png", threshold)
    v2_added = np.zeros_like(base)
    for row, delta in zip(recall, v2_delta):
        if source_of(row) in ELIGIBLE and delta >= threshold2:
            (x0, y0, x1, y1), mask = masks[row["piece_id"]]
            v2_added[y0:y1, x0:x1] |= mask
    maps["v2a"] = base | v2_added
    _, _, _, _, removal_maps = next(image_maps(pool, scores, [name]))
    removal_info = {}
    for key, title in (("detector_mean", "detector"), ("verifier", "v1")):
        threshold = calibration["remove"][key]["threshold"]
        values = [float(pool.meta[indices[r["piece_id"]], 0]) if key == "detector_mean" else
                  float(scores[indices[r["piece_id"]]]) for r in pred]
        layer, rejected = score_layer(pred, masks, values, gt.shape, threshold, deletion=True)
        save(folder / f"remove_{title}_heat.png", composite(t2, layer, rejected, gt_layer))
        colourbar(folder / f"remove_{title}_colourbar.png", threshold)
        maps["remove_" + key] = base & ~(removal_maps[key] < threshold)
        removal_info[key] = dict(threshold=threshold, comparison="score < threshold",
                                 pieces=len(pred), rejected=sum(v < threshold for v in values),
                                 removed_pixels=int((base & ~maps["remove_" + key]).sum()),
                                 threshold_source=f"exp6/verifier/none/thresholds_second_cv.json: remove.{key}")

    wanted = ("1", "5", "v2a", "2_BGH1", "3", "4_BGH1", "2a_BGH1", "2b_BGH1",
              "4a_BGH1", "4b_BGH1", "remove_detector_mean", "remove_verifier")
    numbers = {}
    before_f1 = pixel_f1(pixel_counts(base, gt))
    for key in wanted:
        if key in summary["rows"]:
            decline = summary["rows"][key]["delta_f1"]["decline_fraction"]
            source = f"eval_metrics/summary.json: rows.{key}.delta_f1.decline_fraction"
        elif key.startswith("remove_"):
            decline = removal["rows"][key.removeprefix("remove_")]["decline_fraction"]
            source = "pipeline_figs/removal_summary.json: " + key.removeprefix("remove_")
        else:
            decline, source = None, "未评估全测试集；三图不能推导全测试集下降占比"
        numbers[key] = six_numbers(maps[key], gt, base, dataset_decline_fraction=decline)
        numbers[key].update(delta_f1=numbers[key]["pixel_f1"] - before_f1,
                            decline_source=source, counts=pixel_counts(maps[key], gt))
        save(folder / f"result_{key}.png", errors(t2, maps[key], gt))
        save(folder / f"mask_{key}.png", maps[key].astype(np.uint8) * 255)
    if abs(numbers["5"]["delta_f1"] - example["delta_f1"]) > 1e-10:
        raise ValueError(f"{name}: row 5 does not reproduce selection ΔF1")
    box, changed = zoom_box(maps["4_BGH1"], maps["4b_BGH1"])
    x0, y0, x1, y1 = box
    locator = np.zeros_like(base)
    locator[y0:y1, x0:x1] = True
    save(folder / "edge_where.png", outline(t2, locator, (0, 220, 255)))
    for key in ("1", "2_BGH1", "2a_BGH1", "2b_BGH1", "4_BGH1", "4a_BGH1", "4b_BGH1"):
        layer = blank(gt.shape)
        layer[edge(maps[key])] = (255, 50, 80, 255)
        view = composite(t2, layer, gt_layer)[y0:y1, x0:x1]
        Image.fromarray(view).resize((512, 512), Image.Resampling.NEAREST).save(folder / f"edge_{key}.png")
    pieces = [dict(piece_id=r["piece_id"], kind=r["kind"], source=source_of(r), label=r["label"],
                   area=r["area"], gt_frac=r["gt_frac"], bbox=r["bbox"],
                   detector_mean=float(pool.meta[indices[r["piece_id"]], 0]),
                   v1_score=float(scores[indices[r["piece_id"]]]),
                   v2a=v2[(name, r["piece_id"])] if r["kind"] == "recall" else None) for r in rows]
    write_json(folder / "pieces.json", pieces)
    info = dict(**example, per_source=source_stats, candidates_all=candidate_all, pieces=piece_stats,
                numbers=numbers, removal=removal_info, saved_vs_prob_pixels=different,
                v1_threshold=em.STATE["threshold"], v2a_threshold_logit_diff=threshold2,
                v2a_threshold_probability=threshold2_prob,
                zoom=dict(box=box, changed_boundary_pixels=changed,
                          rule="128 像素窗口、步长 16，最大化 4_BGH1 与 4b_BGH1 边界异或像素数；同分先上后左"),
                candidate_count_check="matched pool per_image.candidates")
    write_json(folder / "example.json", info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_OUT / "selection.json")
    parser.add_argument("--v2", type=Path, default=DEFAULT_OUT / "v2")
    parser.add_argument("--doc-out", type=Path, default=DEFAULT_OUT / "doc")
    args = parser.parse_args()
    selection = read_json(args.selection)
    summary, selected_metrics = completed_metrics(selection["metrics_dir"])
    complete = read_json(args.v2 / "complete.json")
    if complete["selection"] != selection:
        raise ValueError("V2 selection differs from requested figures")
    rows = selected_rows(selection)
    v2 = {}
    with np.load(args.v2 / "a_scores.npz", allow_pickle=False) as saved:
        if str(saved["run_id"]) != complete["models"]["a"]["run_id"]:
            raise ValueError("V2 score run differs")
        for name, pid, value, delta in zip(saved["image"], saved["piece_id"], saved["score"], saved["logit_diff"]):
            key = (str(name), int(pid))
            if key in v2 or not np.isfinite([value, delta]).all():
                raise ValueError("Duplicate or nonfinite V2 score")
            v2[key] = dict(score=float(value), logit_diff=float(delta))
    expected = {(name, r["piece_id"]) for name, items in rows.items() for r in items if r["kind"] == "recall"}
    if set(v2) != expected:
        raise ValueError("V2 scores must cover exactly all selected recall pieces, including unlabeled")
    em.initialize()
    calibration = read_json(V1 / "thresholds_second_cv.json")
    report = read_json(V1 / "eval_second.json")
    if report["applied_thresholds"]["remove"] != {k: v["threshold"] for k, v in calibration["remove"].items()}:
        raise ValueError("Frozen removal thresholds differ from 6b report")
    out = args.doc_out / "results/pipeline_figs"
    out.mkdir(parents=True, exist_ok=True)
    completion = out / "figures_complete.json"
    if completion.exists():
        raise FileExistsError("Figures already completed; use a new --doc-out")
    removal = removal_summary(em.STATE["pool"], em.STATE["scores"], calibration)
    write_json(out / "removal_summary.json", removal)
    pool_summary = read_json(OUT / "exp6/pool/second/pieces_summary.json")
    candidates_by_image = {r["image"]: r["candidates"] for r in pool_summary["per_image"]}
    examples = []
    for example in selection["examples"]:
        if example["delta_f1"] != selected_metrics[example["image"]]["delta_f1"]:
            raise ValueError("Selected metrics changed")
        examples.append(render(example, rows[example["image"]], summary, v2, complete, removal,
                               calibration, out, candidates_by_image[example["image"]]))
        print(example["image"], "complete", flush=True)
    write_json(completion, dict(selection=selection, examples=examples, v2=complete,
                                edge_implementation="eval_metrics_run.reconstruct (batch14)"))


if __name__ == "__main__":
    main()
