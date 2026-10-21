"""6i r3: verify saved r2 displacements on RGB piece masks, CPU only.

No registration is run. All r2-estimable pieces are evaluated, one image at a
time. Input modules come from Chang-Agent/probe (set PYTHONPATH when staged).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import map_coordinates

from common import load_rgb
from exp6_pool_io import load_piece_masks
from exp6b_common import ROOT, SOURCES, Pool, save_npz, verifier_split, write_json
from exp6i_common import AREA_LABELS, distribution, finite, fraction
from exp6i_stats import impact, metrics


DATASETS = ("second", "second_cv")
BUCKETS = ("[0,1]", "(1,3]", "(3,8]", "(8,15]", "(15,30]", "(30,inf)")
MEASUREMENTS = ("before", "after", "random", "support_fraction")


def magnitude_bucket(values):
    return np.searchsorted([1, 3, 8, 15, 30], values, side="left").astype(np.int8)


def sample_rgb(image, y, x):
    """Bilinear RGB samples; callers exclude out-of-image coordinates."""
    return np.stack([map_coordinates(image[..., c], [y, x], order=1,
                                     mode="constant", cval=np.nan, prefilter=False)
                     for c in range(3)], axis=-1)


def support(y, x, shape, dx, dy, random_dx, random_dy):
    height, width = shape[:2]
    return ((x + dx >= 0) & (x + dx <= width - 1)
            & (y + dy >= 0) & (y + dy <= height - 1)
            & (x + random_dx >= 0) & (x + random_dx <= width - 1)
            & (y + random_dy >= 0) & (y + random_dy <= height - 1))


def measure(pair, bbox, mask, dx, dy, random_dx, random_dy):
    """shift(t2, (-dy,-dx))[y,x] = t2[y+dy,x+dx], in the r2 convention.

    All three errors use the same subset of the original, undilated mask.
    A piece with no common valid pixels is untestable, never counted as passed.
    """
    y, x = np.nonzero(mask)
    if not len(y):
        raise ValueError("Empty piece mask")
    y, x = y + bbox[1], x + bbox[0]
    keep = support(y, x, pair[0].shape, dx, dy, random_dx, random_dy)
    count = int(keep.sum())
    if not count:
        return np.nan, np.nan, np.nan, 0.0, 0
    y, x = y[keep], x[keep]
    a, b = pair[0][y, x], pair[1][y, x]
    corrected = sample_rgb(pair[1], y + dy, x + dx)
    random = sample_rgb(pair[1], y + random_dy, x + random_dx)
    errors = [float(np.abs(a - other).mean(dtype=np.float64))
              for other in (b, corrected, random)]
    return *errors, count / len(keep), count


def read_inputs(args, dataset, pool, frozen, baseline):
    with np.load(args.r2 / dataset / "pieces_offset.npz", allow_pickle=False) as z:
        if (str(z["dataset"]) != dataset or str(z["primary_method"]) != "direct"
                or json.loads(str(z["config"])) != baseline["config"]
                or z["image_names"].tolist() != pool.images
                or any(not np.array_equal(z[key], getattr(pool, key))
                       for key in ("image_id", "piece_id", "label", "source"))
                or not np.array_equal(z["split"], pool.meta[:, 11].astype(bool))):
            raise ValueError(f"{dataset}: r2 offsets and current Pool/config differ")
        fields = ("label", "source", "area", "area_bucket", "dx", "dy", "magnitude",
                  "relative", "unestimable", "interpolation_fraction")
        data = {key: z[key] for key in fields}
        known = np.isfinite(data["magnitude"])
        if (not np.array_equal(known, z["direct_valid"])
                or not np.array_equal(known, ~data["unestimable"])
                or not np.isfinite(data["dx"][known]).all()
                or not np.isfinite(data["dy"][known]).all()):
            raise ValueError(f"{dataset}: inconsistent r2 direct validity")
    with np.load(args.verifier / dataset / "scores.npz", allow_pickle=False) as z:
        if str(z["run_id"]) != frozen["run_id"] or str(z["dataset"]) != dataset:
            raise ValueError("V1 scores and frozen run differ")
        indices = pool.feature_indices(z["image"], z["piece_id"])
        scores = np.full(len(pool), np.nan, np.float32)
        scores[indices] = z["score"]
    if not np.isfinite(scores).all():
        raise ValueError("V1 scores must cover all Pool rows")
    return data, scores, known


def select_figures(pool, data, known, old_examples, seed):
    """Six uniform draws per dataset/bucket, excluding the saved r2 extremes."""
    wanted = {}
    for rank, row in enumerate(old_examples, 1):
        if row["dataset"] != pool.dataset:
            continue
        index = int(pool.feature_indices(np.asarray([row["image"]]),
                                        np.asarray([row["piece_id"]]))[0])
        if not known[index] or pool.label[index] != 0:
            raise ValueError("Saved r2 extreme is not an estimable negative")
        wanted[index] = dict(kind="r2_top10", rank=rank, r2_file=row["file"])
    extreme_indices = list(wanted)
    negative = known & (pool.label == 0) & (pool.source != SOURCES.index("pred"))
    negative[extreme_indices] = False
    bucket = magnitude_bucket(data["magnitude"])
    rng = np.random.default_rng(seed)
    counts = []
    for b, label in enumerate(BUCKETS):
        eligible = np.flatnonzero(negative & (bucket == b))
        chosen = rng.choice(eligible, size=min(6, len(eligible)), replace=False)
        counts.append(dict(bucket=label, candidates=len(eligible), drawn=len(chosen)))
        for rank, index in enumerate(chosen, 1):
            wanted[int(index)] = dict(kind="random_negative", bucket=b, rank=rank)
    return wanted, counts


def render(path, pair, bbox, mask, dx, dy, random_dx, random_dy, title):
    height, width = pair[0].shape[:2]
    x0, y0, x1, y1 = bbox
    left, top, right, bottom = max(0, x0 - 24), max(0, y0 - 24), min(width, x1 + 24), min(height, y1 + 24)
    y, x = np.mgrid[top:bottom, left:right]
    a, b = (image[top:bottom, left:right] for image in pair)
    corrected = sample_rgb(pair[1], y + dy, x + dx)
    before = np.abs(a - b).mean(-1)
    after = np.abs(a - corrected).mean(-1)
    region = np.zeros(y.shape, bool)
    region[y0 - top:y1 - top, x0 - left:x1 - left] = mask
    common = region & support(y, x, pair[0].shape, dx, dy, random_dx, random_dy)
    fig, axes = plt.subplots(1, 5, figsize=(16, 3.6))
    images = (a, b, np.ma.masked_invalid(corrected), before, np.ma.masked_invalid(after))
    names = ("t1", "t2", "t2 corrected", "|t1-t2|", "|t1-corrected|")
    for i, (ax, image, name) in enumerate(zip(axes, images, names)):
        ax.set_facecolor("0.5")
        ax.imshow(image, **(dict(cmap="magma", vmin=0, vmax=1) if i >= 3 else {}))
        if region.any() and not region.all():
            ax.contour(region, levels=[0.5], colors="cyan", linewidths=0.6)
        if common.any() and not common.all():
            ax.contour(common, levels=[0.5], colors="lime", linewidths=0.5)
        ax.set_title(name)
        ax.axis("off")
    fig.suptitle(title, fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def summarize_check(values, dev, mask):
    before, after, random = (values[key][mask] for key in ("before", "after", "random"))
    tested = np.isfinite(before) & np.isfinite(after) & np.isfinite(random)
    b, a, r = before[tested], after[tested], random[tested]
    positive_before = b > 0
    return dict(estimable=int(mask.sum()), tested=int(tested.sum()),
                no_common_pixels=int((~tested).sum()),
                improved=int((a < b).sum()), improved_fraction=fraction((a < b).sum(), len(b)),
                better_than_random=int((a < r).sum()),
                better_than_random_fraction=fraction((a < r).sum(), len(b)),
                trusted=int(((a < b) & (a < r)).sum()),
                trusted_fraction=fraction(((a < b) & (a < r)).sum(), len(b)),
                before=distribution(b), after=distribution(a), random=distribution(r),
                decrease=distribution(b - a), random_decrease=distribution(b - r),
                relative_decrease=distribution((b[positive_before] - a[positive_before]) / b[positive_before]),
                zero_before=int((~positive_before).sum()),
                advantage_over_random=distribution(r - a),
                relative_advantage_over_random=distribution((r[positive_before] - a[positive_before]) / b[positive_before]),
                dev=distribution(dev[mask]), support_fraction=distribution(values["support_fraction"][mask]))


def rate(data, target, allowed):
    selected = target & allowed & ~data["unestimable"]
    result = impact(data, selected)
    result.update(target_total=int(target.sum()),
                  r2_estimable=int((target & ~data["unestimable"]).sum()),
                  r2_unknown=int((target & data["unestimable"]).sum()),
                  not_retained_estimable=int((target & ~data["unestimable"] & ~allowed).sum()),
                  retained=int(selected.sum()),
                  retained_fraction_r2=fraction(selected.sum(), (target & ~data["unestimable"]).sum()))
    for threshold in (3, 5):
        count = int((selected & (data["magnitude"] > threshold)).sum())
        result[f"gt{threshold}"] = count
        result[f"gt{threshold}_fraction"] = fraction(count, selected.sum())
    return result


def comparison(data, scores, selected, allowed, threshold):
    negative = selected & (data["label"] == 0)
    small = metrics(data["label"], scores, selected & allowed & (data["magnitude"] <= 3), threshold)
    large = metrics(data["label"], scores, selected & allowed & (data["magnitude"] > 3), threshold)
    difference = {key: large[key] - small[key] if large[key] is not None and small[key] is not None else None
                  for key in ("auc", "ap")}
    return dict(negative=rate(data, negative, allowed),
                negative_by_source={name: rate(data, negative & (data["source"] == i), allowed)
                                    for i, name in enumerate(SOURCES)},
                negative_by_area={name: rate(data, negative & (data["area_bucket"] == i), allowed)
                                  for i, name in enumerate(AREA_LABELS)},
                high_score_negative=rate(data, negative & (scores >= 0.8197), allowed),
                high_score_negative_exact=rate(data, negative & (scores >= threshold), allowed),
                v1=dict(small_le3=small, large_gt3=large, large_minus_small=difference))


def process_dataset(args, dataset, dataset_number, frozen, validation, baseline):
    started = time.monotonic()
    pool = Pool(dataset)
    data, scores, known = read_inputs(args, dataset, pool, frozen, baseline)
    threshold = float(frozen["recall"]["B+G0.05+H1"]["threshold"])
    recall = (pool.source != SOURCES.index("pred")) & (pool.label >= 0)
    evaluation = recall if dataset == "second" else recall & pool.image_mask(validation)
    wanted, figure_sampling = select_figures(pool, data, known, baseline["examples"]["negative_pieces"],
                                              args.seed + 100 + dataset_number)
    # Float64 keeps strict before/after comparisons consistent with the saved errors.
    values = {key: np.full(len(pool), np.nan, np.float64) for key in MEASUREMENTS}
    support_pixels = np.zeros(len(pool), np.int32)
    dev = np.full(len(pool), np.nan, np.float32)
    rng = np.random.default_rng(args.seed + dataset_number)
    angle = np.full(len(pool), np.nan, np.float64)
    angle[known] = rng.uniform(0, 2 * np.pi, size=int(known.sum()))
    random_dx, random_dy = data["magnitude"] * np.cos(angle), data["magnitude"] * np.sin(angle)
    image_medians, examples = [], []
    for number, name in enumerate(pool.images, 1):
        sl = pool.slices[name]
        indices = np.flatnonzero(known[sl]) + sl.start
        if not len(indices):
            image_medians.append(dict(image=name, n=0, dx=None, dy=None))
            continue
        center = np.median(np.column_stack((data["dx"][indices], data["dy"][indices])), axis=0)
        dev[indices] = np.hypot(data["dx"][indices] - center[0], data["dy"][indices] - center[1])
        image_medians.append(dict(image=name, n=len(indices), dx=float(center[0]), dy=float(center[1])))
        pair = [load_rgb(dataset, name, view).astype(np.float32) / 255 for view in ("t1", "t2")]
        if pair[0].shape != pair[1].shape:
            raise ValueError(f"{dataset}/{name}: image shape mismatch")
        masks = load_piece_masks(dataset, name)
        for index in indices:
            piece_id = int(pool.piece_id[index])
            bbox, mask = masks[piece_id]
            if int(mask.sum()) != int(data["area"][index]):
                raise ValueError(f"{dataset}/{name}/{piece_id}: mask area differs from r2")
            dx, dy = float(data["dx"][index]), float(data["dy"][index])
            rd_x, rd_y = random_dx[index], random_dy[index]
            result = measure(pair, bbox, mask, dx, dy, rd_x, rd_y)
            for key, value in zip(MEASUREMENTS, result[:4]):
                values[key][index] = value
            support_pixels[index] = result[4]
            if index in wanted:
                choice = wanted[index]
                prefix = (f"top_{choice['rank']:02d}" if choice["kind"] == "r2_top10"
                          else f"random_b{choice['bucket']}_{choice['rank']:02d}")
                filename = f"{prefix}_{dataset}_{Path(name).stem}_{piece_id}.png"
                title = (f"{dataset}/{name} piece={piece_id}  magnitude={data['magnitude'][index]:.3f}px "
                         f" dev={dev[index]:.3f}px  dx={dx:.3f} dy={dy:.3f}\n"
                         f"MAE {result[0]:.5f} -> {result[1]:.5f}; random={result[2]:.5f}; "
                         f"support={result[4]}/{int(mask.sum())}; cyan=mask, green=common support")
                render(args.figures / filename, pair, bbox, mask, dx, dy, rd_x, rd_y, title)
                examples.append(dict(**choice, dataset=dataset, image=name, piece_id=piece_id,
                                     file=f"figures/exp6i/verify/{filename}", magnitude=float(data["magnitude"][index]),
                                     dx=dx, dy=dy, dev=float(dev[index]),
                                     random_dx=float(rd_x), random_dy=float(rd_y),
                                     **{key: finite(values[key][index]) for key in MEASUREMENTS},
                                     support_pixels=result[4]))
        if number % 100 == 0:
            print(dataset, number, len(pool.images), f"elapsed={time.monotonic() - started:.1f}s", flush=True)
    tested = known & np.isfinite(values["before"]) & np.isfinite(values["after"]) & np.isfinite(values["random"])
    trusted = tested & (values["after"] < values["before"]) & (values["after"] < values["random"])
    expected = known & (support_pixels > 0)
    if not np.array_equal(tested, expected) or len(examples) != len(wanted):
        raise ValueError(f"{dataset}: incomplete or non-finite verification results")
    bucket = magnitude_bucket(data["magnitude"])
    scopes = dict(all_pieces=known, evaluation_recall=known & evaluation)
    checks = {}
    for scope, scope_mask in scopes.items():
        checks[scope] = [dict(bucket=label, label=label_name,
                              **summarize_check(values, dev, scope_mask & (bucket == b) & (pool.label == y)))
                         for b, label in enumerate(BUCKETS)
                         for y, label_name in ((0, "negative"), (1, "positive"), (-1, "unlabeled"))]
    comparisons = {}
    for name, selected in (("evaluation", evaluation), ("all_recall", recall)):
        comparisons[name] = {method: comparison(data, scores, selected, allowed, threshold)
                             for method, allowed in (("r2_all_estimable", known),
                                                     ("common_support", tested), ("trusted", trusted))}
    old = baseline["datasets"][dataset]["pieces"]
    # Recomputed baseline must reproduce the saved main figures before presenting r3.
    reproduced = comparisons["evaluation"]["r2_all_estimable"]
    for key, actual in (("evaluation_negative", reproduced["negative"]),
                        ("high_score_negative", reproduced["high_score_negative"]),
                        ("high_score_negative_exact", reproduced["high_score_negative_exact"])):
        if any(actual[field] != old[key][field] for field in ("estimated", "absolute_gt3", "either")):
            raise ValueError(f"{dataset}: recomputed r2 {key} differs from stats.json")
    for group in ("small_le3", "large_gt3"):
        for key in ("n", "positive", "negative", "auc", "ap"):
            actual, saved = reproduced["v1"][group][key], old["v1"][group][key]
            if (actual is None) != (saved is None) or (actual is not None and not np.isclose(actual, saved, rtol=0, atol=1e-10)):
                raise ValueError(f"{dataset}: recomputed r2 V1 {group}/{key} differs")
    save_npz(args.out / dataset / "pieces_verify.npz", dataset=dataset, seed=args.seed + dataset_number,
             pool_index=np.flatnonzero(known), image_names=np.asarray(pool.images),
             image_id=pool.image_id[known], piece_id=pool.piece_id[known], label=pool.label[known],
             source=pool.source[known], area=data["area"][known], area_bucket=data["area_bucket"][known],
             evaluation=evaluation[known], score=scores[known],
             dx=data["dx"][known], dy=data["dy"][known], magnitude=data["magnitude"][known],
             bucket=bucket[known], dev=dev[known], random_dx=random_dx[known], random_dy=random_dy[known],
             support_pixels=support_pixels[known], tested=tested[known], trusted=trusted[known],
             **{key: value[known] for key, value in values.items()})
    write_json(args.out / dataset / "image_medians.json", image_medians)
    return dict(total=len(pool), estimable=int(known.sum()), unknown=int((~known).sum()),
                tested=int(tested.sum()), no_common_pixels=int((known & ~tested).sum()), trusted=int(trusted.sum()),
                dev=distribution(dev[known]), image_medians_file=f"{dataset}/image_medians.json",
                checks=checks, comparisons=comparisons,
                r2_original={key: old[key] for key in ("all_negative", "evaluation_negative", "high_score_negative",
                                                     "high_score_negative_exact", "negative_by_source", "negative_by_area", "v1")},
                baseline_reproduced=True, figure_sampling=figure_sampling, figures=examples,
                elapsed_seconds=time.monotonic() - started)


def fmt(value, percent=False):
    if value is None:
        return "无可用值"
    return f"{100 * value:.2f}%" if percent else f"{value:.5f}"


def write_markdown(args, report):
    lines = ["# 实验 6i r3：已有偏移的补偿核验", "",
             "使用 r2 已有直接偏移，全部可估计块逐块核验。RGB 转到 [0,1]，在原始块掩膜内计算三通道平均绝对差异。",
             "r2 的 dx 向右、dy 向下；补偿为 shift(t2, (-dy,-dx))，输出 (y,x) 从 t2(y+dy,x+dx) 双线性采样。",
             "随机对照保留幅度，方向在 [0,2π) 均匀抽取一次。三个误差使用共同有效像素；无共同像素的块单列。",
             "可信块定义：补偿误差 < 原始误差，且补偿误差 < 随机误差。规则不使用块标签或 V1 分数。",
             "可信是本检验的通过标记；本次没有独立几何真值精度。dev 为块偏移与同图全部可估计块的分量中位偏移之欧氏距离。",
             "各比例分母为表中所列保留块；原始不可估计项及检验未通过项均保留计数。全文只报告描述性数字。", ""]
    lines += ["下降 = 原始误差 − 补偿误差；相对下降 = 下降 / 原始误差（原始误差为 0 时单列）；"
              "随机优势 = 随机误差 − 补偿误差。绝对或相对明显偏移沿用 r2：幅度 >3 px 或幅度/块包围框长边 >0.3。", ""]
    for dataset, result in report["datasets"].items():
        lines += [f"## {dataset}", "",
                  f"总块数 {result['total']}，r2 可估计 {result['estimable']}，未知 {result['unknown']}；"
                  f"完成差异比较 {result['tested']}，无共同像素 {result['no_common_pixels']}，可信 {result['trusted']}。",
                  f"dev 中位数 {fmt(result['dev']['quantiles']['50'])} px，p90 {fmt(result['dev']['quantiles']['90'])} px，"
                  f"p99 {fmt(result['dev']['quantiles']['99'])} px。", ""]
        for scope, rows in result["checks"].items():
            lines += [f"### 补偿检验：{scope}", "",
                      "| 幅度桶 px | 标签 | 可估计/有共同像素 | 误差下降块占比 | 下降中位数 | 相对下降中位数 | 随机优势中位数 | 优于随机占比 | 可信占比 | dev 中位数 |",
                      "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
            for row in rows:
                if row["label"] == "unlabeled":
                    continue
                lines.append(f"| {row['bucket']} | {'负例' if row['label'] == 'negative' else '正例'} | "
                             f"{row['estimable']}/{row['tested']} | {fmt(row['improved_fraction'], True)} | "
                             f"{fmt(row['decrease']['quantiles']['50'])} | {fmt(row['relative_decrease']['quantiles']['50'], True)} | "
                             f"{fmt(row['advantage_over_random']['quantiles']['50'])} | {fmt(row['better_than_random_fraction'], True)} | "
                             f"{fmt(row['trusted_fraction'], True)} | {fmt(row['dev']['quantiles']['50'])} |")
            lines.append("")
        old = result["r2_original"]
        current = result["comparisons"]["evaluation"]
        lines += ["### 主指标并列", "",
                  "test 使用全部有标签 recall 块；second_cv 使用冻结验证图的有标签 recall 块；均排除 pred。",
                  "r2 原报告没有 >5 px 数字，该项从 r2 原数组按同一范围重算。", "",
                  "| 指标 | r2 原报告 | r2 全部可估计重算 | 有共同像素 | 可信块 |", "|---|---:|---:|---:|---:|"]
        rows = [("负例 >3 px", old["evaluation_negative"]["absolute_fraction_estimated"], "negative", "gt3_fraction"),
                ("负例 >5 px", None, "negative", "gt5_fraction"),
                ("高分负例 ≥0.8197，>3 px", old["high_score_negative"]["absolute_fraction_estimated"], "high_score_negative", "gt3_fraction"),
                ("高分负例 ≥0.8197，>5 px", None, "high_score_negative", "gt5_fraction"),
                ("高分负例 ≥冻结精确阈值，>3 px", old["high_score_negative_exact"]["absolute_fraction_estimated"], "high_score_negative_exact", "gt3_fraction"),
                ("高分负例 ≥冻结精确阈值，>5 px", None, "high_score_negative_exact", "gt5_fraction")]
        for label, original, group, key in rows:
            columns = [fmt(current[method][group][key], True) for method in ("r2_all_estimable", "common_support", "trusted")]
            lines.append(f"| {label} | {fmt(original, True)} | " + " | ".join(columns) + " |")
        for key in ("auc", "ap"):
            columns = [fmt(current[method]["v1"]["large_minus_small"][key]) for method in ("r2_all_estimable", "common_support", "trusted")]
            lines.append(f"| 大偏移减小偏移 V1 {key.upper()} | {fmt(old['v1']['large_minus_small'][key])} | " + " | ".join(columns) + " |")
        lines += ["", f"冻结精确阈值：{report['threshold_exact']:.10f}。", "",
                  "| 负例范围 | r2 可估计 | 有共同像素 | 可信 | 可信/r2 可估计 |", "|---|---:|---:|---:|---:|"]
        for key in ("negative", "high_score_negative", "high_score_negative_exact"):
            a, b, c = [current[m][key] for m in ("r2_all_estimable", "common_support", "trusted")]
            lines.append(f"| {key} | {a['retained']} | {b['retained']} | {c['retained']} | {fmt(c['retained_fraction_r2'], True)} |")
        lines += ["", "| 方法 | 小偏移 n/正例/负例 | 小偏移 AUC/AP | 大偏移 n/正例/负例 | 大偏移 AUC/AP |",
                  "|---|---|---|---|---|"]
        for method, record in current.items():
            small, large = record["v1"]["small_le3"], record["v1"]["large_gt3"]
            lines.append(f"| {method} | {small['n']}/{small['positive']}/{small['negative']} | {fmt(small['auc'])}/{fmt(small['ap'])} | "
                         f"{large['n']}/{large['positive']}/{large['negative']} | {fmt(large['auc'])}/{fmt(large['ap'])} |")
        for scope, comparisons in result["comparisons"].items():
            lines += ["", f"### 来源与面积：{scope}", "",
                      "all_recall 与 r2 原来源、面积表相同，second_cv 包含训练图；evaluation 仅包含主指标范围。", "",
                      "| 分组 | r2 可估计/可信 | r2 >3 / >5 | 可信 >3 / >5 | r2 原报告绝对或相对明显偏移 | 可信绝对或相对明显偏移 |",
                      "|---|---:|---:|---:|---:|---:|"]
            for dimension in ("negative_by_source", "negative_by_area"):
                for label, a in comparisons["r2_all_estimable"][dimension].items():
                    b = comparisons["trusted"][dimension][label]
                    original = old[dimension][label]["either_fraction_estimated"] if scope == "all_recall" else None
                    lines.append(f"| {label} | {a['retained']}/{b['retained']} | {fmt(a['gt3_fraction'], True)} / {fmt(a['gt5_fraction'], True)} | "
                                 f"{fmt(b['gt3_fraction'], True)} / {fmt(b['gt5_fraction'], True)} | {fmt(original, True)} | {fmt(b['either_fraction_estimated'], True)} |")
        lines += ["", "### 随机负例与 r2 最大偏移负例", "",
                  "每桶均匀随机抽取最多 6 个负例，排除 r2 已列出的 10 个极值；不足 6 个时保留全部。随机图覆盖该数据集全部 recall 负例。",
                  "面板依次为 t1、t2、补偿 t2、原始差异、补偿差异。青色为原掩膜，绿色为共同支持；差异色标固定 [0,1]。", ""]
        for example in result["figures"]:
            lines += [f"{example['kind']}：{example['image']} / {example['piece_id']}，幅度 {example['magnitude']:.3f} px，"
                      f"dev {example['dev']:.3f} px，差异 {fmt(example['before'])} → {fmt(example['after'])}，随机 {fmt(example['random'])}。", "",
                      f"![补偿核验]({example['file']})", ""]
    lines += ["## 记录范围", "",
              "verify.json 保存原 r2 主数字、重新计算值、共同支持子集及可信子集；逐块误差与随机向量见 pieces_verify.npz。",
              "r2 可估计集合沿用其原有筛选，其中含真值变化比例条件；本次可信检验不增加标签条件。",
              "差异下降与同一图像上的筛选结果属于同一份证据，未作为独立准确率；未给出几何正确率或因果结论。", ""]
    (args.out / "exp6i_verify.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r2", type=Path, default=ROOT / "misregistration/r2")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "misregistration/r3")
    parser.add_argument("--verifier", type=Path, default=ROOT / "verifier/none")
    parser.add_argument("--seed", type=int, default=1021)
    args = parser.parse_args()
    args.out, args.r2 = args.out.resolve(), args.r2.resolve()
    if args.out == args.r2 or args.r2 in args.out.parents or args.out in args.r2.parents:
        parser.error("r3 output must be separate from the r2 input directory")
    if (args.out / "verify.json").exists():
        parser.error("verify.json already exists; use a new output directory")
    if args.seed < 0:
        parser.error("seed must be nonnegative")
    started = time.monotonic()
    baseline = json.loads((args.r2 / "stats.json").read_text())
    frozen = json.loads((args.verifier / "thresholds_second_cv.json").read_text())
    _, validation = verifier_split()
    if (frozen["validation_images"] != validation or frozen["run_id"] != baseline["frozen_run_id"]
            or float(frozen["recall"]["B+G0.05+H1"]["threshold"]) != baseline["threshold_exact"]):
        raise ValueError("Frozen V1 run/validation split/threshold differs from r2")
    if len(baseline["examples"]["negative_pieces"]) != 10:
        raise ValueError("Expected the ten saved r2 largest-offset negative examples")
    args.figures = args.out / "figures/exp6i/verify"
    args.figures.mkdir(parents=True, exist_ok=True)
    report = dict(r2=str(args.r2), out=str(args.out), verifier=str(args.verifier), seed=args.seed,
                  sampling="all r2-estimable pieces; no statistical subsampling",
                  error="mean absolute RGB error in [0,1] on identical common-support mask pixels",
                  correction="output(y,x)=t2(y+dy,x+dx), bilinear, no padding in measured support",
                  random="one independent uniform angle per piece, same displacement magnitude",
                  trusted_rule="after < before and after < random; no label/score/dev threshold",
                  dev="distance to component-wise median of all r2-estimable piece offsets in that image",
                  independent_geometric_accuracy=None, bucket_labels=BUCKETS,
                  frozen_run_id=frozen["run_id"], threshold_task=0.8197, threshold_exact=baseline["threshold_exact"],
                  validation_images=validation, datasets={})
    for number, dataset in enumerate(DATASETS):
        report["datasets"][dataset] = process_dataset(args, dataset, number, frozen, validation, baseline)
        print("verification complete", dataset, flush=True)
    report["elapsed_seconds"] = time.monotonic() - started
    write_markdown(args, report)
    write_json(args.out / "verify.json", report)
    print(f"complete: {args.out}; elapsed={report['elapsed_seconds']:.1f}s", flush=True)


if __name__ == "__main__":
    main()
