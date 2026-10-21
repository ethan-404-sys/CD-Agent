"""6h-1 raw cosine diagnostics; exp5-env, CPU, 4 CPUs / 16 GiB host RAM.

Reads 7.13/4.54 GiB feature archives one shard at a time; ~5–30 min/pool,
unmeasured. No backbone inference. Test diagnostics require frozen selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from common import OUT
from exp6b_common import Pool, SOURCES, binary_metrics, verifier_split, write_json
from exp6b_report import number, table
from exp6h_common import feature_shards, load_cos


def cosine_metrics(labels, cosine):
    return dict(cos_auc=binary_metrics(labels, cosine)["auc"],
                dissimilarity_auc=binary_metrics(labels, 1 - cosine)["auc"])


def histogram(labels, values, title, path):
    fig, ax = plt.subplots(figsize=(6, 4))
    for y, label in ((0, "Unchanged (negative)"), (1, "Changed (positive)")):
        ax.hist(values[labels == y], bins=np.linspace(-1, 1, 101), density=True,
                histtype="step", label=label)
    ax.set(xlabel="Raw cos(t1, t2)", ylabel="Density", title=title)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def raw_cosine(pool, selected):
    scores = np.full(len(pool), np.nan, np.float32)
    for idx, pair in feature_shards(pool):
        local = np.flatnonzero(selected[idx])
        for start in range(0, len(local), 32768):
            take = local[start:start + 32768]
            f1, f2 = pair[take].astype(np.float32).transpose(1, 0, 2)
            denom = np.linalg.norm(f1, axis=1) * np.linalg.norm(f2, axis=1)
            scores[idx[take]] = np.clip((f1 * f2).sum(1) / np.maximum(denom, 1e-12), -1, 1)
    if not np.isfinite(scores[selected]).all():
        raise ValueError("Raw cosine does not cover evaluation pieces")
    return scores


def remoteclip_rows(paths, dataset):
    rows = []
    for path in paths:
        report = json.loads(path.read_text())
        for signal in ("clip_dissim_ctx", "clip_dissim_tight"):
            value = report["signals"][signal]["auc"]
            rows.append(dict(
                name=f"exp5 RemoteCLIP {path.parent.parent.name}/{signal}",
                dataset=dataset, n=report["n"], cos_auc=1 - value, dissimilarity_auc=value,
                population="exp5 sampled candidates, not the exp6 pool; historical reference only",
                source=str(path), setting="remoteclip", seed=None))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--datasets", nargs="+", choices=("second_cv", "second"), default=["second_cv"])
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--remoteclip_results", type=Path, nargs="*",
                        help="Existing exp5 analysis_5b.json files for SECOND test")
    args = parser.parse_args()
    if "second" in args.datasets and args.selection is None:
        parser.error("Test diagnostics require a frozen --selection")
    frozen = json.loads(args.selection.read_text()) if args.selection else None
    if frozen:
        candidates = {str(Path(r["directory"]).resolve()): r["run_id"] for r in frozen["candidates"]}
        for run in args.runs:
            trained = json.loads((run / "val_metrics.json").read_text())
            if candidates.get(str(run.resolve())) != trained["run_id"]:
                raise ValueError(f"{run}: not registered before test evaluation")
    args.out.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for dataset in args.datasets:
        pool = Pool(dataset)
        selected = (pool.label >= 0) & (pool.source != SOURCES.index("pred"))
        if dataset == "second_cv":
            selected &= pool.image_mask(verifier_split()[1])
        labels = pool.label[selected]
        entries = [("raw_dinov3_256", None)] + [(run.name, run) for run in args.runs]
        for name, run in entries:
            result_path = args.out / f"{name}_{dataset}.json"
            training = json.loads((run / "val_metrics.json").read_text()) if run else None
            run_id = training["run_id"] if run else "cached_dinov3_256"
            if result_path.exists():
                result = json.loads(result_path.read_text())
                if result["run_id"] != run_id:
                    raise ValueError(f"{result_path}: different model run")
            else:
                cosine = (load_cos(run / "cos", pool, run_id, selected) if run
                          else raw_cosine(pool, selected))
                result = dict(name=name, dataset=dataset, run_id=run_id, n=int(selected.sum()),
                              setting=training["config"]["setting"] if run else "raw_dinov3_256",
                              seed=training["config"]["seed"] if run else None,
                              population="labeled_recall_pieces", **cosine_metrics(labels, cosine[selected]))
                histogram(labels, cosine[selected], f"{name} / {dataset}",
                          args.out / f"{name}_{dataset}.png")
                write_json(result_path, result)
            rows.append(result)
        if dataset == "second":
            paths = args.remoteclip_results
            if paths is None:
                paths = [OUT / sub / "second/analysis_5b.json" for sub in ("exp4b", "exp5b")]
                paths = [path for path in paths if path.exists()]
            rows.extend(remoteclip_rows(paths, dataset))
            if not paths:
                missing.append("RemoteCLIP SECOND 历史结果未找到；由 Claude 按原始结果手填来源和数值。")
        else:
            missing.append("RemoteCLIP 无同一 297 图验证集的已确认结果；由 Claude 查证后手填，不能移用测试值。")
    write_json(args.out / "diagnostics.json", dict(rows=rows, missing=missing,
               direction="cos_auc uses raw similarity; dissimilarity_auc uses fixed 1-cos; positive=change"))
    lines = ["# 6h-1 两期余弦诊断", "",
             "正例为真变化。原始 cos 与固定方向 1−cos 均报告；不按数据挑方向。"
             "原始 DINOv3 对照是已有固定高斯投影后的 256 维 f1/f2，未做训练集标准化。"
             "G1-0 为未加 L_t 的投影头对照。RemoteCLIP 是不同候选群体的历史参照。", ""]
    lines += table(["模型", "数据集", "n", "AUC(cos)", "AUC(1−cos)", "来源/群体"], [
        [r["name"], r["dataset"], r["n"], number(r["cos_auc"]), number(r["dissimilarity_auc"]),
         r.get("source", r["population"])] for r in rows])
    lines += missing
    (args.out / "diagnostics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
