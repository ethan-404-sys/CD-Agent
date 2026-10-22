"""Fill the exp2_visual.html placeholders: flow diagram, summary table, per-dataset conclusions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DETECTORS = {"levir": ("LEVIR-CD", "SChanger-base"), "whu": ("WHU-CD", "SChanger-small"),
             "sysu": ("SYSU-CD", "SChanger-small"), "second": ("SECOND", "MambaSCD-Base")}
SETTINGS = ("2a", "2a_select", "2b", "both_select")

# Quoted from results/exp2_oracle.md (conclusions), not new claims.
CONCLUSIONS = {
    "levir": "LEVIR 上 SAM3 平均 IoU 0.867，低于 SChanger 的 0.878–0.883：SAM3 给出的单体建筑轮廓与 LEVIR 的标注口径存在系统差异，检测器则是在该口径上训练的。全部替换后 F1 下降 1.2–1.5；补齐全部整目标漏检后 F1 提升 +0.50–0.55。",
    "whu": "对 SChanger 已命中的目标，SAM3 只在 30–41% 的目标上 IoU 更高，全部替换后 WHU 基本不变（−0.1 到 −0.3）。补齐全部整目标漏检后 F1 提升 +1.18–1.32；两侧都选对时的总上界为 +1.95 / +2.23。",
    "sysu": "通用变化上 SAM3 的分割质量明显下降（全部目标平均 IoU 0.78）。精度侧全部替换降 3.5，即使有完美判断器也只有 +1.6；补齐整目标漏检 +1.9 到 +2.5，两侧都做对的总上界 +3.5 到 +4.0。",
    "second": "MambaSCD 的边界较差，SAM3 在 84% 的命中目标上更好，所以直接替换也有 +1.1，有完美判断器也只有 +1.5；补齐整目标漏检 +7.8，两侧都做对的总上界 +9.1。大量漏检属于改建和植被等语义变化，检测器没有信号，agent 能否自己找到这些位置是实际收益的关键。",
}

EXTRA_CSS = """
:root { --gt: #b5651d; --gt-soft: #fbeedd; --det: #1f5fa8; --det-soft: #e3edf9; --sam: #5b6b2e; --sam-soft: #eef2e0; }
@media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {
  --gt: #f0a560; --gt-soft: #3a2a18; --det: #8ec0ff; --det-soft: #1b2c44; --sam: #c3d58a; --sam-soft: #2a3218; } }
:root[data-theme=dark] { --gt: #f0a560; --gt-soft: #3a2a18; --det: #8ec0ff; --det-soft: #1b2c44; --sam: #c3d58a; --sam-soft: #2a3218; }
.flow text { fill: var(--text); font: 13px system-ui, sans-serif; }
.flow .sm { font-size: 11.5px; fill: var(--muted); }
.flow .gt { fill: var(--gt-soft); stroke: var(--gt); stroke-width: 2; }
.flow .det { fill: var(--det-soft); stroke: var(--det); stroke-width: 2; }
.flow .mix { fill: var(--surface); stroke: var(--muted); stroke-width: 1.5; stroke-dasharray: 5 3; }
.flow .sam { fill: var(--sam-soft); stroke: var(--sam); stroke-width: 2; }
.flow .arrow { stroke: var(--muted); stroke-width: 1.6; fill: none; marker-end: url(#ah); }
.flow .ahead { fill: var(--muted); }
.flow .gtt { fill: var(--gt); font-weight: 600; } .flow .dett { fill: var(--det); font-weight: 600; }
td.pos { color: var(--sam); } td.neg { color: var(--fp); }
blockquote { margin: 8px 0 16px; padding: 8px 14px; border-left: 3px solid var(--border); color: var(--muted); }
"""


def box(x, y, w, h, cls, lines):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="{cls}"/>']
    for i, (text, small) in enumerate(lines):
        out.append(f'<text x="{x + 12}" y="{y + 22 + 18 * i}"{" class=\"sm\"" if small else ""}>{text}</text>')
    return "".join(out)


def arrow(x1, y1, x2, y2):
    return f'<path class="arrow" d="M{x1},{y1} L{x2},{y2}"/>'


def pipeline_svg():
    W = 940
    parts = [f'<div class="scroll"><svg class="flow" viewBox="0 0 {W} 620" width="{W}" role="img" '
             'aria-label="实验 2 处理流程：真值目标拆分、命中判定、裁块送 SAM3、时相选择、合成规则">',
             '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             '<path class="ahead" d="M0,0 L10,5 L0,10 z"/></marker></defs>']
    # inputs
    parts.append(box(20, 10, 270, 52, "gt", [("真值变化图", False), ("SECOND 另有两期类别标签", True)]))
    parts.append(box(335, 10, 270, 52, "det", [("检测器 0.5 掩码", False), ("预测连通域", True)]))
    parts.append(box(650, 10, 270, 52, "mix", [("t1 / t2 影像", False), ("只作为 SAM3 输入", True)]))
    # step 1
    parts.append(box(20, 100, 270, 72, "gt", [("① 拆成真值目标", False), ("8 连通域；SECOND 按（t1 类, t2 类）", True),
                                               ("转移拆分后再取连通域", True)]))
    parts.append(arrow(155, 62, 155, 98))
    # step 2
    parts.append(box(335, 100, 585, 72, "mix", [("② 每个目标与预测连通域求最佳 IoU，判定类别", False),
                                                 ("IoU ≥ 0.5 命中 · 0.1 ≤ IoU < 0.5 部分命中 · IoU < 0.1 整目标漏检", True),
                                                 ("目标来自真值，匹配对象来自检测器", True)]))
    parts.append(arrow(290, 136, 333, 136))
    parts.append(arrow(470, 62, 470, 98))
    # step 3
    parts.append(box(20, 210, 430, 90, "gt", [("③ 真值外接框 → 裁块", False),
                                               ("正方形，边长 max(3 × 长边, 128 px)，截到影像内", True),
                                               ("提示 = 真值框（另一组加 1 个正点：目标内离边界最远的像素）", True),
                                               ("所有目标先跑，结果与检测器无关", True)]))
    parts.append(box(490, 210, 430, 90, "sam", [("SAM3 交互头 predict_inst", False), ("单掩码输出；裁块内部缩放到 1008", True),
                                                 ("每个目标、每个时相只跑一轮", True), ("SECOND 只跑 ≥ 20 px 的目标", True)]))
    parts.append(arrow(155, 172, 155, 208))
    parts.append(arrow(450, 255, 488, 255))
    parts.append(arrow(785, 62, 785, 208))
    # step 4
    parts.append(box(20, 338, 900, 70, "gt", [("④ 时相选择", False),
                                               ("WHU：两期建筑标签判新建（用 t2）/ 拆除（用 t1），无法判定时两期都跑", True),
                                               ("LEVIR、SYSU、SECOND：两期都跑，取与真值 IoU 高的一期", True)]))
    parts.append(arrow(705, 300, 705, 336))
    # step 5
    parts.append(box(20, 446, 900, 150, "mix", [("⑤ 合成规则（起点：检测器 0.5 掩码）", False),
                                                 ("2a        命中目标：删去匹配的预测连通域，贴 SAM3 掩码；连通域落在其他真值目标上的像素保留", True),
                                                 ("2a_strict 同 2a，但整块删除匹配连通域（会误删相邻目标）", True),
                                                 ("2a_select 只在 SAM3 IoU 高于检测器 IoU 时替换（取舍用真值）", True),
                                                 ("2b        整目标漏检：直接并入 SAM3 掩码", True),
                                                 ("2c        部分命中：只并不删　·　both = 2a + 2b　·　both_select = 2a_select + 2b", True),
                                                 ("", True)]))
    parts.append(arrow(470, 408, 470, 444))
    # legend
    parts.append('<text x="560" y="586" class="gtt">■ 来自真值</text><text x="680" y="586" class="dett">■ 来自检测器</text>'
                 '<text x="810" y="586" class="sm">虚线 = 两者都用</text>')
    parts.append('</svg></div>')
    return "".join(parts)


def summary_table(figroot):
    rows = []
    for d, (name, det) in DETECTORS.items():
        totals = json.loads((figroot / d / "selection.json").read_text())["totals"]
        f = {s: round(100 * totals[s]["recomputed"]["f1"], 2) for s in ("original",) + SETTINGS}
        cells = []
        for s in SETTINGS:
            dv = round(f[s] - f["original"], 2)
            cells.append(f'<td class="num {"pos" if dv > 0 else "neg" if dv < 0 else ""}">{f[s]:.2f}（{dv:+.2f}）</td>')
        rows.append(f'<tr><td>{name}</td><td>{det}</td><td class="num">{f["original"]:.2f}</td>{"".join(cells)}</tr>')
    return ('<p>F1（%），SAM3 真值框提示；括号内为相对原始的 ΔF1。数值由本页逐图复算求和得到，与 '
            '<code>metrics.json</code> 逐项一致。</p><div class="scroll"><table><thead><tr><th>数据集</th><th>检测器</th>'
            '<th>原始</th><th>2a</th><th>2a_select</th><th>2b</th><th>both_select</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>'
            '<p class="muted">局限：提示只跑一轮，不按误差迭代；修边界只处理命中目标；补漏只处理整目标漏检。'
            '目标位置、外接框、时相选择和 2a_select 的取舍都用了真值，所以这些都是提示完美时的上界。</p>')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--figroot", type=Path, required=True)
    ap.add_argument("--page", type=Path, required=True)
    args = ap.parse_args()
    html = args.page.read_text(encoding="utf-8")
    html = html.replace("</style>", EXTRA_CSS + "</style>", 1)
    html = html.replace("<!--PIPELINE_SVG-->", pipeline_svg())
    html = html.replace("<!--SUMMARY_TABLE-->", summary_table(args.figroot))
    for d, text in CONCLUSIONS.items():
        html = html.replace(f"<!--CONCLUSION_{d}-->",
                            f'<blockquote>引自 <code>exp2_oracle.md</code>：{text}</blockquote>')
    args.page.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
