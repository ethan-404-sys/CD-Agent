"""6i descriptive statistics and figures, CPU only, no fitting or external calls.

Estimated 2–5 GB RAM and 10–25 min on one CPU (unmeasured). One dataset at a time;
only five grid examples and ten negative piece rows are retained for rendering.
Unknown offsets remain a separate bucket. Primary grid statistics use measured
valid blocks; filled-grid statistics are reported separately as sensitivity data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw

from common import load_rgb
from exp6b_common import ROOT, SOURCES, Pool, binary_metrics, rows_by_image, verifier_split, write_json
from exp6i_common import (AREA_LABELS, AUDIT, OFFSET_LABELS, OUT, RELATIVE_LABELS,
                         distribution, finite, fraction, iter_grids, read_config)
from exp6v2_data import Renderer, SIDE


def histogram(path, series, xlabel):
    fig, ax = plt.subplots(figsize=(7, 4))
    finite_series = [(label, np.asarray(values)[np.isfinite(values)]) for label, values in series]
    combined = [values for _, values in finite_series if len(values)]
    if combined:
        edges = np.histogram_bin_edges(np.concatenate(combined), bins=50)
        for label, values in finite_series:
            if len(values):
                ax.hist(values, bins=edges, density=True, histtype="step", label=f"{label} (n={len(values)})")
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No estimable observations", ha="center", transform=ax.transAxes)
    ax.set(xlabel=xlabel, ylabel="Density")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def grid_statistics(args, dataset, config, top_grids):
    global_values, direct, residuals, filled = [], [], [], []
    interpolation_distances = []
    counts = dict(images=0, unestimable_images=0, grids=0, valid_grids=0,
                  interpolated_grids=0, excluded_change=0, excluded_texture=0, excluded_peak=0)
    global_energy = 0.0
    for name, grid in iter_grids(args.out, dataset, config):
        counts["images"] += 1
        counts["unestimable_images"] += int(grid["unestimable"])
        counts["grids"] += len(grid["dx"])
        counts["valid_grids"] += int(grid["valid"].sum())
        counts["interpolated_grids"] += int(grid["interpolated"].sum())
        interpolation_distances.extend(grid["interpolation_distance"][grid["interpolated"]].tolist())
        counts["excluded_change"] += int((grid["change_fraction"] >= 0.1).sum())
        counts["excluded_texture"] += int((grid["texture"] <= config["texture_threshold"]).sum())
        counts["excluded_peak"] += int((grid["peak_ratio"] <= config["peak_ratio_threshold"]).sum())
        magnitude = np.hypot(grid["dx"], grid["dy"])
        residual = np.hypot(grid["residual_dx"], grid["residual_dy"])
        g = float(np.hypot(grid["global_dx"], grid["global_dy"]))
        global_values.append(g)
        valid = grid["valid"] & np.isfinite(magnitude)
        direct.extend(magnitude[valid].tolist())
        residuals.extend(residual[valid].tolist())
        filled.extend(magnitude.tolist())
        if valid.any():
            global_energy += g * g * int(valid.sum())
            best = np.flatnonzero(valid)[np.argsort(magnitude[valid])[-5:]]
            for index in best:
                top_grids.append(dict(dataset=dataset, image=name, grid_index=int(index),
                                      bbox=grid["bbox"][index].tolist(),
                                      dx=float(grid["dx"][index]), dy=float(grid["dy"][index]),
                                      magnitude=float(magnitude[index]), error=finite(grid["error"][index]),
                                      peak_ratio=float(grid["peak_ratio"][index]),
                                      texture=float(grid["texture"][index]),
                                      change_fraction=float(grid["change_fraction"][index])))
            top_grids.sort(key=lambda row: (-row["magnitude"], row["dataset"], row["image"], row["grid_index"]))
            del top_grids[5:]
    global_values, direct, residuals, filled = map(np.asarray, (global_values, direct, residuals, filled))
    residual_energy = float(np.sum(residuals ** 2))
    raw_energy = float(np.sum(direct ** 2))
    ratio = fraction(residual_energy, global_energy)
    # This is descriptive, not an additive variance decomposition or a significance test.
    dominance = ("insufficient" if not len(direct) else "local" if residual_energy > global_energy
                 else "global" if global_energy else "negligible")
    result = dict(**counts, global_offset=distribution(global_values),
                  measured_offset=distribution(direct), residual_local=distribution(residuals),
                  filled_offset=distribution(filled),
                  interpolation_distance=distribution(interpolation_distances),
                  global_gt2_images=int(np.count_nonzero(global_values > 2)),
                  global_gt2_fraction=fraction(np.count_nonzero(global_values > 2), np.isfinite(global_values).sum()),
                  residual_gt3_blocks=int(np.count_nonzero(residuals > 3)),
                  residual_gt3_fraction=fraction(np.count_nonzero(residuals > 3), len(residuals)),
                  valid_grid_fraction=fraction(counts["valid_grids"], counts["grids"]),
                  residual_to_global_squared_magnitude_ratio=ratio,
                  global_translation_squared_error_reduction=(1 - residual_energy / raw_energy) if raw_energy else None,
                  descriptive_dominance=dominance,
                  interpretation="Compare squared residual and global displacement on measured valid blocks; "
                                 "overlapping grids are dependent; this is not evidence of causal parallax.")
    histogram(args.figures / f"{dataset}_grid_offsets.png",
              [("global per image", global_values), ("residual measured blocks", residuals)], "Displacement (pixels)")
    return result


def cross_tables(data, buckets):
    """Counts plus within-stratum, within-offset-bin and overall proportions."""
    dimensions = dict(label=(data["label"] + 1, ("unlabeled", "negative", "positive")),
                      source=(data["source"], SOURCES),
                      split=(data["split"].astype(int), ("unsplit", "SLIC")),
                      area=(data["area_bucket"], AREA_LABELS))
    result = {}
    bin_total = np.bincount(buckets, minlength=5)
    for key, (values, labels) in dimensions.items():
        count = np.bincount(values * 5 + buckets, minlength=len(labels) * 5).reshape(len(labels), 5)
        result[key] = [dict(group=label, counts=row.tolist(),
                            fraction_within_group=[fraction(v, row.sum()) for v in row],
                            fraction_within_offset=[fraction(v, d) for v, d in zip(row, bin_total)])
                       for label, row in zip(labels, count)]
    labels = [item[1] for item in dimensions.values()]
    shape = tuple(len(value) for value in labels) + (5,)
    indices = np.ravel_multi_index(tuple(item[0] for item in dimensions.values()) + (buckets,), shape)
    count = np.bincount(indices, minlength=int(np.prod(shape))).reshape(shape)
    joint = []
    for idx in np.argwhere(count > 0):
        key = tuple(map(int, idx))
        n = int(count[key])
        joint.append(dict(label=labels[0][key[0]], source=labels[1][key[1]], split=labels[2][key[2]],
                          area=labels[3][key[3]], bucket=key[4], n=n,
                          fraction_all=fraction(n, len(buckets)),
                          fraction_within_offset=fraction(n, bin_total[key[4]]),
                          fraction_within_group=fraction(n, count[key[:-1]].sum())))
    result["joint"] = joint
    result["all"] = dict(counts=bin_total.tolist(), fractions=[fraction(v, len(buckets)) for v in bin_total])
    return result


def impact(data, selected):
    known = selected & ~data["unestimable"]
    absolute = known & (data["magnitude"] > 3)
    relative = known & (data["relative"] > 0.3)
    return dict(total=int(selected.sum()), estimated=int(known.sum()),
                unknown=int((selected & ~known).sum()),
                absolute_gt3=int(absolute.sum()), relative_gt03=int(relative.sum()),
                either=int((absolute | relative).sum()),
                absolute_fraction_estimated=fraction(absolute.sum(), known.sum()),
                relative_fraction_estimated=fraction(relative.sum(), known.sum()),
                either_fraction_estimated=fraction((absolute | relative).sum(), known.sum()),
                either_fraction_all_lower_bound=fraction((absolute | relative).sum(), selected.sum()),
                interpolation_fraction=distribution(data["interpolation_fraction"][known]))


def metrics(labels, scores, selected, exact_threshold):
    y, s = labels[selected], scores[selected]
    base = binary_metrics(y, s)
    result = {key: base[key] for key in ("n", "positive", "negative", "auc", "ap")}
    result["thresholds"] = {}
    for name, threshold in (("task_0.8197", 0.8197), ("frozen_exact", exact_threshold)):
        negative, positive = y == 0, y == 1
        fp = int(np.count_nonzero(negative & (s >= threshold)))
        tp = int(np.count_nonzero(positive & (s >= threshold)))
        result["thresholds"][name] = dict(threshold=threshold, false_positives=fp,
                                           fpr=fraction(fp, negative.sum()), recall=fraction(tp, positive.sum()))
    return result


def metric_plot(path, reports, relative=False):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    x = np.arange(5)
    for cohort, report in reports.items():
        for ax, key in zip(axes, ("auc", "ap", "fpr")):
            values = [row["thresholds"]["task_0.8197"]["fpr"] if key == "fpr" else row[key]
                      for row in report["relative" if relative else "absolute"]]
            ax.plot(x, [np.nan if v is None else v for v in values], marker="o", label=cohort)
            ax.set(xticks=x, xticklabels=RELATIVE_LABELS if relative else OFFSET_LABELS,
                   ylabel=key.upper(), ylim=(0, 1))
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def piece_statistics(args, dataset, config, frozen, validation, top_pieces):
    pool = Pool(dataset)
    with np.load(args.out / dataset / "pieces_offset.npz", allow_pickle=False) as z:
        if (str(z["dataset"]) != dataset or json.loads(str(z["config"])) != config
                or str(z["primary_method"]) != "direct"
                or z["image_names"].tolist() != pool.images
                or not np.array_equal(z["image_id"], pool.image_id)
                or not np.array_equal(z["piece_id"], pool.piece_id)
                or not np.array_equal(z["label"], pool.label)
                or not np.array_equal(z["source"], pool.source)
                or not np.array_equal(z["split"], pool.meta[:, 11].astype(bool))):
            raise ValueError("Piece offsets and current Pool/config differ")
        fields = ("label", "source", "split", "area", "area_bucket", "dx", "dy", "magnitude", "relative",
                  "interpolation_fraction", "unestimable", "offset_bucket", "relative_bucket")
        data = {key: z[key] for key in fields}
        grid_fields = ("dx", "dy", "magnitude", "relative", "interpolation_fraction",
                       "interpolation_distance", "unestimable", "offset_bucket", "relative_bucket")
        grid_data = {**data, **{key: z[f"grid_{key}"] for key in grid_fields}}
    with np.load(args.verifier / dataset / "scores.npz", allow_pickle=False) as z:
        if str(z["run_id"]) != frozen["run_id"] or str(z["dataset"]) != dataset:
            raise ValueError("Scores and frozen thresholds belong to different runs/datasets")
        idx = pool.feature_indices(z["image"], z["piece_id"])
        scores = np.full(len(pool), np.nan, np.float32)
        scores[idx] = z["score"]
    if not np.isfinite(scores).all():
        raise ValueError("Scores must cover all current Pool rows")
    recall = (pool.source != SOURCES.index("pred")) & (pool.label >= 0)
    selected = recall if dataset == "second" else recall & pool.image_mask(validation)
    negative = selected & (pool.label == 0)
    threshold = float(frozen["recall"]["B+G0.05+H1"]["threshold"])
    cohort = "test" if dataset == "second" else "second_cv_verifier_val"
    model = dict(cohort=cohort, scope="labeled recall pieces, all sources; pred excluded",
                 overall=metrics(pool.label, scores, selected, threshold),
                 absolute=[], relative=[], measured_only_absolute=[])
    for b in range(5):
        model["absolute"].append(metrics(pool.label, scores, selected & (data["offset_bucket"] == b), threshold))
        model["relative"].append(metrics(pool.label, scores, selected & (data["relative_bucket"] == b), threshold))
        measured = data["interpolation_fraction"] <= 1e-7
        model["measured_only_absolute"].append(metrics(
            pool.label, scores, selected & measured & (data["offset_bucket"] == b), threshold))
    model["small_le3"] = metrics(pool.label, scores, selected & (data["magnitude"] <= 3), threshold)
    model["large_gt3"] = metrics(pool.label, scores, selected & (data["magnitude"] > 3), threshold)
    model["large_minus_small"] = {
        key: (model["large_gt3"][key] - model["small_le3"][key])
        if model["large_gt3"][key] is not None and model["small_le3"][key] is not None else None
        for key in ("auc", "ap")}
    large_fpr = model["large_gt3"]["thresholds"]["task_0.8197"]["fpr"]
    small_fpr = model["small_le3"]["thresholds"]["task_0.8197"]["fpr"]
    model["large_minus_small"]["fpr"] = large_fpr - small_fpr if large_fpr is not None and small_fpr is not None else None
    all_negative = recall & (pool.label == 0)
    paired = ~data["unestimable"] & ~grid_data["unestimable"]
    grid_model = dict(
        absolute=[metrics(pool.label, scores, selected & (grid_data["offset_bucket"] == b), threshold)
                  for b in range(5)],
        relative=[metrics(pool.label, scores, selected & (grid_data["relative_bucket"] == b), threshold)
                  for b in range(5)],
        measured_only_absolute=[metrics(pool.label, scores, selected & (grid_data["offset_bucket"] == b)
                                       & (grid_data["interpolation_fraction"] <= 1e-7), threshold)
                                for b in range(5)])
    comparison = dict(
        direct_estimated=int((~data["unestimable"]).sum()),
        grid_estimated=int((~grid_data["unestimable"]).sum()), paired=int(paired.sum()),
        direct_only=int((~data["unestimable"] & grid_data["unestimable"]).sum()),
        grid_only=int((data["unestimable"] & ~grid_data["unestimable"]).sum()),
        vector_difference=distribution(np.hypot(data["dx"][paired] - grid_data["dx"][paired],
                                                data["dy"][paired] - grid_data["dy"][paired])),
        grid_magnitude=distribution(grid_data["magnitude"]),
        grid_relative=distribution(grid_data["relative"]),
        grid_interpolated=int(np.count_nonzero(grid_data["interpolation_fraction"] > 0)),
        grid_interpolation_distance=distribution(
            grid_data["interpolation_distance"][grid_data["interpolation_fraction"] > 0]),
        grid_absolute_tables=cross_tables(grid_data, grid_data["offset_bucket"]),
        grid_relative_tables=cross_tables(grid_data, grid_data["relative_bucket"]),
        grid_negative=impact(grid_data, all_negative), grid_v1=grid_model)
    result = dict(total=len(pool), primary_method="direct",
                  primary_reason=config["primary_piece_reason"], method_comparison=comparison,
                  magnitude=distribution(data["magnitude"]),
                  relative=distribution(data["relative"]),
                  absolute_tables=cross_tables(data, data["offset_bucket"]),
                  relative_tables=cross_tables(data, data["relative_bucket"]),
                  all_negative=impact(data, all_negative),
                  evaluation_negative=impact(data, negative),
                  high_score_negative=impact(data, negative & (scores >= 0.8197)),
                  high_score_negative_exact=impact(data, negative & (scores >= threshold)),
                  negative_by_source={source: impact(data, all_negative & (pool.source == i))
                                      for i, source in enumerate(SOURCES)},
                  negative_by_area={label: impact(data, all_negative & (data["area_bucket"] == i))
                                    for i, label in enumerate(AREA_LABELS)},
                  v1=model)
    result["negative_comparisons"] = {}
    for key, left, right in (
        ("C1_vs_other_sources", all_negative & (pool.source == SOURCES.index("C1")),
         all_negative & (pool.source != SOURCES.index("C1"))),
        ("area_lt50_vs_ge50", all_negative & (data["area"] < 50), all_negative & (data["area"] >= 50)),
        ("high_score_vs_all_evaluation_negatives", negative & (scores >= 0.8197), negative),
    ):
        a, b = impact(data, left), impact(data, right)
        rates = ("absolute_fraction_estimated", "relative_fraction_estimated", "either_fraction_estimated")
        result["negative_comparisons"][key] = dict(
            left=a, right=b, difference={field: a[field] - b[field] if a[field] is not None and b[field] is not None else None
                                       for field in rates})
    histogram(args.figures / f"{dataset}_piece_offsets.png",
              [("negative", data["magnitude"][all_negative]),
               ("positive", data["magnitude"][recall & (pool.label == 1)])], "Piece displacement (pixels)")
    histogram(args.figures / f"{dataset}_relative_offsets.png",
              [("negative", data["relative"][all_negative]),
               ("positive", data["relative"][recall & (pool.label == 1)])], "Displacement / bbox long side")
    indices = np.flatnonzero(all_negative & ~data["unestimable"])
    top = indices[np.argsort(-data["magnitude"][indices], kind="stable")[:10]]
    wanted = {}
    for index in top:
        name = pool.images[pool.image_id[index]]
        wanted[(name, int(pool.piece_id[index]))] = index
    for name, rows in rows_by_image(dataset):
        if name not in {key[0] for key in wanted}:
            continue
        for row in rows:
            key = (name, row["piece_id"])
            if key in wanted:
                index = wanted[key]
                top_pieces.append(dict(dataset=dataset, row=row, magnitude=float(data["magnitude"][index]),
                                       relative=float(data["relative"][index]), score=float(scores[index]),
                                       grid_magnitude=finite(grid_data["magnitude"][index]),
                                       grid_interpolation_distance=finite(grid_data["interpolation_distance"][index]),
                                       interpolation_fraction=float(data["interpolation_fraction"][index])))
    top_pieces.sort(key=lambda row: (-row["magnitude"], row["dataset"], row["row"]["image"], row["row"]["piece_id"]))
    del top_pieces[10:]
    return result


def audit_statistics(args, config):
    manifest = json.loads((args.out / "audit_samples_offset.json").read_text())
    if manifest["offset_config"] != config:
        raise ValueError("Merged audit configuration mismatch")
    rows = manifest["samples"]
    answers = json.loads(args.answers.read_text()) if args.answers.exists() else None
    if answers is not None:
        ids = {r["id"] for r in rows}
        if set(answers) - ids or any(v["choice"] not in ("changed", "unchanged", "unclear", "skip") for v in answers.values()):
            raise ValueError("Audit answers contain unknown IDs or choices")

    def group(selected):
        absolute = np.asarray([np.nan if r["offset"]["magnitude"] is None else r["offset"]["magnitude"] for r in selected])
        relative = np.asarray([np.nan if r["offset"]["relative"] is None else r["offset"]["relative"] for r in selected])
        bins = [dict(bucket=label, n=sum(r["offset"]["offset_bucket"] == label for r in selected)) for label in OFFSET_LABELS]
        result = dict(n=len(selected), absolute=distribution(absolute), relative=distribution(relative), buckets=bins,
                      absolute_gt3_fraction=fraction(np.count_nonzero(absolute > 3), np.isfinite(absolute).sum()),
                      relative_gt03_fraction=fraction(np.count_nonzero(relative > 0.3), np.isfinite(relative).sum()))
        if answers is not None:
            for entry in bins:
                members = [r for r in selected if r["offset"]["offset_bucket"] == entry["bucket"]]
                counts = {choice: 0 for choice in ("changed", "unchanged", "unclear", "skip", "missing")}
                disagree = 0
                for row in members:
                    choice = answers[row["id"]]["choice"] if row["id"] in answers else "missing"
                    counts[choice] += 1
                    if choice in ("changed", "unchanged"):
                        disagree += int((choice == "changed") != bool(row["label"]))
                definite = counts["changed"] + counts["unchanged"]
                entry.update(answer_counts=counts, definite=definite, disagreements=disagree,
                             disagreement_rate=fraction(disagree, definite))
        return result

    unique = [r for r in rows if not r["is_duplicate"]]
    result = dict(all_presentations=group(rows), original_samples=group(unique),
                  by_stratum={s: group([r for r in unique if r["stratum"] == s]) for s in sorted({r["stratum"] for r in rows})},
                  answers_status="analyzed" if answers is not None else "skipped: audit_answers.json is absent",
                  answers_path=str(args.answers),
                  interpretation="Diagnostic stratified sample; unweighted proportions are not population estimates. "
                                 "Duplicate presentations retained in all_presentations; original_samples excludes repeats.")
    series = [(s, np.asarray([np.nan if r["offset"]["magnitude"] is None else r["offset"]["magnitude"]
                              for r in unique if r["stratum"] == s])) for s in result["by_stratum"]]
    histogram(args.figures / "audit_strata_offsets.png", series, "Audit piece displacement (pixels)")
    return result


def paired(path, images, caption):
    canvas = Image.new("RGB", (2 * SIDE, SIDE + 36), "white")
    for i, image in enumerate(images):
        canvas.paste(image.resize((SIDE, SIDE), Image.Resampling.BICUBIC), (i * SIDE, 36))
    ImageDraw.Draw(canvas).text((6, 10), caption, fill="black")
    canvas.save(path)


def render_examples(args, top_pieces, top_grids):
    renderer = Renderer()
    captions = []
    for rank, item in enumerate(top_pieces, 1):
        row, dataset = item["row"], item["dataset"]
        filename = f"negative_{rank:02d}_{dataset}_{Path(row['image']).stem}_{row['piece_id']}.png"
        paired(args.figures / filename, renderer.render(dataset, row),
               f"t1 (left), t2 (right); d={item['magnitude']:.2f}px; relative={item['relative']:.3f}")
        caption = (f"{dataset}/{row['image']} 切块 {row['piece_id']}：真值负例，来源 "
                   f"{row['subsource'] or row['source']}，面积 {row['area']} 像素，偏移 {item['magnitude']:.2f} 像素，"
                   f"直接估计的相对偏移 {item['relative']:.3f}，网格偏移 {fmt(item['grid_magnitude'])} 像素，"
                   f"网格插值距离 {fmt(item['grid_interpolation_distance'])} 像素，"
                   f"V1 分数 {item['score']:.4f}。")
        captions.append(dict(file=filename, caption=caption, **{k: v for k, v in item.items() if k != "row"},
                             image=row["image"], piece_id=row["piece_id"]))
    check = args.figures / "check"
    check.mkdir(parents=True, exist_ok=True)
    for rank, item in enumerate(top_grids, 1):
        x0, y0, x1, y1 = item["bbox"]
        images = [Image.fromarray(load_rgb(item["dataset"], item["image"], view)[y0:y1, x0:x1]) for view in ("t1", "t2")]
        filename = f"grid_{rank:02d}_{item['dataset']}_{Path(item['image']).stem}_{item['grid_index']}.png"
        paired(check / filename, images, f"t1 (left), t2 (right); dx={item['dx']:.2f}, dy={item['dy']:.2f}px")
        item["file"] = f"check/{filename}"
    return dict(negative_pieces=captions, largest_measured_grids=top_grids,
                manual_check="pending: inspect the five grids for true change, repeated texture, and misregistration")


def fmt(value, percent=False):
    if value is None:
        return "无法估计"
    return f"{100 * value:.2f}%" if percent else f"{value:.4f}"


def write_report(args, report):
    lines = ["# 实验 6i：两期配准偏移", "",
             "偏移方向为 t2 相对 t1，dx 向右、dy 向下。网格主要统计只使用有效实测块。",
             "切块、V1 和审核的主要统计统一采用通过可靠性筛选的中心直接估计；未通过的记为未知。",
             "小切块及边缘切块容易受远处网格插值影响，中心直接估计提供更近的图像支持；该规则在参考检查前固定。",
             "网格切块估计作为敏感性对照：先按掩膜交集面积加权有效窗口，无交集才使用最近有效窗口，并记录距离。",
             "两期差分校准衡量人为平移的恢复一致性，不能独立证明原始绝对偏移正确；仍需参考与人工检查。",
             "无法估计项单列；比例默认以可估计项为分母。所有相关性均为描述性结果，未做显著性检验。", ""]
    for dataset, data in report["datasets"].items():
        g, p = data["grids"], data["pieces"]
        m, n, h = p["v1"], p["evaluation_negative"], p["high_score_negative"]
        comparison = p["method_comparison"]
        lines += [f"## {dataset}", "",
                  f"切块共 {p['total']} 个，直接可估计 {comparison['direct_estimated']} 个，"
                  f"网格可估计 {comparison['grid_estimated']} 个，两者均可估计 {comparison['paired']} 个。",
                  f"两者均有效时向量差中位数 {fmt(comparison['vector_difference']['quantiles']['50'])} 像素；"
                  f"网格切块使用最近块插值 {comparison['grid_interpolated']} 个，"
                  f"插值距离中位数 {fmt(comparison['grid_interpolation_distance']['quantiles']['50'])} 像素。",
                  "网格切块的分桶、V1 指标和纯实测支持结果见 stats.json 的 method_comparison。",
                  f"影像 {g['images']} 张，无法估计 {g['unestimable_images']} 张，有效块占 {fmt(g['valid_grid_fraction'], True)}。",
                  f"整图偏移中位数 {fmt(g['global_offset']['quantiles']['50'])} 像素；"
                  f"整图偏移 >2 像素的可估计影像占 {fmt(g['global_gt2_fraction'], True)}。",
                  f"残余局部偏移中位数 {fmt(g['residual_local']['quantiles']['50'])} 像素；"
                  f">3 像素的有效实测块占 {fmt(g['residual_gt3_fraction'], True)}。",
                  f"残余/整图平方位移比 {fmt(g['residual_to_global_squared_magnitude_ratio'])}，"
                  f"按两者大小作描述性判断：{g['descriptive_dominance']}；"
                  f"减去整图中位数后的平方位移下降 {fmt(g['global_translation_squared_error_reduction'], True)}。", "",
                  f"{m['cohort']} 负例中，偏移 >3 像素占 {fmt(n['absolute_fraction_estimated'], True)}，"
                  f"相对偏移 >0.3 占 {fmt(n['relative_fraction_estimated'], True)}，"
                  f"任一条件满足占 {fmt(n['either_fraction_estimated'], True)}；未知 {n['unknown']}/{n['total']}。",
                  f"高分负例（≥0.8197）中偏移 >3 像素占 {fmt(h['absolute_fraction_estimated'], True)}，"
                  f"任一明显偏移条件满足占 {fmt(h['either_fraction_estimated'], True)}；未知 {h['unknown']}/{h['total']}。",
                  f"大偏移（>3）减小偏移（≤3）的 V1 AUC 差 {fmt(m['large_minus_small']['auc'])}，"
                  f"AP 差 {fmt(m['large_minus_small']['ap'])}，负例误检率差 {fmt(m['large_minus_small']['fpr'], True)}。", "",
                  "| 负例来源/面积 | 已估计数 | 明显偏移比例（绝对或相对） |", "|---|---:|---:|"]
        for key in ("negative_by_source", "negative_by_area"):
            for label, entry in p[key].items():
                lines.append(f"| {label} | {entry['estimated']} | {fmt(entry['either_fraction_estimated'], True)} |")
        for key, entry in p["negative_comparisons"].items():
            lines += ["", f"{key}：明显偏移比例差 {fmt(entry['difference']['either_fraction_estimated'], True)}。"]
        lines += ["", "完整的标签、来源、SLIC 切分、面积联合交叉表以及两套偏移桶的指标见 stats.json。", ""]
    comparison = report["dataset_comparison"]
    lines += ["## test 与 second_cv", "",
              f"second_cv 减 test：整图中位数差 {fmt(comparison['global_median_difference_cv_minus_test'])} 像素，"
              f"残余局部中位数差 {fmt(comparison['residual_median_difference_cv_minus_test'])} 像素。",
              "second_cv 的网格统计覆盖全部图；V1 指标仅使用冻结验证图，未混入训练图。", "",
              "## 审核与真实校验", "", f"人工答案：{report['audit']['answers_status']}。",
              "审核分层分布及每桶人工与真值不一致率见 stats.json；看不清、跳过、缺答分别计数，不进入不一致率分母。",
              f"test_857.png 切块 18：直接估计 {fmt(report['reference']['direct']['magnitude'])} 像素"
              f"（dx={fmt(report['reference']['direct']['dx'])}，dy={fmt(report['reference']['direct']['dy'])}），"
              f"网格估计 {fmt(report['reference']['grid']['magnitude'])} 像素"
              f"（dx={fmt(report['reference']['grid']['dx'])}，dy={fmt(report['reference']['grid']['dy'])}）。",
              f"8–12 像素判据：直接 {report['reference']['passed']}，网格 {report['reference']['grid_passed']}；"
              f"允许继续：{report['reference']['proceed']}。{report['reference']['note']}",
              "五个最大有效网格已保存到 figures/exp6i/check/，人工核验状态 pending。", "",
              "## 后续建议与工作量", ""]
    recommendation = report["recommendation"]
    lines += [f"当前优先建议：{recommendation['action']}。{recommendation['reason']}", "",
              "该建议使用本次 test 的描述性数字，仍需检查五个网格裁图；是否改变正式流水线由后续对照实验决定。", ""]
    # A research decision needs inspection; these are conditional options, not fitted decision rules.
    lines += ["先核对上述最大网格裁图和插值覆盖率，再根据本次实测数字选择后续实验：", "",
              "| 方案 | 适用证据与建议 | 预计工作量（人工估计，未测量） |", "|---|---|---|",
              "| 保持当前流程 | 若可估计覆盖充分、明显偏移少且大偏移桶指标没有实质下降，可保留当前流程并记录限制。 | 半天整理证据 |",
              "| 增加整图配准 | 若整图位移贡献较大且扣除整图偏移后残余明显减少，先做小规模下游对照。 | 1–2 天实现与对照，计算另计 |",
              "| V1/V2 容忍局部偏移 | 若残余局部位移仍大且大偏移桶性能较差，优先验证邻域特征匹配或扩大 V2 近景框。 | 2–4 天实现，训练/推理另计 |",
              "| 单列小切块与大相对偏移 | 可先增加敏感性分析；正式候选和评估范围的改变须单独研究，保留完整基线。 | 半天至 1 天 |", "",
              "上述差异可能同时受来源、面积、真变化和可估计覆盖影响，不能仅凭分桶差异认定配准是原因。", "",
              "## 最大偏移负例", ""]
    for item in report["examples"]["negative_pieces"]:
        relative_path = os.path.relpath(args.figures / item["file"], args.out)
        lines += [item["caption"], "", f"![负例]({relative_path})", ""]
    (args.out / "exp6i_misregistration.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--figures", type=Path)
    parser.add_argument("--verifier", type=Path, default=ROOT / "verifier/none")
    parser.add_argument("--answers", type=Path, default=AUDIT / "audit_answers.json")
    args = parser.parse_args()
    args.figures = args.figures or args.out / "figures/exp6i"
    args.figures.mkdir(parents=True, exist_ok=True)
    config = read_config(args.config or args.out / "config.json")
    frozen = json.loads((args.verifier / "thresholds_second_cv.json").read_text())
    _, validation = verifier_split()
    if frozen["validation_images"] != validation:
        raise ValueError("Frozen threshold validation images differ from current verifier split")
    reference = json.loads((args.out / "test_857_piece18_offset.json").read_text())
    if not reference["proceed"] or reference["config"] != config:
        raise ValueError("Real reference check failed")
    report = dict(config=config, frozen_run_id=frozen["run_id"],
                  threshold_task=0.8197, threshold_exact=frozen["recall"]["B+G0.05+H1"]["threshold"],
                  absolute_bucket_labels=OFFSET_LABELS, relative_bucket_labels=RELATIVE_LABELS,
                  area_bucket_labels=AREA_LABELS, datasets={}, reference=reference)
    top_pieces, top_grids = [], []
    for dataset in ("second", "second_cv"):
        report["datasets"][dataset] = dict(
            grids=grid_statistics(args, dataset, config, top_grids),
            pieces=piece_statistics(args, dataset, config, frozen, validation, top_pieces))
        print("statistics complete", dataset, flush=True)
    report["dataset_comparison"] = {}
    for label, field in (("global", "global_offset"), ("residual", "residual_local")):
        a, b = [report["datasets"][ds]["grids"][field]["quantiles"]["50"] for ds in ("second", "second_cv")]
        report["dataset_comparison"][f"{label}_median_difference_cv_minus_test"] = b - a if a is not None and b is not None else None
    report["audit"] = audit_statistics(args, config)
    report["examples"] = render_examples(args, top_pieces, top_grids)
    test = report["datasets"]["second"]
    grids, pieces = test["grids"], test["pieces"]
    difference = pieces["v1"]["large_minus_small"]["auc"]
    if grids["descriptive_dominance"] == "insufficient" or difference is None:
        action = "先补充有效偏移和双类别指标证据"
    elif difference >= 0:
        action = "先保留当前流程，检查面积和来源分组中的敏感性"
    elif grids["descriptive_dominance"] == "local":
        action = "优先做 V1 邻域特征匹配或扩大 V2 近景框的小规模对照"
    else:
        action = "优先做整图配准的小规模对照，同时单列高残余局部偏移样本"
    report["recommendation"] = dict(
        action=action,
        reason=f"test 中残余/整图平方位移比为 {fmt(grids['residual_to_global_squared_magnitude_ratio'])}，"
               f"大偏移组相对小偏移组 AUC 差为 {fmt(difference)}；"
               f"可估计负例中满足任一明显偏移条件的比例为 {fmt(pieces['evaluation_negative']['either_fraction_estimated'], True)}。",
        status="provisional; manual grid inspection pending; no significance or causal claim")
    models = {data["pieces"]["v1"]["cohort"]: data["pieces"]["v1"] for data in report["datasets"].values()}
    metric_plot(args.figures / "v1_offset_metrics.png", models)
    metric_plot(args.figures / "v1_relative_metrics.png", models, relative=True)
    write_json(args.out / "stats.json", report)
    write_report(args, report)
    print(args.out / "stats.json", flush=True)


if __name__ == "__main__":
    main()
