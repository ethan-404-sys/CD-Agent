"""Chinese metrics tables and test_987 figures; exp5-env, CPU, no model inference.

Reads a completed eval_metrics_run output. Writes <doc-root>/results/eval_metrics.md
and <doc-root>/figures/eval_metrics/. Estimate: 1 CPU, 2 GB, <5 min (unmeasured).
The six_numbers interface is re-exported for downstream pipeline pages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np
from scipy import ndimage

from common import OUT
from metrics_cd import BUCKETS, boundary, six_numbers

EXAMPLE_ROWS = ("1", "4_BGH1", "4a_BGH1", "4b_BGH1", "4c_BGH1")
EXAMPLE_LABELS = ("1 Detector", "4 Oracle B+G+H1", "4a Soft boundary", "4b Opening + closing", "4c SAM3 P2 t2")
ZOOM_RULE = (
    "在 4_BGH1 边界上，取距 4 与 4b 掩码差异不超过半径 2 圆盘、"
    "且不在原检测器边界半径 2 圆盘内的像素作为台阶代理；"
    "逐像素枚举完整 96×96 窗口，选代理像素最多的窗口。"
    "并列按上到下、左到右取首个；所有窗口为零时也按此规则取首个，并报告零分。"
    "五行使用同一窗口，以最近邻放大，不使用真值选窗。"
)


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def fmt(value, percent=False):
    if value is None:
        return "—"
    if percent:
        return f"{100 * value:.4f}"
    if isinstance(value, int):
        return str(value)
    return f"{value:.4f}"


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |",
            *["| " + " | ".join(str(x) for x in row) + " |" for row in rows], ""]


def zoom_box(base, oracle, smoothed):
    disk = np.indices((5, 5)) - 2
    disk = (disk ** 2).sum(0) <= 4
    changed = ndimage.binary_dilation(oracle != smoothed, disk)
    base_edge = ndimage.binary_dilation(boundary(base), disk)
    proxy = boundary(oracle) & changed & ~base_edge
    side = min(96, *base.shape)
    integral = np.pad(proxy.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    scores = (integral[side:, side:] - integral[:-side, side:]
              - integral[side:, :-side] + integral[:-side, :-side])
    y, x = np.unravel_index(int(scores.argmax()), scores.shape)
    return [int(x), int(y), int(x + side), int(y + side)], int(scores[y, x])


def overlay(rgb, pred, gt):
    canvas = (rgb.astype(np.float32) * 0.65).astype(np.uint8)
    pb, gb = boundary(pred), boundary(gt)
    canvas[gb] = (255, 220, 0)
    canvas[pb] = (0, 230, 255)
    canvas[pb & gb] = (255, 255, 255)
    return canvas


def render(source, destination, summary):
    with np.load(source / "test_987.npz", allow_pickle=False) as z:
        gt, before, t2 = z["gt"], z["before"], z["t2"]
        masks = {key: z[f"mask_{key}"] for key in EXAMPLE_ROWS}
    box, score = zoom_box(before, masks["4_BGH1"], masks["4b_BGH1"])
    x0, y0, x1, y1 = box
    selection = np.s_[y0:y1, x0:x1]
    panels = {key: overlay(t2, mask, gt) for key, mask in masks.items()}
    for kind in ("masks", "boundaries"):
        figure, axes = plt.subplots(1, len(EXAMPLE_ROWS), figsize=(20, 4.5))
        for axis, key, label in zip(axes, EXAMPLE_ROWS, EXAMPLE_LABELS):
            axis.imshow(masks[key] if kind == "masks" else panels[key],
                        cmap="gray", vmin=0, vmax=1 if kind == "masks" else 255, interpolation="nearest")
            axis.add_patch(plt.Rectangle((x0, y0), x1-x0, y1-y0, fill=False,
                                         edgecolor="magenta", linewidth=1))
            axis.set_title(label)
            axis.axis("off")
        if kind == "boundaries":
            figure.suptitle("Prediction: cyan | GT: yellow | coincident boundary: white")
        figure.tight_layout()
        figure.savefig(destination / f"test_987_{kind}.png", dpi=160)
        plt.close(figure)
    figure, axes = plt.subplots(2, len(EXAMPLE_ROWS), figsize=(18, 7))
    for j, (key, label) in enumerate(zip(EXAMPLE_ROWS, EXAMPLE_LABELS)):
        axes[0, j].imshow(masks[key][selection], cmap="gray", vmin=0, vmax=1, interpolation="nearest")
        axes[1, j].imshow(panels[key][selection], interpolation="nearest")
        axes[0, j].set_title(label)
        axes[0, j].axis("off")
        axes[1, j].axis("off")
    figure.tight_layout()
    figure.savefig(destination / "test_987_zoom.png", dpi=160)
    plt.close(figure)
    numbers = {key: six_numbers(mask, gt, before,
                               dataset_decline_fraction=summary["rows"][key]["delta_f1"]["decline_fraction"])
               for key, mask in masks.items()}
    data = dict(image="test_987.png", rows=list(EXAMPLE_ROWS), zoom_xyxy=box, zoom_score=score,
                zoom_rule=ZOOM_RULE, six_numbers=numbers,
                decline_fraction_scope="SECOND test all 1694 images, matching row")
    dump(destination / "test_987.json", data)
    return data


def report(summary, checks, example):
    rows, order, config = summary["rows"], summary["row_order"], summary["config"]
    lines = ["# SECOND test 统一评价指标", "",
             f"共 {config['images']} 张图；比例显示为百分数，ΔF1 显示为百分点。"
             "A–C 按像素、边界计数或目标数汇总；D 的总体粗糙度为周长和与凸包周长和之比；"
             "分布表按图等权。所有变化均相对第 1 行。", "",
             "## 六个数", ""]
    lines += table(["行", "像素 F1 %", "IoU %", "BF(d=2) %", "目标 F1 %", "新增误检目标/图", "下降图 %"],
                   [[r, *[fmt(rows[r]["micro"][k], True) for k in ("pixel_f1", "pixel_iou", "bf2_f1", "object_f1")],
                     fmt(rows[r]["distributions"]["new_false_objects"]["mean"]),
                     fmt(rows[r]["delta_f1"]["decline_fraction"], True)] for r in order])
    lines += ["## 行定义与复算核对", ""]
    lines += table(["行", "设定", "历史参考核对"],
                   [[r, rows[r]["title"], "TP/FP/FN 一致，F1 通过" if checks["rows"][r]["status"] == "matched"
                     else "新边缘变体；无历史值；两次逐图计数一致"] for r in order])
    lines += [f"V1 阈值为 {config['v1_threshold']:.16g}；A2 预算 k={config['a2_k']}，"
              f"空预测图的预算基数为测试集平均预测面积 {config['a2_empty_prediction_base']}。",
              f"精确 JSON 的 F1 容差为 {config['pixel_f1_tolerance']}（0–1），整数计数要求完全一致；"
              f"既有报告两位小数的容差为 {config['rounded_report_tolerance_pp']} 个百分点。",
              "第 4 行参考由已核对的补漏、去误检、原始计数推导，"
              "合并为 cleaned ∪ (accepted ∖ original)，不恢复删除的原预测像素。",
              f"保存掩码与重新读取概率 >0.5 相差 {checks['saved_vs_prob_pixels']} 像素；"
              "本表沿用历史 6a/6b/6e 的保存掩码。", ""]
    for row, reason in config["skipped"].items():
        lines += [f"第 {row} 行跳过：{reason}", ""]
    base, both = rows["1"]["micro"], rows["4_BGH1"]["micro"]
    lines += ["## A 像素级", ""]
    lines += ['同一micro汇总下 F1=2×IoU/(1+IoU)，两者单调等价，IoU不提供新增形状约束。', '']
    lines += table(["行", "TP", "FP", "FN", "精度 %", "召回 %", "F1 %", "IoU %"],
                   [[r, *[rows[r]["counts"][k] for k in ("tp", "fp", "fn")],
                     *[fmt(rows[r]["micro"][k], True) for k in
                       ("pixel_precision", "pixel_recall", "pixel_f1", "pixel_iou")]] for r in order])
    lines += [f"第 1 / 4_BGH1 行的像素 F1 为 {fmt(base['pixel_f1'], True)} / "
              f"{fmt(both['pixel_f1'], True)}%，IoU 为 {fmt(base['pixel_iou'], True)} / {fmt(both['pixel_iou'], True)}%。", "",
              "## B 边界级", ""]
    boundary_keys = tuple(f'bf{d}_{k}' for d in (1, 2, 4, 5) for k in ('precision', 'recall', 'f1')) + ('biou_diag', 'biou_3')
    lines += table(['行', *[f'BF{d} {k} %' for d in (1, 2, 4, 5) for k in ('P', 'R', 'F1')], 'BIoU 2%对角线 %', 'BIoU d=3 %'],
                   [[r, *[fmt(rows[r]["micro"][k], True) for k in boundary_keys]] for r in order])
    lines += [f"第 1 / 4_BGH1 行的 BF2 为 {fmt(base['bf2_f1'], True)} / {fmt(both['bf2_f1'], True)}%，"
              f"BF5 为 {fmt(base['bf5_f1'], True)} / {fmt(both['bf5_f1'], True)}%。", "",
              "## C 目标级", "",
              "预测与真值均使用二值图 8 连通域，不按语义转移拆分；覆盖/重叠至少 50% 即命中，"
              "两侧分别计数，不要求一一匹配。面积桶端点为 <100、[100,1000]、(1000,5000]、>5000。", ""]
    lines += table(["行", "预测目标", "正确预测目标", "真值目标", "命中真值目标", "精度 %", "召回 %", "F1 %",
                    "<100 R %", "100–1000 R %", "1000–5000 R %", ">5000 R %"],
                   [[r, *[rows[r]["counts"][k] for k in ("pred_objects", "pred_objects_correct", "gt_objects", "gt_objects_hit")],
                     *[fmt(rows[r]["micro"][k], True) for k in ("object_precision", "object_recall", "object_f1")],
                     *[fmt(rows[r]["micro"][f"object_recall_{b}"], True) for b in BUCKETS]] for r in order])
    lines += table(["行", "校正前纯误检目标总数", "校正后纯误检目标总数", "新增误检/图均值", "新增误检/图 P90"],
                   [[r, rows[r]["counts"]["before_false_objects"], rows[r]["counts"]["pred_false_objects"],
                     fmt(rows[r]["distributions"]["new_false_objects"]["mean"]),
                     fmt(rows[r]["distributions"]["new_false_objects"]["p90"])] for r in order])
    lines += table(['行', '拆分率 %', '粘连率(≥1像素) %', '粘连率(每个真值覆盖≥50%) %'],
                   [[r, *[fmt(rows[r]['micro'][k], True) for k in ('split_rate', 'merge_rate', 'merge_rate_50')]] for r in order])
    lines += ['拆分分母为全部真值目标；与其有≥1像素重叠的预测连通域至少2个即计入。'
              '粘连分母为全部预测目标；与至少2个真值目标各重叠≥1像素即计入；另报逐个真值覆盖≥50%的版本。', '']
    lines += ["新增误检数取每图 max(0, 校正后零真值重叠连通域数 − 校正前同类连通域数)，"
              "再求均值和 P90；此值是数量净增加，删除造成的碎裂也可能计入。",
              f"第 1 / 4_BGH1 行的目标 F1 为 {fmt(base['object_f1'], True)} / {fmt(both['object_f1'], True)}%。", "",
              "## D 形状质量", ""]
    lines += table(["行", "预测周长和", "预测凸包周长和", "预测粗糙度", "真值周长和", "真值凸包周长和", "真值粗糙度",
                    "预测连通域/图均值", "预测连通域/图 P90", "<20碎片/图均值", "<20碎片/图 P90"],
                   [[r, fmt(rows[r]["counts"]["pred_perimeter"]), fmt(rows[r]["counts"]["pred_hull_perimeter"]),
                     fmt(rows[r]["micro"]["pred_roughness"]), fmt(rows[r]["counts"]["gt_perimeter"]),
                     fmt(rows[r]["counts"]["gt_hull_perimeter"]), fmt(rows[r]["micro"]["gt_roughness"]),
                     *[fmt(rows[r]["distributions"][k][q]) for k in ("pred_fragments", "pred_fragments_lt20")
                       for q in ("mean", "p90")]] for r in order])
    lines += [f"第 1 / 4_BGH1 行的预测总体粗糙度为 {fmt(base['pred_roughness'])} / {fmt(both['pred_roughness'])}，"
              f"真值为 {fmt(base['gt_roughness'])}。", "",
              "## E 逐图变化", ""]
    lines += table(["行", "提升图 %", "下降图 %", "不变图 %", "ΔF1 P10", "ΔF1 P50", "ΔF1 P90", "最差五图（百分点）"],
                   [[r, *[fmt(rows[r]["delta_f1"][k], True) for k in
                          ("improve_fraction", "decline_fraction", "unchanged_fraction", "p10", "p50", "p90")],
                     "; ".join(f"{x['image']}: {fmt(x['delta_f1'], True)}" for x in rows[r]["delta_f1"]["worst5"])] for r in order])
    lines += [f"4_BGH1 的提升 / 下降 / 不变图占比为 "
              + " / ".join(fmt(rows["4_BGH1"]["delta_f1"][k], True)
                           for k in ("improve_fraction", "decline_fraction", "unchanged_fraction")) + "%。", "",
              "## test_987", "", "第 4、4a、4b、4c 行均展示 B+G0.05+H1。品红框为放大区域。", "",
              "![掩码并排](../figures/eval_metrics/test_987_masks.png)", "",
              "![t2 边界叠加](../figures/eval_metrics/test_987_boundaries.png)", "",
              "![局部放大](../figures/eval_metrics/test_987_zoom.png)", "",
              "边界配色：预测青色、真值黄色、重合白色。" + ZOOM_RULE,
              f"窗口 [x0,y0,x1,y1)={example['zoom_xyxy']}，代理像素数 {example['zoom_score']}。", ""]
    lines += table(["示例行", "像素 F1 %", "IoU %", "BF2 %", "目标 F1 %", "新增误检目标数", "全测试集下降图 %"],
                   [[r, *[fmt(example["six_numbers"][r][k], True) for k in
                          ("pixel_f1", "pixel_iou", "boundary_f1_d2", "object_f1")],
                     fmt(example["six_numbers"][r]["new_false_objects"]),
                     fmt(example["six_numbers"][r]["decline_fraction"], True)] for r in EXAMPLE_ROWS])
    lines += ["## 实现口径", "",
              "边界为掩码减 3×3 腐蚀，图外按背景；BF 用欧氏距离 ≤1/2/4/5 像素的双向独立匹配。"
              "Boundary IoU 用掩码内部带：减去 d 次 3×3 腐蚀，d=max(1,round(对角线×2%))，另报 d=3；512² 时 d=14。",
              "周长计像素方格外露的单位边（包含孔洞与图框），凸包取整张预测所有前景像素方格的几何凸包。"
              "因此粗糙度同时受碎片影响。空图周长均为 0、粗糙度缺省；单像素周长 4、凸包周长 4、粗糙度 1。"
              "精度/召回/F1/IoU 分母为零时取 0；空面积桶召回缺省；均值和分位数排除缺省值并列出有效图数。",
              "2a/4a 按字面取 (切块内 ∩ 概率>0.05) ∩ 切块外扩2像素；"
              "切块已经包含在外扩区，故外扩不会加入切块外像素。2b/4b 对最终整图作半径2圆盘开运算再闭运算，图外按背景。",
              "six_numbers(pred, gt, before) 返回单图前五个数，第六个值在未提供全测试集统计时为 None；"
              "流程页需传入 dataset_decline_fraction=summary['rows'][行]['delta_f1']['decline_fraction']。", "",
              "## 逐图分布补充", "",
              "以下直接使用 0–1 比例尺度；目标/碎片为个数、粗糙度为比值。逐图原始计数及 ΔF1 在 per_image.jsonl。", ""]
    for r in order:
        lines += [f"### {r} {rows[r]['title']}", ""]
        lines += table(["指标", "有效图数", "均值", "P10", "P50", "P90", "最小", "最大"],
                       [[k, d["n"], *[fmt(d[q]) for q in ("mean", "p10", "p50", "p90", "min", "max")]]
                        for k, d in rows[r]["distributions"].items()])
    lines += ['## E 补充与 SAM 小框精修', '',
              '有害编辑率：有实际增删像素的图中，ΔF1 < −0.5个百分点的比例；无编辑图时缺省。'
              'ΔBoundary IoU亦报告同一阈值的下降率，分母同为实际编辑图。', '']
    lines += table(['行', '实际编辑图', '有害编辑图', '有害编辑率 %', '新增像素均值/P10/P50/P90', '删除像素均值/P10/P50/P90'],
                   [[r, rows[r]['edited_images'], rows[r]['harmful_images'], fmt(rows[r]['harmful_edit_rate'], True),
                     *[' / '.join(fmt(rows[r]['distributions'][k][q]) for q in ('mean', 'p10', 'p50', 'p90'))
                       for k in ('added_pixels', 'deleted_pixels')]] for r in order])
    for key in ('delta_biou_diag', 'delta_biou_3'):
        lines += table(['行', key, '提升 %', '下降 %', '不变 %', 'P10', 'P50', 'P90', '编辑图中下降>0.5pp %', '最差五图'],
                       [[r, '', *[fmt(rows[r][key][q], True) for q in ('improve_fraction', 'decline_fraction', 'unchanged_fraction', 'p10', 'p50', 'p90', 'harmful_edit_rate')],
                         '; '.join(f"{x['image']}: {fmt(x['delta'], True)}" for x in rows[r][key]['worst5'])] for r in order])
    lines += ['2c/4c按组合先合并相邻确认切块，使用SAM3/P2/t2，保守固定第二期；与确认区域欧氏外扩5像素取交。'
              '4c不恢复原去误检删除的像素。SAM IoU以与确认区域重叠的完整二值真值目标并集为参照；'
              '面积桶按该真值并集面积，无匹配另列；此值与真值紧框实验2的逐目标IoU口径不同。', '']
    for r in ('2c_BG', '2c_BGH1'):
        lines += table([r + ' / 4c同组', '区域数', '平均SAM IoU %'],
                       [[b, d['n'], fmt(d['mean'], True)] for b, d in rows[r]['sam']['bucket'].items()])
    lines += [f"评估用时 {config['seconds']:.1f} 秒，工作进程 {config['workers']}。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=OUT / "eval_metrics_v2")
    parser.add_argument("--doc-root", type=Path, required=True,
                        help="documentation repository root containing results/ and figures/")
    args = parser.parse_args()
    summary = json.loads((args.input / "summary.json").read_text())
    checks = json.loads((args.input / "pixel_checks.json").read_text())
    if summary["config"]["images"] != 1694 or any(r["images"] != 1694 for r in summary["rows"].values()):
        raise ValueError("Only the complete SECOND test report is supported")
    figures = args.doc_root / "figures/eval_metrics"
    figures.mkdir(parents=True, exist_ok=True)
    example = render(args.input, figures, summary)
    destination = args.doc_root / "results/eval_metrics.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report(summary, checks, example), encoding="utf-8")
    print(f"Report: {destination}\nFigures: {figures}", flush=True)


if __name__ == "__main__":
    main()
