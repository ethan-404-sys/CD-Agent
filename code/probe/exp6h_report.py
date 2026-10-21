"""6h-1 validation selection and three-seed report; exp5-env, CPU.

Request 2 CPUs / 2 GiB RAM, <2 min estimated. Reads saved JSON/logs only.
--freeze_selection reads training validation reports only, then exits.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from exp6b_common import ROOT, verifier_split, write_json
from exp6b_report import number, table

BUCKETS = ("all", "<100", "100-1000", ">1000")


def read(path):
    return json.loads(Path(path).read_text())


def group_runs(runs):
    groups = defaultdict(list)
    for run in runs:
        report = read(run / "val_metrics.json")
        config = read(run / "config.json")
        if config != report["config"]:
            raise ValueError(f"{run}: config and training report differ")
        groups[config["setting"]].append(dict(directory=str(run.resolve()), run_id=report["run_id"],
                                            config=config, report=report))
    for setting, items in groups.items():
        if sorted(r["config"]["seed"] for r in items) != [0, 1, 2]:
            raise ValueError(f"{setting}: need exactly seeds 0, 1, 2")
        items.sort(key=lambda r: r["config"]["seed"])
    return dict(groups)


def freeze(groups, path):
    if path.exists():
        raise FileExistsError(f"{path}: selection is already frozen")
    expected = {"g1_0", *(f"g1_{m}_l{v}" for m in ("cos", "sm") for v in ("0.1", "0.3", "1"))}
    if set(groups) != expected:
        raise ValueError("Final freeze requires all seven settings, each with seeds 0/1/2")
    choices = {"control": "g1_0"}
    means = {key: float(np.mean([r["report"]["selection_population"]["ap"] for r in values]))
             for key, values in groups.items()}
    for mode in ("cos", "sm"):
        eligible = [key for key in groups if key.startswith(f"g1_{mode}_")]
        choices[mode] = min(eligible, key=lambda k: (-means[k], groups[k][0]["config"]["temporal_lambda"]))
    candidates = [dict(directory=r["directory"], run_id=r["run_id"],
                       setting=key, seed=r["config"]["seed"])
                  for key, values in groups.items() for r in values]
    train, val = verifier_split()
    write_json(path, dict(
        selection="highest_three_seed_mean_validation_AP_with_lowest_lambda_tie_break",
        choices=choices, validation_ap=means, candidates=candidates,
        selected_runs=[r for r in candidates if r["setting"] in choices.values()],
        train_images=train, val_images=val, sources="all", rule="threshold",
        test_policy="selected settings for classification/e2e; all preregistered settings for cosine diagnostic only"))


def aggregate(values, percent=False):
    values = [v for v in values if v is not None]
    if len(values) != 3:
        return f"待完成（{len(values)}/3）"
    x = np.asarray(values) * (100 if percent else 1)
    digits = 2 if percent else 4
    return f"{x.mean():.{digits}f} ± {x.std(ddof=1):.{digits}f}"


def flatten_classification(metrics, pixels=None):
    result = dict(auc=metrics["auc"], ap=metrics["ap"],
                  fpr80=metrics["recall_80"]["fpr"], fpr90=metrics["recall_90"]["fpr"],
                  pixel_precision=None, f1_half=None)
    if pixels is not None:
        p = pixels["pixel_precision_at_recall_80"]
        result.update(pixel_precision=p["precision"], f1_half=p["f1_half"])
    return result


def load_evaluation(item, dataset):
    path = Path(item["directory"]) / "evaluation" / f"eval_{dataset}.json"
    if not path.exists():
        return None
    result = read(path)
    calibration = read(path.parent / "thresholds_second_cv.json")
    if result["run_id"] != item["run_id"] or calibration["run_id"] != item["run_id"]:
        raise ValueError(f"{path}: evaluation run mismatch")
    if calibration.get("rule", "threshold") != "threshold":
        raise ValueError("6h-1 report requires validation-frozen score thresholds")
    return result


def curves(groups, baseline_runs, out):
    out.mkdir(parents=True, exist_ok=True)
    baselines = []
    for run in baseline_runs:
        path = run / "train_log.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"Missing required 6b reference curve: {path}")
        baselines.append((f"6b {run.name}", [json.loads(x) for x in path.read_text().splitlines() if x.strip()]))
    for setting, items in groups.items():
        fig, axes = plt.subplots(1, 2, figsize=(11, 4))
        for item in items:
            records = [json.loads(x) for x in (Path(item["directory"]) / "train_log.jsonl").read_text().splitlines()
                       if x.strip()]
            for axis, key in zip(axes, ("train_eval", "val")):
                axis.plot([r["epoch"] for r in records], [r[key]["auc"] for r in records],
                          label=f"s{item['config']['seed']}")
        for label, records in baselines:
            for axis, key in zip(axes, ("train_eval", "val")):
                values = [(r.get(key) or {}).get("auc") for r in records]
                if not any(v is not None for v in values):
                    raise ValueError(f"{label}: reference log has no {key} AUC")
                axis.plot([r["epoch"] for r in records],
                          [np.nan if v is None else v for v in values], "--", label=label)
        for axis, title in zip(axes, ("Fixed training subset, eval mode", "Validation")):
            axis.set(xlabel="Epoch", ylabel="AUC", title=title)
            axis.legend(fontsize=8)
        fig.suptitle(setting)
        fig.tight_layout()
        fig.savefig(out / f"h1_{setting}_auc.png", dpi=180)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--freeze_selection", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--diagnostics", type=Path, help="diagnostics.json from exp6h_diag")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary_output", type=Path, help="6h subsection for results/summary.md")
    parser.add_argument("--figures", type=Path)
    parser.add_argument("--budget_file", type=Path, default=ROOT / "variants/h1_gpu_budget.json")
    parser.add_argument("--baseline_runs", type=Path, nargs="+",
                        default=[ROOT / "verifier/curve_runs/dinov3", ROOT / "verifier/curve_runs/none"])
    parser.add_argument("--none_run", type=Path, default=ROOT / "verifier/none")
    args = parser.parse_args()
    groups = group_runs(args.runs)
    if args.freeze_selection:
        freeze(groups, args.freeze_selection)
        print(f"frozen validation-only selection: {args.freeze_selection}", flush=True)
        return
    if args.output is None or args.figures is None:
        parser.error("Reporting requires --output and --figures")
    frozen = read(args.selection) if args.selection else None
    if frozen:
        candidates = {r["directory"]: r["run_id"] for r in frozen["candidates"]}
        for items in groups.values():
            for item in items:
                if candidates.get(item["directory"]) != item["run_id"]:
                    raise ValueError("Report and frozen selection refer to different runs")
    choices = frozen["choices"] if frozen else {}
    curves(groups, args.baseline_runs, args.figures)
    lines = ["# 实验 6h-1：时相约束辅助损失", "",
             "三种子为 0、1、2；表内标准差为样本标准差（ddof=1）。"
             "缺失值标为待完成，不用较少种子代替三种子结论。"
             "λ 按三种子验证 AP 均值选择，种子各自按验证 AP 早停。"
             "全部来源、阈值补漏；仅使用缓存的 256 维 f1/f2 和 6b none 的 11 维元数据。", ""]
    classification, pixels, targets, recall_rows, timings, thresholds = ([] for _ in range(6))
    evals, recall_values = {}, {}
    for setting, items in groups.items():
        for item in items:
            report = item["report"]
            timings.append([setting, item["config"]["seed"], report["best_epoch"],
                            f"{report['seconds'] / 60:.2f}",
                            f"{report['peak_gpu_bytes'] / 2**30:.2f}", report["log"]])
        for dataset in ("second_cv", "second"):
            if dataset == "second" and setting not in choices.values():
                continue
            results = [load_evaluation(item, dataset) for item in items]
            evals[(setting, dataset)] = results
            cls = []
            for item, result in zip(items, results):
                if result:
                    cls.append(flatten_classification(result["recall"]["overall"], result["pixels"]))
                elif dataset == "second_cv":
                    cls.append(flatten_classification(item["report"]["selection_population"]))
            classification.append([setting, dataset,
                *[aggregate([m[field] for m in cls], field not in ("auc", "ap"))
                  for field in ("auc", "ap", "fpr80", "fpr90", "pixel_precision", "f1_half")]])
            complete = [r for r in results if r]
            ps = [r["pixels"]["recall/all"] for r in complete]
            pixels.append([setting, dataset, *[aggregate([p[field] for p in ps], True)
                          for field in ("f1", "precision", "recall", "delta_f1")],
                          *[aggregate([p[field] for p in ps]) for field in ("added_tp", "added_fp")]])
            targets.append([setting, dataset, *[aggregate([p["target_recall"][b]["recall"] for p in ps], True)
                                                for b in BUCKETS]])
            for item, result in zip(items, results):
                if result:
                    thresholds.append([setting, item["config"]["seed"], dataset,
                                       f"{result['applied_thresholds']['recall']['all']:.8g}"])
            reports = []
            for item in items:
                run = Path(item["directory"])
                path = run / "recall" / f"{run.name}_{dataset}.json"
                if path.exists():
                    report = read(path)
                    if report["run_id"] != item["run_id"]:
                        raise ValueError(f"{path}: recall run mismatch")
                    reports.append(report)
            for budget in (0.05, 0.1, 0.2):
                points = [next(p for p in r["combinations"]["all"]["budgets"] if p["budget"] == budget)
                          for r in reports]
                recall_values[(setting, dataset, budget)] = [p["target_recall"]["all"] for p in points]
                recall_rows.append([setting, dataset, f"{budget * 100:g}",
                    aggregate([p["actual_fp_ratio"] for p in points], True),
                    *[aggregate([p["target_recall"][b] for p in points], True) for b in BUCKETS]])
    for title, headers, rows in (
        ("分类", ["设置", "数据集", "AUC", "AP", "FPR@80%", "FPR@90%", "补入像素精度@80%", "F1/2%"], classification),
        ("端到端补漏：全部来源", ["设置", "数据集", "F1%", "P%", "R%", "ΔF1百分点", "新增TP", "新增FP"], pixels),
        ("端到端目标召回", ["设置", "数据集", "全部%", "<100%", "100–999%", "≥1000%"], targets),
        ("召回优先诊断", ["设置", "数据集", "FP预算%", "实际FP%", "全部%", "<100%", "100–999%", "≥1000%"], recall_rows),
        ("验证集冻结阈值", ["设置", "种子", "应用数据集", "阈值"], thresholds),
        ("训练记录", ["设置", "种子", "最佳轮次", "共享作业墙钟分钟", "共享峰值显存GiB", "日志"], timings),
    ):
        lines += [f"## {title}", ""] + table(headers, rows)
    lines += ["召回 80%/90% 与 FP 预算点是当前数据集上的诊断点；端到端使用验证集固定阈值。"
              "F1/2 文档参考为验证 35.7%、测试 37.08%，表中还保留实际检测器结果对应的门槛。"
              "训练种子共享作业时间和显存；不能将种子耗时相加为 GPU 总耗时。", ""]
    if args.budget_file.exists():
        budget = read(args.budget_file)
        lines += [f"累计单 GPU 作业墙钟：{sum(j['seconds'] for j in budget['jobs']) / 3600:.3f} 小时"
                  "（包含特征读取、训练、打分；CPU 评估另计）。", ""]
    for setting in groups:
        lines += [f"![{setting} AUC]({args.figures / ('h1_' + setting + '_auc.png')})", ""]
    summary = ["### 实验 6h-1", ""]
    if not frozen:
        summary += ["尚未冻结最终 λ；当前报告只汇总验证集结果。", ""]
    elif "g1_0" in groups:
        control = groups["g1_0"]
        for mode in ("cos", "sm"):
            setting = choices[mode]
            if setting not in groups:
                raise ValueError(f"Missing selected setting: {setting}")
            summary.append(f"验证 AP 选择 `{setting}`；"
                           f"λ={groups[setting][0]['config']['temporal_lambda']:g}。")
            for field in ("auc", "ap"):
                diffs = [a["report"]["selection_population"][field] - b["report"]["selection_population"][field]
                         for a, b in zip(groups[setting], control)]
                mean, std = float(np.mean(diffs)), float(np.std(diffs, ddof=1))
                summary.append(f"相对 G1-0，验证 {field.upper()} 配对差为 {mean:+.4f} ± {std:.4f}；"
                               f"正增益种子 {sum(d > 0 for d in diffs)}/3，"
                               f"均值{'超过' if mean > std else '未超过'}配对差标准差。")
            tests = [r for r in evals.get((setting, "second"), []) if r]
            if len(tests) == 3:
                auc = np.mean([r["recall"]["overall"]["auc"] for r in tests])
                delta = np.mean([r["pixels"]["recall/all"]["delta_f1"] for r in tests])
                summary.append(f"冻结版本测试 AUC 均值 {auc:.4f}，"
                               f"{'超过' if auc > 0.874 else '未超过'}文档 none 基线 0.874；"
                               f"测试 ΔF1 均值 {delta * 100:+.3f} 个百分点。")
                base_tests = [r for r in evals.get(("g1_0", "second"), []) if r]
                if len(base_tests) == 3:
                    gap = delta - np.mean([r["pixels"]["recall/all"]["delta_f1"] for r in base_tests])
                    summary.append(f"相对 G1-0 的测试 ΔF1 差为 {gap * 100:+.3f} 个百分点；"
                                   "召回优先三个预算及面积桶结果见表。")
                for budget in (0.05, 0.1, 0.2):
                    current = recall_values.get((setting, "second", budget), [])
                    reference = recall_values.get(("g1_0", "second", budget), [])
                    if len(current) == len(reference) == 3 and all(v is not None for v in current + reference):
                        gap = np.mean(current) - np.mean(reference)
                        summary.append(f"测试 FP 预算 {budget * 100:g}% 时，目标召回相对 G1-0 "
                                       f"{gap * 100:+.3f} 个百分点。")
            else:
                summary.append("测试端到端结果待完成，尚不能判断测试收益。")
        summary += ["配对差标准差用于描述种子波动，不构成显著性检验；"
                    "G1-sm 同时改变投影维度和余弦输入，与 G1-0 的差值不能单独归因于 L_t。", ""]
    if args.diagnostics:
        diagnostics = read(args.diagnostics)
        lines += ["## 两期相似度", ""] + table(
            ["设置/种子", "数据集", "AUC(cos)", "AUC(1−cos)", "群体"],
            [[r["name"], r["dataset"], number(r["cos_auc"]), number(r["dissimilarity_auc"]),
              r["population"]] for r in diagnostics["rows"]])
        lines += diagnostics["missing"] + [""]
        for dataset in ("second_cv", "second"):
            raw = [r for r in diagnostics["rows"] if r["setting"] == "raw_dinov3_256" and r["dataset"] == dataset]
            for mode, setting in choices.items():
                if mode == "control":
                    continue
                rows = [r for r in diagnostics["rows"] if r["setting"] == setting and r["dataset"] == dataset]
                if len(rows) == 3 and raw:
                    mean = np.mean([r["dissimilarity_auc"] for r in rows])
                    summary.append(f"`{setting}` / {dataset}：cos AUC "
                                   f"{aggregate([r['cos_auc'] for r in rows])}，固定 1−cos AUC "
                                   f"{aggregate([r['dissimilarity_auc'] for r in rows])}；"
                                   f"相对原始 256 维特征的 1−cos AUC 差 "
                                   f"{mean - raw[0]['dissimilarity_auc']:+.4f}。")
        summary += ["RemoteCLIP 的样本群体与 6h 不同，历史值仅供参照；不能据此声称同群体提升。", ""]
    else:
        summary += ["余弦诊断尚未提供，暂不回答相似度信号是否改善。", ""]
    none_path = args.none_run / "eval_second.json"
    if frozen and none_path.exists():
        base = read(none_path)
        summary.append(f"现有 6b none 测试 AUC={base['recall']['overall']['auc']:.4f}，"
                       f"全部来源 ΔF1={base['pixels']['recall/all']['delta_f1'] * 100:+.3f} 个百分点。")
    summary += ["6h-2 未执行，等待 6f-C 基线后的独立任务。", ""]
    lines += ["## 判断问题", ""] + summary[2:]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text("\n".join(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
