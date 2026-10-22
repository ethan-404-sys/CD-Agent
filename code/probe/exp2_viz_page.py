"""Build an offline Chinese experiment-2 page from selected figures and metadata."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from urllib.parse import quote

DATASETS = ("levir", "whu", "sysu", "second")
DISPLAY = {"levir": "LEVIR-CD", "whu": "WHU-CD", "sysu": "SYSU-CD", "second": "SECOND"}
ROLES = {"help": "2a_select 收益最大", "hurt": "2a 增益最小（伤害示例）",
         "fill": "2b 收益最大", "strict": "2a_strict 误删示例"}
OBJECT_ROLES = {"hit_gain": "命中目标：SAM3 改善最大", "hit_loss": "命中目标：SAM3 退步最大",
                "miss_filled": "漏检目标：SAM3 IoU ≥ 0.5，面积最大",
                "miss_failed": "漏检目标：SAM3 IoU < 0.5，面积最大",
                "largest_remaining": "补充展示：其余已提示目标中面积最大"}
CLASSES = {"hit": "命中", "partial": "部分命中", "miss": "整目标漏检"}
TREATMENTS = {"replace": "替换", "keep": "保留", "add": "并入", "none": "无操作"}

CSS = """
:root {
  color-scheme: light;
  --bg: #f3f5f8; --surface: #fff; --text: #202a38; --muted: #566478;
  --border: #ccd4df; --accent: #175d98; --soft: #e9eff7;
  --tp: #ddd; --fp: #ed4b50; --fn: #408bff; --tn: #20242c;
  --hit: #44bb88; --partial: #ffc04d; --miss: #b97bea;
  --radius: 12px; --gutter: 16px; --width: 1180px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme=light]) {
    color-scheme: dark;
    --bg: #10161e; --surface: #19222e; --text: #e8eef6; --muted: #b1bfd0;
    --border: #3b4a5e; --accent: #91caff; --soft: #233246;
  }
}
:root[data-theme=dark] {
  color-scheme: dark;
  --bg: #10161e; --surface: #19222e; --text: #e8eef6; --muted: #b1bfd0;
  --border: #3b4a5e; --accent: #91caff; --soft: #233246;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 16px/1.75 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: var(--width); margin: 0 auto; padding: 28px var(--gutter) 64px; }
h1, h2, h3, h4 { line-height: 1.35; overflow-wrap: anywhere; }
h1 { font-size: clamp(1.6rem, 4vw, 2.3rem); }
h2 { margin-top: 2.4rem; } h3 { margin-top: 0; }
a { color: var(--accent); } a:focus-visible, summary:focus-visible { outline: 3px solid var(--accent); }
nav { display: flex; flex-wrap: wrap; gap: 8px 18px; }
article, .block { background: var(--surface); border: 1px solid var(--border);
                  border-radius: var(--radius); padding: 20px; margin: 20px 0; }
figure { margin: 22px 0; } img { display: block; max-width: 100%; height: auto; margin: auto; }
figcaption, .muted { color: var(--muted); font-size: .92rem; }
figcaption { margin-top: 8px; }
.scroll { overflow-x: auto; max-width: 100%; }
table { width: 100%; border-collapse: collapse; font-size: .92rem; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }
th { background: var(--soft); } td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.badge { display: inline-block; border-radius: 6px; background: var(--soft); padding: 2px 8px; margin: 2px; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.legend i { display: inline-block; width: 14px; height: 14px; margin-right: 6px;
             border: 1px solid var(--border); vertical-align: middle; }
details { border: 1px solid var(--border); border-radius: var(--radius); margin: 14px 0; padding: 12px; }
summary { cursor: pointer; font-weight: 600; } details table { margin-top: 14px; }
code { overflow-wrap: anywhere; } section, article { scroll-margin-top: 16px; }
@media (max-width: 640px) {
  article, .block { padding: 12px; } th, td { padding: 6px; }
  main { padding-top: 16px; } details { padding: 8px; }
}
"""


def e(value):
    return escape(str(value), quote=True)


def f1(value):
    return f"{100 * value:.2f}%"


def delta(value):
    return f"{100 * value:+.2f} 个百分点"


def figure(figroot, dataset, stem, filename, caption):
    path = figroot / dataset / stem / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    url = quote(f"exp2_figs/{dataset}/{stem}/{filename}", safe="/")
    return (f'<figure><a href="{url}"><img src="{url}" alt="{e(caption)}" loading="lazy" '
            f'decoding="async"></a><figcaption>{e(caption)}</figcaption></figure>')


def selection_table(dataset, selection):
    rows = []
    for role, entry in selection["selections"].items():
        stem = Path(entry["image"]).stem
        metric = (f"损失 {entry['tp_lost']:,} 个 TP 像素" if role == "strict"
                  else f"{entry['target_setting']}：{delta(entry['delta_f1'])}")
        rows.append(f'<tr><td>{ROLES[role]}</td><td><a href="#{e(dataset)}-{e(stem)}">'
                    f'{e(entry["image"])}</a></td><td class="num">{entry["gt_pixels"]:,}</td>'
                    f'<td>{e(metric)}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>选图角色</th><th>图名</th>'
            '<th>真值变化像素</th><th>选图指标</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')


def object_card(figroot, dataset, stem, o):
    rows = [("目标类别 / 面积", f"{CLASSES[o['class']]} / {o['area']:,} 像素"),
            ("采用时相", o["chosen_view"]),
            ("SAM3 IoU", f"{o['sam_iou']:.4f}"), ("检测器 IoU", f"{o['det_iou']:.4f}")]
    rows.extend((s, TREATMENTS[t]) for s, t in o["treatment"].items())
    table = "".join(f"<tr><th scope=\"row\">{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    caption = (f"目标 {o['id']}：依次为 SAM3 输入裁块、SAM3 掩码叠加、检测器原始裁块掩码、该目标真值。"
               "黄框为真值框；绿色正点仅用于说明其位置，纯框提示未使用此点。")
    return (f'<details><summary>目标 {o["id"]} · {e(OBJECT_ROLES[o["panel_role"]])}</summary>'
            f'<div class="scroll"><table><tbody>{table}</tbody></table></div>'
            + figure(figroot, dataset, stem, o["panel"], caption) + '</details>')


def image_section(figroot, dataset, meta, roles):
    name = meta["image"]
    stem = Path(name).stem
    parts = [f'<article id="{e(dataset)}-{e(stem)}"><h3>{e(name)}</h3>',
             '<p>' + " ".join(f'<span class="badge">{ROLES[r]}</span>' for r in roles) + '</p>',
             f'<p>检测器：<code>{e(meta["detector"])}</code>；原始 F1：'
             f'{f1(meta["settings"]["original"]["f1"])}；真值变化像素：{meta["gt_pixels"]:,}。</p>']
    captions = {
        "overview": "输入与检测结果：t1、t2、真值变化、检测器 0.5 掩码、检测器 TP / FP / FN。",
        "objects": "按最佳预测连通域 IoU 分类真值目标；每个目标均标出真值外接框与编号。",
        "compose": "整图合成结果。标题中的 F1 为百分比，dF1 为相对原始检测器的百分点变化。",
    }
    for key in ("overview", "objects"):
        parts.append(figure(figroot, dataset, stem, meta["figures"][key], captions[key]))
    parts.append('<h4>代表目标</h4><p class="muted">依次选择命中目标中改善最大、退步最大者，'
                 '以及漏检目标中 SAM3 IoU ≥ 0.5 和 &lt; 0.5 的最大目标；同分按目标编号升序。'
                 '不足两例时补充其余已提示目标中面积最大者；全部已提示目标不足两例时展示实际数量。</p>')
    if meta["missing_panel_roles"]:
        parts.append('<p class="muted">本图没有符合以下规则的目标：'
                     + '；'.join(e(OBJECT_ROLES[r]) for r in meta["missing_panel_roles"]) + '。</p>')
    shown = [o for o in meta["objects"] if "panel" in o]
    for o in shown:
        parts.append(object_card(figroot, dataset, stem, o))
    if not shown:
        parts.append('<p>本图无已有 SAM3 结果的目标，无法展示逐目标面板。</p>')
    unprompted = sum(not o["prompted"] for o in meta["objects"])
    if unprompted:
        parts.append(f'<p class="muted">本图有 {unprompted} 个目标未运行 SAM3，不对这些目标直接执行替换或并入。</p>')
    parts.append('<p class="muted">检测器 IoU 来自完整预测连通域与该真值目标的匹配；'
                 '裁块中的检测器图显示全部原始预测像素。表中的处理方式表示该目标触发的操作，'
                 '其他目标的删除和并入仍可能影响同一区域。</p>')
    parts.append(figure(figroot, dataset, stem, meta["figures"]["compose"], captions["compose"]))
    if meta["strict"] is not None:
        strict = meta["strict"]
        component = strict["shared_component"]
        parts.append('<h4>整块删除造成的真阳性损失</h4>')
        if component:
            parts.append(f'<p>预测连通域 {component["component"]} 同时覆盖真值目标 '
                         f'{e(", ".join(map(str, component["gt_ids"])))}；该连通域内损失 '
                         f'{component["tp_lost"]:,} 个 TP 像素。</p>')
        else:
            parts.append('<p>本图未找到同时覆盖至少两个真值目标的已替换预测连通域。</p>')
        parts.append(f'<p>整图 2a 比 2a_strict 多 {strict["tp_difference"]:,} 个 TP 像素。'
                     '白色损失图表示 2a 保留、2a_strict 丢失的真值像素。</p>')
        parts.append(figure(figroot, dataset, stem, meta["figures"]["strict"],
                             '共享预测连通域及其覆盖的真值目标、2a_strict、2a，以及损失的 TP 像素。'))
    parts.append('</article>')
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figroot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>实验 2：SAM3 真值框上界的处理过程</title>', f'<style>{CSS}</style></head><body><main>',
             '<header><h1>实验 2：SAM3 真值框上界的处理过程</h1>',
             '<p>使用已保存的纯框提示结果，展示修边界、补整目标漏检和整图合成。所有 ΔF1 均以百分点展示。</p>',
             '<nav aria-label="数据集导航">' + ''.join(f'<a href="#{d}">{DISPLAY[d]}</a>' for d in DATASETS)
             + '</nav></header>',
             '<section class="block"><h2>四个数据集汇总</h2><!--SUMMARY_TABLE--></section>',
             '<section class="block"><h2>处理流程</h2><!--PIPELINE_SVG-->',
             '<p>检测器从 0.5 掩码出发。真值目标按 8 连通域拆分，SECOND 先按两期类别转移拆分。'
             '目标与预测连通域的最佳 IoU ≥ 0.5 为命中，0.1 ≤ IoU &lt; 0.5 为部分命中，IoU &lt; 0.1 为整目标漏检。</p>',
             '<p>裁块边长为 max(3 × 目标外接框长边, 128 像素)，裁块限制在影像范围内。'
             'SAM3 使用 predict_inst 单掩码结果。WHU 按分期标签判定新建或拆除；无法判定时比较两期。'
             'LEVIR、SYSU、SECOND 比较两期与真值的 IoU，取较高者；同分沿用提示记录中先出现的时相。</p>',
             '<p>2a 对命中目标删除匹配预测连通域并贴入 SAM3 掩码，保留原连通域落在其他真值目标上的像素；'
             '2a_strict 删除整个匹配连通域；2a_select 仅在 SAM3 IoU 严格高于检测器 IoU 时替换。'
             '2b 对整目标漏检直接并入 SAM3 掩码。both_select 合并 2a_select 与 2b。'
             '原实验另有 both（2a + 2b）和 2c（部分命中只并不删），本页展示指定的四种合成图。</p>',
             '<p>目标位置、外接框、正点位置、目标分类、时相判断和选择性替换判断均使用真值信息。'
             '本页使用纯框结果；逐目标图中的正点只说明位置，未用于该次 SAM3 推理。</p>',
             '<p>这些上界只跑一轮提示，不按误差迭代；修边界仅处理命中目标，补漏仅处理整目标漏检。'
             'SECOND 未运行 SAM3 的小碎片不触发直接修改。</p></section>',
             '<div class="legend" aria-label="像素颜色图例">' + ''.join(
                 f'<span><i style="background:var(--{k})"></i>{label}</span>' for k, label in
                 (("tp", "TP 真阳性"), ("fp", "FP 假阳性"), ("fn", "FN 假阴性"), ("tn", "TN 真阴性")))
             + '</div>',
             '<div class="legend" aria-label="目标分类图例">' + ''.join(
                 f'<span><i style="background:var(--{k})"></i>{label}</span>' for k, label in CLASSES.items())
             + '</div>']
    for dataset in DATASETS:
        selection = json.loads((args.figroot / dataset / "selection.json").read_text())
        if selection["dataset"] != dataset or selection["variant"] != "box":
            raise ValueError(f"Wrong dataset/variant in {dataset}/selection.json")
        parts += [f'<section id="{dataset}"><h2>{DISPLAY[dataset]}</h2>',
                  f'<!--CONCLUSION_{dataset}-->',
                  '<p>前三个角色仅从真值变化像素 ≥ 1,000 的图像中选择；同分按文件名升序。'
                  '同一图像允许承担多个角色，下方合并展示并保留全部角色标签。</p>']
        if dataset == "whu":
            parts.append('<p>误删示例优先采用 test_255；缺少此图时，选择 2a 与 2a_strict 的 TP 差最大的图，'
                         '同分按文件名升序，该角色不设面积门槛。</p>')
        parts.append(selection_table(dataset, selection))
        roles_by_image = {}
        for role, row in selection["selections"].items():
            roles_by_image.setdefault(row["image"], []).append(role)
        for name, roles in roles_by_image.items():
            meta = json.loads((args.figroot / dataset / Path(name).stem / "meta.json").read_text())
            if meta["image"] != name or meta["dataset"] != dataset or meta["detector"] != selection["detector"]:
                raise ValueError(f"Metadata mismatch: {dataset}/{name}")
            parts.append(image_section(args.figroot, dataset, meta, roles))
        parts.append('</section>')
    parts.append('</main></body></html>')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(args.out, flush=True)


if __name__ == "__main__":
    main()
