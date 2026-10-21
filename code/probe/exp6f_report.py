"""Summarize completed 6f/6b evaluations and diagnostics; exp5-env, CPU.

Estimate: <1 GB RAM, <1 min (unmeasured). No inference or test-driven selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from exp6b_eval import COMBINATIONS
from exp6b_report import number, table


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True, help="Evaluation directories; include 6b baselines")
    parser.add_argument("--recall_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lines = ["# 实验 6f：V1 变体", "", "仅汇总已有结果。模型、早停、来源组合、阈值及 k 均由验证集选择；"
             "测试曲线与测试最优值用于结果描述。", ""]
    classification, pixels, targets, choices, timings, ablations, importances, signs = ([] for _ in range(8))
    evaluations = []
    for directory in args.runs:
        label = directory.name
        calibration = read(directory / "thresholds_second_cv.json")
        rule = calibration.get("rule", "threshold")
        source = directory
        available = [directory / f"eval_{ds}.json" for ds in ("second_cv", "second")]
        available = [p for p in available if p.exists()]
        if not available:
            raise FileNotFoundError(f"{directory}: no completed evaluation")
        pairs = {}
        for path in available:
            result = read(path)
            if result["run_id"] != calibration["run_id"]:
                raise ValueError(f"{path}: threshold run mismatch")
            if (result.get("feature_config") or {}).get("scores_dir"):
                source = Path(result["feature_config"]["scores_dir"])
            ds = result["dataset"]
            pairs[ds] = result
            evaluations.append((label, ds, result))
            m = result["recall"]["overall"]
            p = result["pixels"]["pixel_precision_at_recall_80"]
            classification.append([label, ds, number(m["auc"]), number(m["ap"]),
                                   number(m["recall_80"]["fpr"], True), number(m["recall_90"]["fpr"], True),
                                   number(p["precision"], True), number(p["f1_half"], True)])
            for key, value in result["pixels"].items():
                if not isinstance(value, dict) or "delta_f1" not in value:
                    continue
                pixels.append([label, ds, key, number(value["f1"], True), number(value["precision"], True),
                               number(value["recall"], True), number(value["delta_f1"], True, True),
                               value["added_tp"], value["added_fp"], value["removed_tp"], value["removed_fp"]])
                targets.append([label, ds, key, *[number(value["target_recall"][b]["recall"], True)
                                for b in ("all", "<100", "100-1000", ">1000")]])
            if rule == "budget":
                lines += [f"`{label}/{ds}` 空预测图的基数为全 `{ds}` 数据集平均预测像素 "
                          f"{result['pixels']['empty_prediction_base']:.2f}；统计包含空预测图，不读取真值。", ""]
        for key, result in calibration["recall"].items():
            choices.append([label, key, "k" if rule == "budget" else "分数阈值",
                            f"{result['threshold']:.8g}", number(result["delta_f1"], True, True)])
        if "second_cv" in pairs and "second" in pairs:
            for key in COMBINATIONS:
                v = pairs["second_cv"]["pixels"]["recall/" + key]["delta_f1"]
                t = pairs["second"]["pixels"]["recall/" + key]["delta_f1"]
                signs.append([label, key, number(v, True, True), number(t, True, True),
                              "是" if (v > 0) == (t > 0) and (v < 0) == (t < 0) else "否"])
        if (source / "val_metrics.json").exists():
            report = read(source / "val_metrics.json")
            if report["run_id"] != calibration["run_id"]:
                raise ValueError(f"{source}: training run mismatch")
            timings.append([label, report["best_epoch"],
                            f"{report['seconds'] / 3600:.3f}" if "seconds" in report else "—",
                            str(source / "train_log.jsonl")])
        if (source / "ablations.json").exists():
            for row in read(source / "ablations.json")["rows"]:
                ablations.append([label, row["group"], number(row["auc"]), number(row["ap"]),
                                  number(row.get("delta_auc")), number(row.get("delta_ap"))])
        elif (source / "ablations").exists():
            full = read(source / "val_metrics.json")["selection_population"]
            ablations.append([label, "full", number(full["auc"]), number(full["ap"]), "—", "—"])
            for path in sorted((source / "ablations").glob("*/val_metrics.json")):
                row = read(path)["selection_population"]
                ablations.append([label, path.parent.name, number(row["auc"]), number(row["ap"]),
                                  number(row["auc"] - full["auc"]), number(row["ap"] - full["ap"])])
        if (source / "importance.json").exists():
            report = read(source / "importance.json")
            for i, name in enumerate(report["features"]):
                importances.append([label, name, report["samples"],
                                    number(report["metrics"]["auc"]["mean"][i]),
                                    number(report["metrics"]["auc"]["std"][i]),
                                    number(report["metrics"]["ap"]["mean"][i])])
    for title, headers, rows in (
        ("分类与召回 80% 像素精度", ["版本", "数据集", "AUC", "AP", "FPR@80%", "FPR@90%", "新增像素精度%", "F1/2%"], classification),
        ("端到端结果", ["版本", "数据集", "操作", "F1%", "P%", "R%", "ΔF1百分点", "新增TP", "新增FP", "删除TP", "删除FP"], pixels),
        ("目标召回", ["版本", "数据集", "操作", "全部%", "<100%", "100–999%", "≥1000%"], targets),
        ("验证集选择", ["版本", "来源", "规则", "选定值", "验证ΔF1百分点"], choices),
        ("验证与测试符号", ["版本", "来源", "验证ΔF1", "测试ΔF1", "符号一致"], signs),
        ("训练痕迹", ["版本", "最佳轮次", "训练小时", "日志"], timings),
        ("验证集特征组消融", ["版本", "删除组", "AUC", "AP", "ΔAUC", "ΔAP"], ablations),
        ("验证集 permutation importance", ["版本", "特征", "样本数", "AUC下降均值", "AUC下降标准差", "AP下降均值"], importances),
    ):
        if rows:
            lines += [f"## {title}", ""] + table(headers, rows)
    if args.recall_dir:
        rows = []
        for path in sorted(args.recall_dir.glob("*.json")):
            report = read(path)
            for key, combo in report["combinations"].items():
                for value in combo["budgets"]:
                    baseline = (report["baseline_hits"]["all"] / report["totals"]["all"]
                                if report["totals"]["all"] else None)
                    recall = value["target_recall"]["all"]
                    rows.append([report["model"], report["dataset"], key,
                                 number(value["budget"], True), number(value["actual_fp_ratio"], True),
                                 number(recall, True), number(recall - baseline, True, True)
                                 if recall is not None and baseline is not None else "—"])
        lines += ["## 召回优先诊断", "", "预算点取完整曲线上实际 FP 不超过预算的点，不插值。"
                  "测试曲线使用真值计数，只用于诊断；oracle 按切块真值占比排序，0.5 点对应原切块 oracle。", ""]
        lines += table(["版本", "数据集", "来源", "FP预算%", "实际FP%", "目标召回%", "较检测器提高百分点"], rows)
    lines += ["## 判断依据与限制", "",
              "A1/A2 的符号是否一致见表；符号变化本身不足以确认概率刻度偏移的因果关系。"
              "上下文及树模型是否超过 AUC 0.874、召回 80% 像素精度是否接近 37%，可由分类表直接核对。"
              "B1/B2 增益需与同一来源、同一划分的表格基线比较；B1 的第 0 轮可保留验证 AP 最好的零修正基线，"
              "并不保证测试集或 F1 提升。C、A3 不在本批。", ""]
    test_rows = [(name, key, r["pixels"]["recall/" + key]["delta_f1"])
                 for name, ds, r in evaluations if ds == "second" for key in COMBINATIONS]
    if test_rows:
        name, key, delta = max(test_rows, key=lambda row: row[2])
        lines += [f"所列已冻结版本的测试补漏最大 ΔF1 为 {delta * 100:+.3f} 个百分点（{name}，{key}）。"
                  "这是事后描述，不作为版本选择依据。", ""]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
