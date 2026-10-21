"""6h-2 validation freeze and report; exp5-env, CPU, request 2 CPUs / 2 GiB.

Estimated <2 min. Reuses 6h report formatting and 6f-compatible evaluation JSON.
Reads no model or image data. Missing evaluation evidence is printed explicitly.
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

from exp6b_common import ROOT, verifier_split
from exp6b_report import number, table
from exp6h_report import BUCKETS, aggregate, flatten_classification, read

SEEDS = [1020, 1021, 1022]
WEIGHTS = (0, 0.1, 0.3, 1)


def group_runs(runs):
    groups = defaultdict(list)
    for run in runs:
        config, report = read(run / "config.json"), read(run / "val_metrics.json")
        if (report["smoke"] or config["run_id"] != report["run_id"]
                or config["seed"] != report["seed"]
                or config["temporal_lambda"] != report["temporal_lambda"]):
            raise ValueError(f"{run}: incomplete or inconsistent training")
        key = f"h2_l{config['temporal_lambda']:g}"
        groups[key].append(dict(directory=str(run.resolve()), run_id=report["run_id"],
                                config=config, report=report, seed=config["seed"], setting=key))
    if set(groups) != {f"h2_l{v:g}" for v in WEIGHTS}:
        raise ValueError("Need all four lambdas")
    for key, items in groups.items():
        items.sort(key=lambda r: r["seed"])
        if [r["seed"] for r in items] != SEEDS:
            raise ValueError(f"{key}: need exactly seeds {SEEDS}")
    return dict(groups)


def freeze(groups, path):
    means = {key: float(np.mean([r["report"]["full_validation"]["ap"] for r in items]))
             for key, items in groups.items()}
    eligible = [key for key in groups if groups[key][0]["config"]["temporal_lambda"] > 0]
    best = min(eligible, key=lambda k: (-means[k], groups[k][0]["config"]["temporal_lambda"]))
    candidates = [{k: r[k] for k in ("directory", "run_id", "seed", "setting")}
                  for items in groups.values() for r in items]
    train, val = verifier_split()
    result = dict(selection="highest_three_seed_mean_full_validation_AP_among_positive_lambdas; ties choose lower lambda",
                  choices=dict(control="h2_l0", best=best), validation_ap=means,
                  candidates=candidates, selected_runs=[r for r in candidates if r["setting"] in ("h2_l0", best)],
                  train_images=train, val_images=val, sources="all", rule="threshold")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def plot_training(groups, figures):
    figures.mkdir(parents=True, exist_ok=True)
    for key, items in groups.items():
        fig, axes = plt.subplots(2, 3, figsize=(15, 8))
        for item in items:
            run = Path(item["directory"])
            records = [json.loads(line) for line in (run / "train_log.jsonl").read_text().splitlines() if line.strip()]
            step = [r for r in records if r["event"] == "step"]
            epoch = [r for r in records if r["event"] == "epoch_end"]
            label = f"s{item['seed']}"
            x = [r["epoch"] for r in step]
            axes[0, 0].plot(x, [r["train_bce"] for r in step], label=label)
            axes[0, 1].plot(x, [r["train_lt"] for r in step], label=label)
            axes[0, 2].plot(x, [r["val"]["loss"] for r in step], label=label)
            axes[1, 0].plot(x, [r["train_eval"]["auc"] for r in step], label=label + " train")
            axes[1, 0].plot(x, [r["val"]["auc"] for r in step], "--", label=label + " val monitor")
            axes[1, 1].plot(x, [r["val"]["ap"] for r in step], label=label + " monitor")
            axes[1, 1].plot([r["epoch"] for r in epoch], [r["val"]["ap"] for r in epoch],
                            "o--", label=label + " full")
            axes[1, 2].plot(x, [r["val"]["dissimilarity_auc"] for r in step], label=label)
        titles = ("Train weighted BCE", "Train temporal loss (pixel mean)", "Validation BCE",
                  "AUC, eval mode", "Validation AP (separate populations)", "Validation AUC(1-cos)")
        for axis, title in zip(axes.flat, titles):
            axis.set(title=title, xlabel="Epoch")
            axis.legend(fontsize=6)
        fig.tight_layout()
        fig.savefig(figures / f"{key}_training.png", dpi=150)
        for item in items:
            out = Path(item["directory"]) / "curves"
            out.mkdir(exist_ok=True)
            fig.savefig(out / "temporal_training.png", dpi=150)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(7, 4))
        for label in ("unchanged", "changed"):
            hist = [r["report"]["full_validation"]["cosine_histogram"] for r in items]
            counts = np.sum([h[label] for h in hist], axis=0)
            edges = np.asarray(hist[0]["edges"])
            ax.stairs(counts / counts.sum(), edges, label=label)
        ax.set(xlabel="Mean pre-pool spatial cosine", ylabel="Fraction", title=f"{key}, full validation, 3 seeds")
        ax.legend()
        fig.tight_layout()
        fig.savefig(figures / f"{key}_cosine.png", dpi=150)
        plt.close(fig)


def report_metrics(entries, datasets, lines, missing):
    classifications, pixels, targets, recalls, thresholds = ([] for _ in range(5))
    collected, recall_values = {}, {}
    for name, items, single, allowed_test in entries:
        fmt = (lambda values, percent=False: number(values[0], percent) if len(values) == 1 else "待完成") if single else aggregate
        for dataset in datasets:
            if dataset == "second" and not allowed_test:
                continue
            cls, ps, rs = [], [], []
            results = []
            for item in items:
                run = Path(item["directory"])
                path = run / item.get("eval_subdir", "evaluation") / f"eval_{dataset}.json"
                if path.exists():
                    result = read(path)
                    if result["run_id"] != item["run_id"]:
                        raise ValueError(f"{path}: wrong run_id")
                    results.append(result)
                    cls.append(flatten_classification(result["recall"]["overall"], result["pixels"]))
                    ps.append(result["pixels"]["recall/all"])
                    thresholds.append([name, item.get("seed", "参考"), dataset,
                                       result["applied_thresholds"]["recall"]["all"]])
                else:
                    missing.append(str(path))
                reference = item.get("reference_recall")
                path = (Path(reference) / f"{item['reference_name']}_{dataset}.json" if reference
                        else run / "recall" / f"{run.name}_{dataset}.json")
                if path.exists():
                    result = read(path)
                    if reference:
                        # Historical 6f summaries identify model/dataset, without run_id.
                        if result["model"] != item["reference_name"] or result["dataset"] != dataset:
                            raise ValueError(f"{path}: wrong historical reference")
                    elif result["run_id"] != item["run_id"]:
                        raise ValueError(f"{path}: wrong run_id")
                    rs.append(result)
                else:
                    missing.append(str(path))
            collected[name, dataset] = results
            classifications.append([name, dataset, *[fmt([r[k] for r in cls], k not in ("auc", "ap"))
                for k in ("auc", "ap", "fpr80", "fpr90", "pixel_precision", "f1_half")]])
            pixels.append([name, dataset, *[fmt([r[k] for r in ps], True) for k in ("f1", "precision", "recall", "delta_f1")],
                           *[fmt([r[k] for r in ps]) for k in ("added_tp", "added_fp")]])
            targets.append([name, dataset, *[fmt([r["target_recall"][k]["recall"] for r in ps], True) for k in BUCKETS]])
            for budget in (0.05, 0.1, 0.2):
                points = [next(p for p in r["combinations"]["all"]["budgets"] if p["budget"] == budget) for r in rs]
                recall_values[name, dataset, budget] = [p["target_recall"]["all"] for p in points]
                recalls.append([name, dataset, budget, fmt([p["actual_fp_ratio"] for p in points], True),
                                *[fmt([p["target_recall"][k] for p in points], True) for k in BUCKETS]])
    for title, headers, rows in (
        ("分类", ["设置", "数据集", "AUC", "AP", "FPR@80%", "FPR@90%", "补入像素精度@80%", "F1/2%"], classifications),
        ("端到端全部来源", ["设置", "数据集", "F1%", "P%", "R%", "ΔF1百分点", "新增TP", "新增FP"], pixels),
        ("目标召回", ["设置", "数据集", "全部%", "<100%", "100–999%", "≥1000%"], targets),
        ("召回优先诊断", ["设置", "数据集", "FP预算", "实际FP%", "全部%", "<100%", "100–999%", "≥1000%"], recalls),
        ("验证冻结阈值", ["设置", "种子", "数据集", "阈值"], thresholds)):
        lines += [f"### {title}", ""] + table(headers, rows)
    return collected, recall_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--freeze_selection", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--h1_selection", type=Path, default=ROOT / "variants/h1_selection.json")
    parser.add_argument("--siamese_run", type=Path, default=ROOT / "variants/c/siamese_raw")
    parser.add_argument("--none_run", type=Path, default=ROOT / "verifier/none")
    parser.add_argument("--reference_recall", type=Path, default=ROOT / "variants/recall_priority")
    parser.add_argument("--budget_file", type=Path, default=ROOT / "variants/h2_gpu_budget.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--figures", type=Path)
    args = parser.parse_args()
    groups = group_runs(args.runs)
    if args.freeze_selection:
        freeze(groups, args.freeze_selection)
        print(f"Frozen: {args.freeze_selection}", flush=True)
        return
    if args.output is None or args.figures is None:
        parser.error("Report needs --output and --figures")
    frozen = read(args.selection) if args.selection else None
    if frozen:
        expected = {r["directory"]: r["run_id"] for r in frozen["candidates"]}
        actual = {r["directory"]: r["run_id"] for items in groups.values() for r in items}
        if actual != expected:
            raise ValueError("Frozen selection and reported runs differ")
    plot_training(groups, args.figures)
    lines = ["## 6h-2：原分辨率孪生网络与时相约束", "",
        "三种子 1020、1021、1022；均值 ± 样本标准差。每个种子的初始化、数据打乱、增强和 dropout 独立。"
        "λ=0 同代码路径重训。输入、优化器、增强、早停和超参保持 siamese_raw 设置。",
        "L_t 使用池化前 8×8 特征，包含原有末层 Dropout2d；64×64 真值未变化掩码先同步最近邻增强，"
        "再面积平均，值为 1 的格点参与损失。所有训练块均参与，按有效格点总数平均；没有格点时为零。",
        "单模型检查点按固定验证子集 AP 早停，λ 按恢复最佳模型后的完整验证集 AP 三种子均值选择，平局取较小 λ。"
        "沿用 6h-1，在正 λ 中选择并保留 λ=0 对照。验证图真值不进入 L_t。"
        "余弦诊断固定使用逐位置余弦的空间均值，变化块为正类。", ""]
    val_rows, timings = [], []
    for key, items in groups.items():
        val_rows.append([key, *[aggregate([r["report"]["full_validation"][k] for r in items])
                                for k in ("auc", "ap", "cos_auc", "dissimilarity_auc")]])
        for item in items:
            report = item["report"]
            timings.append([key, item["seed"], report["best_epoch"], report["best_step"],
                            f"{report['seconds'] / 60:.2f}", f"{report['peak_gpu_gib']:.2f}",
                            str(Path(item["directory"]) / "train_log.jsonl")])
    lines += table(["设置", "完整验证AUC", "完整验证AP", "AUC(cos)", "AUC(1−cos)"], val_rows)
    missing, entries = [], []
    chosen = set(frozen["choices"].values()) if frozen else set()
    for key, items in groups.items():
        entries.append((key, items, False, key in chosen))
    h1 = read(args.h1_selection)
    h1_groups = defaultdict(list)
    for item in h1["selected_runs"]:
        h1_groups[item["setting"]].append(item)
    for key, items in h1_groups.items():
        entries.append(("6h-1 " + key, items, False, True))
    for label, run, reference_name in (("6f-C siamese_raw（单种子参考）", args.siamese_run, "c_siamese"),
                                      ("6b none（参考）", args.none_run, "none")):
        report = read(run / "val_metrics.json")
        entries.append((label, [dict(directory=str(run), run_id=report["run_id"], eval_subdir="",
                                    reference_recall=str(args.reference_recall), reference_name=reference_name)], True, True))
    results, recall_values = report_metrics(entries, ("second_cv", "second") if frozen else ("second_cv",), lines, missing)
    lines += ["F1/2 文档参考：验证 35.7%，测试 37.08%；表格保留实际评估门槛。"
              "测试召回点及 FP 预算曲线只作诊断；端到端阈值始终来自验证集。"
              "siamese_raw 与 none 的召回优先结果复用历史 recall_priority 文件，以 model/dataset 标识，原文件没有 run_id。", ""]
    lines += ["### 训练记录", ""] + table(["设置", "种子", "最佳轮次", "最佳步数", "分钟", "显存GiB", "日志"], timings)
    if args.budget_file.exists():
        budget = read(args.budget_file)
        lines += [f"累计单 GPU 作业时间：{sum(j['seconds'] for j in budget['jobs']) / 3600:.3f} 小时（包含失败作业）；"
                  "强制终止的作业需先按 sacct 补齐。", ""]
    lines += ["### 判断问题", ""]
    control = groups["h2_l0"]
    for key, items in groups.items():
        if key == "h2_l0":
            continue
        for metric in ("auc", "ap", "dissimilarity_auc"):
            diffs = [r["report"]["full_validation"][metric] - c["report"]["full_validation"][metric]
                     for r, c in zip(items, control)]
            mean, std = np.mean(diffs), np.std(diffs, ddof=1)
            lines.append(f"{key} 相对 λ=0：验证 {metric} 配对差 {mean:+.4f} ± {std:.4f}，"
                         f"{sum(d > 0 for d in diffs)}/3 种子提高；均值{'超过' if mean > std else '未超过'}配对差标准差。")
    if frozen:
        best = frozen["choices"]["best"]
        lines += [f"验证选择：{best}。", ""]
        tests, bases = results.get((best, "second"), []), results.get(("h2_l0", "second"), [])
        if len(tests) == len(bases) == 3:
            auc = np.mean([r["recall"]["overall"]["auc"] for r in tests])
            gap = np.mean([r["pixels"]["recall/all"]["delta_f1"] for r in tests]) - np.mean(
                [r["pixels"]["recall/all"]["delta_f1"] for r in bases])
            lines.append(f"选定设置测试 AUC={auc:.4f}，{'超过' if auc > 0.874 else '未超过'}文档 none 参考 0.874；"
                         f"相对重训 λ=0 的 ΔF1 差 {gap * 100:+.3f} 个百分点。")
        else:
            lines.append("测试结果尚未齐全，不能判断测试收益。")
        for budget in (0.05, 0.1, 0.2):
            current = recall_values.get((best, "second", budget), [])
            reference = recall_values.get(("h2_l0", "second", budget), [])
            if len(current) == len(reference) == 3 and all(v is not None for v in current + reference):
                gap = np.mean(current) - np.mean(reference)
                lines.append(f"测试 FP 预算 {budget * 100:g}% 时，目标召回相对 λ=0 差 {gap * 100:+.3f} 个百分点。")
        for mode in ("cos", "sm"):
            reference = h1_groups[h1["choices"][mode]]
            reference_control = h1_groups[h1["choices"]["control"]]
            old_gap = np.mean([read(Path(r["directory"]) / "val_metrics.json")["selection_population"]["ap"]
                               for r in reference]) - np.mean(
                [read(Path(r["directory"]) / "val_metrics.json")["selection_population"]["ap"] for r in reference_control])
            new_gap = frozen["validation_ap"][best] - frozen["validation_ap"]["h2_l0"]
            lines.append(f"6h-1 {mode} 验证 AP 增益 {old_gap:+.4f}，6h-2 选定设置增益 {new_gap:+.4f}；"
                         f"两者{'均为正' if old_gap > 0 and new_gap > 0 else '未同时呈现正增益'}。")
    else:
        lines.append("尚未冻结 λ；当前只报告验证数据。")
    lines += ["种子标准差描述波动，不构成显著性检验。6h-1 与 6h-2 的输入、结构和种子编号不同，跨实验差值仅供参考。"
              "RemoteCLIP 历史 AUC 0.51–0.67 的候选群体不同；本次空间余弦诊断仅覆盖验证集。", ""]
    for key in groups:
        lines += [f"![{key} 训练曲线]({args.figures / (key + '_training.png')})", "",
                  f"![{key} 余弦分布]({args.figures / (key + '_cosine.png')})", ""]
    if missing:
        lines += ["### 未完成证据", "", *[f"- `{path}`" for path in dict.fromkeys(missing)], ""]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
