"""Experiment 6g audit statistics: CPU, conda_envs/exp5-env.

Estimated <1 GB RAM and <1 minute on one CPU for 320 answers (not measured).
Writes JSON and PNG/SVG figures; missing, skipped and unclear answers stay separate.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from exp6b_common import ROOT, run_dir, write_json

CHOICES = {"changed", "unchanged", "unclear", "skip"}
DEFINITE = {"changed", "unchanged"}
AREA_BINS = ("<50", "50–300", "300–1000", ">1000")


def wilson(successes, count):
    if count <= 0:
        return None
    z = 1.959963984540054
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def choice(row, answers):
    return answers.get(row["id"], {}).get("choice")


def disagreement(row, answers):
    return int((choice(row, answers) == "changed") != bool(row["label"]))


def summarize(rows, answers):
    counts = {key: sum(choice(row, answers) == key for row in rows) for key in CHOICES}
    definite = [row for row in rows if choice(row, answers) in DEFINITE]
    errors = sum(disagreement(row, answers) for row in definite)
    n = len(definite)
    judged = n + counts["unclear"]
    return dict(
        sampled=len(rows), missing=sum(choice(row, answers) is None for row in rows),
        skipped=counts["skip"], unclear=counts["unclear"], determinate=n,
        changed=counts["changed"], unchanged=counts["unchanged"], disagreements=errors,
        disagreement_rate=errors / n if n else None, wilson95=wilson(errors, n),
        unclear_rate=counts["unclear"] / judged if judged else None,
        unclear_rate_denominator="changed + unchanged + unclear; excludes skipped/missing",
    )


def area_bucket(area):
    if area < 50:
        return AREA_BINS[0]
    if area < 300:
        return AREA_BINS[1]
    if area <= 1000:
        return AREA_BINS[2]
    return AREA_BINS[3]


def pool_estimate(groups, answers):
    """Poststratify within the random audit's source/label cells."""
    total = sum(population for _, population, _ in groups)
    point, lower_var, upper_var = 0.0, 0.0, 0.0
    known_lower, known_upper = 0.0, 0.0
    covered = 0
    cells = {}
    for name, population, rows in groups:
        if not population:
            continue
        summary = summarize(rows, answers)
        weight = population / total
        p, ci = summary["disagreement_rate"], summary["wilson95"]
        if p is not None:
            covered += population
            point += weight * p
            lower_var += (weight * (p - ci[0])) ** 2
            upper_var += (weight * (ci[1] - p)) ** 2
        # Missing/skip/unclear outcomes can lie anywhere; no claim of sampling CI here.
        n = len(rows)
        known_lower += weight * (summary["disagreements"] / n if n else 0)
        unknown = n - summary["determinate"]
        known_upper += weight * ((summary["disagreements"] + unknown) / n if n else 1)
        cells[name] = dict(population=population, population_weight=weight, **summary)
    complete = bool(total and covered == total)
    return dict(
        population=total, represented_population=covered,
        represented_population_fraction=covered / total if total else None,
        disagreement_rate=point if complete else None,
        approximate95=[max(0, point - math.sqrt(lower_var)),
                       min(1, point + math.sqrt(upper_var))] if complete else None,
        nonresponse_sensitivity_bounds=[known_lower, known_upper] if total else None,
        cells=cells,
        method="Population-weighted cell rates; MOVER combination of cell Wilson intervals. "
               "Approximate 95%, independent pieces; no image-cluster adjustment. "
               "Within each cell, definite responders represent nonresponders. "
               "A cell without definite responses makes the overall point/CI unavailable. "
               "Sensitivity bounds assign all non-definite samples error=0/1; "
               "these bounds exclude sampling uncertainty.",
    )


def duplicate_consistency(samples, answers):
    lookup = {row["id"]: row for row in samples}
    repeats = [row for row in samples if row["is_duplicate"]]
    pairs, same = 0, 0
    definite_pairs, definite_same = 0, 0
    details = []
    for row in repeats:
        original = lookup[row["original_id"]]
        a, b = choice(original, answers), choice(row, answers)
        usable = a in DEFINITE | {"unclear"} and b in DEFINITE | {"unclear"}
        binary = a in DEFINITE and b in DEFINITE
        pairs += int(usable)
        same += int(usable and a == b)
        definite_pairs += int(binary)
        definite_same += int(binary and a == b)
        details.append(dict(id=row["id"], original_id=original["id"],
                            original_choice=a, duplicate_choice=b,
                            agreement=(a == b) if usable else None))
    return dict(
        repeats=len(repeats), paired_judgments=pairs, agreements=same,
        consistency=same / pairs if pairs else None, wilson95=wilson(same, pairs),
        definite_pairs=definite_pairs, definite_agreements=definite_same,
        definite_consistency=definite_same / definite_pairs if definite_pairs else None,
        definite_wilson95=wilson(definite_same, definite_pairs),
        rule="Primary includes unclear/unclear agreements; skip/missing pairs excluded. "
             "Definite-only consistency is also reported.", pairs=details,
    )


def weighted_hidden_change(groups, answers, pixel_weight):
    """Estimate high-negative change rate; Kish-Wilson for unequal weights."""
    outcomes, weights, details = [], [], {}
    missing_cell = False
    for name, population, rows in groups:
        if not population:
            continue
        definite = [row for row in rows if choice(row, answers) in DEFINITE]
        details[name] = dict(population=population, **summarize(rows, answers))
        if not definite:
            missing_cell = True
            continue
        for row in definite:
            w = population / len(definite)
            if pixel_weight:
                w *= row["added_fp_pixels"]
            weights.append(w)
            outcomes.append(float(choice(row, answers) == "changed"))
    w, y = np.asarray(weights, float), np.asarray(outcomes, float)
    if missing_cell or not len(w) or w.sum() <= 0:
        return dict(rate=None, approximate95=None, effective_n=0, cells=details)
    p = float(np.dot(w, y) / w.sum())
    effective_n = float(w.sum() ** 2 / np.dot(w, w))
    return dict(
        rate=p, approximate95=wilson(p * effective_n, effective_n), effective_n=effective_n,
        cells=details, weighted_mass=float(w.sum()),
        method="Population/definite-response weights; approximate Kish effective-n Wilson. "
               "Pixel version additionally weights by saved-mask pixels outside the detector "
               "and outside GT; overlaps across pieces are not removed. "
               "Unclear/skip/missing excluded, with within-cell response representativeness assumed.",
    )


def corrected_precision(manifest, originals, answers, evaluation):
    diagnostic = evaluation["pixels"]["pixel_precision_at_recall_80"]
    tp, fp = int(diagnostic["added_tp"]), int(diagnostic["added_fp"])
    if tp < 0 or fp < 0 or tp + fp <= 0:
        raise ValueError("Invalid 6b diagnostic added pixel counts")
    baseline = tp / (tp + fp)
    if not math.isclose(baseline, diagnostic["precision"], abs_tol=1e-8):
        raise ValueError("6b precision and pixel counts disagree")
    previous = [r for r in originals if r["stratum"] == "S2" and r["in_high_negative_population"]]
    s3 = [r for r in originals if r["stratum"] == "S3"]
    info = manifest["strata"]["S3"]
    if len(previous) != info["excluded_previous"]:
        raise ValueError("S3 exclusions differ from the S2 high-negative audit records")
    groups = [("previous_S2_high", len(previous), previous),
              ("S3_remaining", info["available"], s3)]
    estimates = {}
    for mode, pixel_weight in (("piece", False), ("added_negative_pixel_proxy", True)):
        rate = weighted_hidden_change(groups, answers, pixel_weight)
        q, ci = rate["rate"], rate["approximate95"]
        convert = lambda value: (tp + fp * value) / (tp + fp)
        adjusted = convert(q) if q is not None else None
        adjusted_ci = [convert(value) for value in ci] if ci is not None else None
        estimates[mode] = dict(
            hidden_change_fraction=rate, corrected_precision_scenario=adjusted,
            approximate95_scenario=adjusted_ci,
            gap_to_0_3708=0.3708 - adjusted if adjusted is not None else None,
            interval_exceeds_0_3708=(adjusted_ci[0] > 0.3708) if adjusted_ci else None,
        )
    return dict(
        scenario_only=True, added_tp=tp, added_fp=fp, baseline_precision=baseline,
        f1_half_reference=0.3708, exact_6b_f1_half=diagnostic["f1_half"],
        required_hidden_fp_fraction=(0.3708 * (tp + fp) - tp) / fp if fp else None,
        audit_threshold=manifest["frozen_threshold"],
        recall80_threshold=diagnostic["threshold"], s3_only=summarize(s3, answers),
        high_negative_population=info["population"], estimates=estimates,
        limitations=[
            "S3 uses the frozen validation threshold; recall80 uses a different test diagnostic "
            "threshold and all sources, including unlabeled pieces. Transport is unverified.",
            "S3 samples the remaining high negatives after S2. Previously drawn S2 high negatives "
            "are a separate conditional census cell; diagnostic S3-only numbers are also retained.",
            "Binary piece judgments do not identify changed pixels. Pixel-weighted values are "
            "proxies using added GT-negative area, not a human pixel annotation or deduplicated union.",
            "The scenario assumes audited hidden-change fractions apply to all added FP pixels "
            "and that all original TP pixels remain correct; positive-label errors are not corrected.",
            "Intervals quantify audit sampling approximately, not transport or pixel-conversion "
            "uncertainty. The original F1/2 reference is held fixed, not re-evaluated against corrected GT.",
        ],
    )


def error_examples(originals, samples, answers):
    repeats = {}
    for row in samples:
        if row["is_duplicate"]:
            repeats.setdefault(row["original_id"], []).append(row)
    candidates = []
    for row in originals:
        if choice(row, answers) not in DEFINITE or not disagreement(row, answers):
            continue
        repeat_answers = [choice(r, answers) for r in repeats.get(row["id"], [])]
        confirmed = any(c == choice(row, answers) for c in repeat_answers)
        human = "变化" if choice(row, answers) == "changed" else "未变化"
        official = "正例" if row["label"] else "负例"
        record = {key: row[key] for key in (
            "id", "dataset", "image", "piece_id", "stratum", "label", "gt_frac",
            "area", "source", "none_score", "transition", "transition_group",
        )}
        record.update(
            answer=choice(row, answers), duplicate_confirms=confirmed,
            repeat_choices=repeat_answers, area_bucket=area_bucket(row["area"]),
            crops={suffix: f"crops/{row['id']}_{suffix}.jpg"
                   for suffix in ("t1", "t2", "context_t1", "context_t2")},
            explanation=f"人工判断为{human}，池标签为{official}；真值占比 "
                        f"{row['gt_frac']:.1%}，面积 {row['area']} 像素。需结合原图复核原因。",
        )
        candidates.append(record)
    candidates.sort(key=lambda r: (-int(r["duplicate_confirms"]), -r["area"], r["id"]))
    # Diversify the shortlist by stratum, transition, area and direction of disagreement.
    selected, seen = [], set()
    for row in candidates:
        key = (row["stratum"], row["transition_group"], row["area_bucket"], row["label"])
        if key not in seen:
            selected.append(row)
            seen.add(key)
        if len(selected) == 10:
            break
    selected_ids = {row["id"] for row in selected}
    selected.extend(row for row in candidates if row["id"] not in selected_ids)
    return dict(
        top10=selected[:10], all_disagreements=candidates,
        ranking="Duplicate-supported first, then larger area; top10 diversified by "
                "stratum/transition/area/error direction. Descriptive shortlist, not prevalence.",
    )


def save_figures(report, folder):
    folder.mkdir(parents=True, exist_ok=True)
    for filename, groups in (("strata", report["by_stratum"]), ("area", report["by_area"])):
        fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
        for index, (name, stats) in enumerate(groups.items()):
            p, ci = stats["disagreement_rate"], stats["wilson95"]
            if p is not None:
                axes[0].errorbar(index, p, yerr=[[p - ci[0]], [ci[1] - p]], fmt="o", capsize=4)
            else:
                axes[0].text(index, 0.04, "NA", ha="center")
            if stats["unclear_rate"] is not None:
                axes[1].bar(index, stats["unclear_rate"], color="#dba54c")
            else:
                axes[1].text(index, 0.04, "NA", ha="center")
        for ax in axes:
            ax.set_xticks(range(len(groups)), list(groups))
            ax.set_ylim(0, 1)
            ax.grid(axis="y", alpha=0.2)
        axes[0].set_title("Disagreement among definite answers (Wilson 95%)")
        axes[1].set_title("Unclear / (definite + unclear)")
        fig.suptitle("Audit sample; duplicates excluded; diagnostic groups overlap populations")
        for suffix in ("png", "svg"):
            fig.savefig(folder / f"{filename}.{suffix}", dpi=160)
        plt.close(fig)
    correction = report["precision_correction"]
    fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
    ax.scatter([0], [correction["baseline_precision"]], label="6b observed")
    labels = ["6b", "Piece scenario", "Pixel proxy scenario"]
    for index, estimate in enumerate(correction["estimates"].values(), 1):
        p, ci = estimate["corrected_precision_scenario"], estimate["approximate95_scenario"]
        if p is not None:
            ax.errorbar(index, p, yerr=[[p - ci[0]], [ci[1] - p]], fmt="o", capsize=4)
        else:
            ax.text(index, 0.04, "NA", ha="center")
    ax.axhline(0.3708, color="#bf4941", linestyle="--", label="Original F1/2 = 37.08%")
    ax.set_xticks(range(3), labels)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Added-pixel precision")
    ax.set_title("Transport scenarios only; thresholds differ; approximate intervals")
    ax.legend()
    for suffix in ("png", "svg"):
        fig.savefig(folder / f"precision_scenarios.{suffix}", dpi=160)
    plt.close(fig)


def interpretation(report):
    test, train = report["overall"]["test"], report["overall"]["train"]
    s3 = report["precision_correction"]["s3_only"]
    diagnosed = [key for key, value in report["by_stratum"].items()
                 if value["disagreement_rate"] is not None and value["disagreement_rate"] > 0.20]
    missing = sum(v["missing"] + v["skipped"] for v in report["by_stratum"].values())
    advice = []
    if missing:
        advice.append(f"仍有 {missing} 个原始样本缺失或跳过，当前为部分审核结果。")
    if diagnosed:
        advice.append("以下诊断层的不一致率超过 20%：" + "、".join(diagnosed)
                      + "。建议在人工确认的干净子集上补充评估，并保留原始指标。")
    elif test["disagreement_rate"] is not None:
        advice.append("当前没有分层点估计超过 20%；可继续 V2/6f，"
                      "同时结合区间和有效回答数判断证据强弱。")
    else:
        advice.append("整体估计所需的部分单元没有明确回答，暂不据此选择后续训练方案。")
    small = report["by_area"][AREA_BINS[0]]
    if small["unclear_rate"] is not None and small["unclear_rate"] > 0.20:
        advice.append("小于 50 像素样本的看不清比例超过 20%；建议单独评估小切块，"
                      "再比较训练时排除模糊样本或抗噪损失，不修改官方真值。")
    a, b = test["approximate95"], train["approximate95"]
    comparison = "缺少整体区间，暂无法比较。"
    if a is not None and b is not None:
        comparison = ("两池区间重叠；这不足以证明噪声相同。" if max(a[0], b[0]) <= min(a[1], b[1])
                      else "两池区间不重叠，提示噪声可能不同；需结合抽样和图像相关性复核。")
    return dict(
        overall_test_error=test["disagreement_rate"], overall_train_error=train["disagreement_rate"],
        s3_hidden_change_piece_fraction=s3["disagreement_rate"],
        train_test_comparison=comparison, recommendations=advice,
        precision_conclusion="修正精度仅为阈值间外推情景，不能作为已跨过 F1/2 门槛的实测证据。",
        severity_note="按面积和转移类型的结果描述本次审核样本；S3–S5 被刻意加抽，"
                      "不能直接当成这些类别在全池的错误率。",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "label_noise/audit/samples.json")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--eval", type=Path, default=run_dir("none") / "eval_second.json")
    parser.add_argument("--out", type=Path, default=ROOT / "label_noise/results/exp6g_stats.json")
    parser.add_argument("--figures", type=Path, default=ROOT / "label_noise/figures/exp6g")
    args = parser.parse_args()
    manifest = json.loads(args.samples.read_text())
    answer_path = args.answers or args.samples.parent / "audit_answers.json"
    answers = json.loads(answer_path.read_text())
    samples = manifest["samples"]
    ids = {row["id"] for row in samples}
    if not isinstance(answers, dict):
        raise ValueError("Answers must be an id -> {choice, timestamp} JSON object")
    if len(ids) != len(samples) or set(answers) - ids:
        raise ValueError("Duplicate sample IDs or answers from a different audit")
    for value in answers.values():
        if not isinstance(value, dict) or value.get("choice") not in CHOICES:
            raise ValueError("Invalid answer option")
    originals = [row for row in samples if not row["is_duplicate"]]
    strata = {name: [r for r in originals if r["stratum"] == name] for name in manifest["strata"]}
    test_groups, train_groups = [], []
    for name in ("S1", "S2"):
        for source, cell in manifest["strata"][name]["cells"].items():
            test_groups.append((f"{name}/{source}", cell["population"],
                                [r for r in strata[name] if r["sampling_source"] == source]))
    for label, cell in manifest["strata"]["S6"]["cells"].items():
        train_groups.append((f"S6/{label}", cell["population"],
                             [r for r in strata["S6"] if r["label"] == int(label)]))
    evaluation = json.loads(args.eval.read_text())
    if evaluation["dataset"] != "second" or evaluation["backbone"] != "none":
        raise ValueError("Expected the SECOND none 6b evaluation")
    if evaluation["run_id"] != manifest["run_id"]:
        raise ValueError("Audit and 6b evaluation belong to different runs")
    report = dict(
        samples_file=str(args.samples), answers_file=str(answer_path), eval_file=str(args.eval),
        original_count=len(originals), answers_count=len(answers),
        by_stratum={name: summarize(rows, answers) for name, rows in strata.items()},
        by_area={bucket: summarize([r for r in originals if area_bucket(r["area"]) == bucket], answers)
                 for bucket in AREA_BINS},
        area_boundaries="<50; 50<=area<300; 300<=area<=1000; >1000",
        by_transition={
            group: summarize([r for r in originals if (r["transition_group"] or "no_transition") == group],
                             answers)
            for group in sorted({r["transition_group"] or "no_transition" for r in originals})
        },
        overall=dict(test=pool_estimate(test_groups, answers), train=pool_estimate(train_groups, answers)),
        duplicates=duplicate_consistency(samples, answers),
        precision_correction=corrected_precision(manifest, originals, answers, evaluation),
        examples=error_examples(originals, samples, answers),
        conventions="Duplicates excluded except consistency; unclear is not counted as wrong "
                    "and is excluded from the disagreement denominator. Original answers are "
                    "never replaced by their repeats. Overall populations contain labeled recall "
                    "pieces only; S6 excludes verifier validation images.",
    )
    report["interpretation"] = interpretation(report)
    save_figures(report, args.figures)
    write_json(args.out, report)
    write_json(args.out.with_name("exp6g_error_examples.json"), report["examples"])
    print(args.out, args.figures, flush=True)


if __name__ == "__main__":
    main()
