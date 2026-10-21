"""Build matching offline HTML / Markdown from the same content (exp5, CPU).

Reads only shared experiment outputs. No dependency on the document checkout.
Images, Mermaid sources, numeric records and MLLM transcripts stay beside the page.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

from common import OUT
from exp6b_common import write_json
from pipeline_select import DEFAULT_OUT, read_json

TEMPLATE = Path(__file__).with_name("pipeline_template2.html")
LABELS = {"1": "检测器原预测", "5": "V1 none 补漏", "v2a": "V2a 补漏（仅三图）",
          "2_BGH1": "oracle 补漏", "3": "oracle 去误检", "4_BGH1": "oracle 两路",
          "2a_BGH1": "oracle 补漏：软边界", "2b_BGH1": "oracle 补漏：开闭运算",
          "4a_BGH1": "oracle 两路：软边界", "4b_BGH1": "oracle 两路：开闭运算",
          "remove_detector_mean": "检测器平均概率去误检", "remove_verifier": "V1 去误检"}
GLOSSARY = [
    ("双时相影像 t1 / t2", "同一地点前后两期 RGB 影像，SECOND 为 512×512。"),
    ("检测器", "MambaSCD-Base 输出变化概率和 0.5 二值图；下游校正使用已保存的二值 PNG。"),
    ("真值（GT）", "人工变化标注；SECOND 的两期语义标签记录变化前后的六类地物。只用于训练标签和评估。"),
    ("漏检 / 误检", "蓝色为漏检 FN，红色为误检 FP，绿色为正确预测 TP。"),
    ("无信号漏检", "预测覆盖不足 10%、面积至少 20 像素的真值目标。历史统计按语义转移拆目标。"),
    ("候选（来源）", "候选工具提出可能变化的区域：H1、B、G0.05、C1/C2。"),
    ("覆盖率", "候选覆盖目标至少 50%，且候选面积不超过目标外框的 4 倍时算找到；统计无信号漏检目标。"),
    ("纯度", "区域内真变化像素比例；来源面积合计按候选重复计数，全部来源并集另行去重。"),
    ("切细 / 切块", "SLIC 根据两期颜色与位置划分超像素，大候选与超像素相交后形成切块。"),
    ("验证器", "逐块判断变化的模型；补漏用高分块，去误检删低分块。"),
    ("验证器 V1 / V2", "V1 为特征加 MLP，本页用 none 表格基线；V2 为 Qwen3.5-4B LoRA，直接看两期裁块。"),
    ("补漏切块 / 去误检切块", "前者来自候选工具，后者来自检测器自身预测区域（pred）；V1 主模型仅用补漏切块训练。"),
    ("oracle", "直接按真值挑块的理想上界。页面将其与真实验证器结果分别标注。"),
    ("F1 / ΔF1", "F1 为精度与召回的调和平均；ΔF1 为校正后减校正前。页面 F1 用百分数，ΔF1 用百分点。"),
    ("F1/2 门槛", "并入像素的真变化比例超过当前 F1 的一半时，像素 F1 才会上升；SECOND 约 37.08%。"),
    ("AUC / 召回 80% 时误检率", "AUC 衡量排序；误检率为放过真变化的同时放过多少负例。验证阈值与测试 ROC 诊断分别标注。"),
    ("交叉拟合（K 折）", "训练集分三折，用其余两折训练的检测器预测留出折，为训练验证器产生真实漏检。"),
    ("SeK", "SECOND 语义变化指标，用于选择交叉拟合检测器检查点。"),
]


def pct(value):
    return "未评估" if value is None else f"{100 * value:.2f}%"


def delta(value):
    return f"{100 * value:+.4f}"


class Page:
    """Write both formats from shared calls, without a bespoke Markdown parser."""

    def __init__(self, results):
        self.results = results
        self.html, self.md = [], []
        self.assets = set()

    def heading(self, text, level=2):
        self.html.append(f"<h{level}>{html.escape(text)}</h{level}>")
        self.md.append("#" * level + " " + text + "\n")

    def paragraph(self, text):
        self.html.append("<p>" + html.escape(text) + "</p>")
        self.md.append(text + "\n")

    def links(self, items):
        self.html.append('<p class="sources">来源：' + "；".join(
            f'<a href="{html.escape(path, quote=True)}">{html.escape(label)}</a>' for label, path in items) + "</p>")
        self.md.append("来源：" + "；".join(f"[{label}]({path})" for label, path in items) + "。\n")

    def table(self, headers, rows):
        rows = [[str(x) for x in row] for row in rows]
        self.html.append('<div class="table-scroll"><table><thead><tr>' + "".join(
            "<th>" + html.escape(x) + "</th>" for x in headers) + "</tr></thead><tbody>" + "".join(
            "<tr>" + "".join("<td>" + html.escape(x) + "</td>" for x in row) + "</tr>" for row in rows) + "</tbody></table></div>")
        escape = lambda text: text.replace("|", "&#124;").replace("\n", "<br>")
        self.md.append("| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "\n".join(
            "| " + " | ".join(map(escape, row)) + " |" for row in rows) + "\n")

    def code(self, text, language="text"):
        self.html.append("<pre><code>" + html.escape(text) + "</code></pre>")
        fence = "`" * max(3, max((len(run) for run in text.split() if set(run) == {"`"}), default=0) + 1)
        self.md.append(f"{fence}{language}\n{text}\n{fence}\n")

    def gallery(self, items):
        self.assets.update(path for _, path in items)
        self.html.append('<div class="gallery">' + "".join(
            f'<figure><a href="{path}"><img src="{path}" loading="lazy" alt="{html.escape(label, quote=True)}"></a>'
            f'<figcaption>{html.escape(label)}</figcaption></figure>' for label, path in items) + "</div>")
        self.md.append("| " + " | ".join(label.replace("|", "&#124;") for label, _ in items) + " |\n| " +
                       " | ".join("---" for _ in items) + " |\n| " + " | ".join(
                           f"![{label}]({path})" for label, path in items) + " |\n")

    def diagram(self, name, nodes, edges=None):
        # Both Mermaid source and offline SVG use this one ordered node list.
        if edges is None:
            edges = [(i, i + 1) for i in range(len(nodes) - 1)]
        source = "flowchart TD\n" + "\n".join(
            f'  n{i}["{title}<br/>{body}"]' for i, (title, body) in enumerate(nodes)) + "\n" + "\n".join(
                f"  n{a} --> n{b}" for a, b in edges) + "\n"
        folder = self.results / "pipeline_figs/overview"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.mmd").write_text(source, encoding="utf-8")
        height = len(nodes) * 102
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 {height}" role="img">',
                 '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">'
                 '<path d="M0,0 L0,6 L9,3 z" fill="#657080"/></marker></defs>',
                 '<rect width="100%" height="100%" fill="#f6f6f3"/>']
        for i, (title, body) in enumerate(nodes):
            y = 8 + i * 102
            colour = "#2d6cdf" if "检测器" in title else "#b5541c" if "候选" in title else "#1f7a4d"
            parts.extend([f'<rect x="12" y="{y}" width="896" height="77" rx="9" fill="white" stroke="{colour}" stroke-width="2"/>',
                          f'<text x="460" y="{y+29}" text-anchor="middle" font-size="20" fill="#1d1f23">{html.escape(title)}</text>',
                          f'<text x="460" y="{y+57}" text-anchor="middle" font-size="15" fill="#424850">{html.escape(body)}</text>'])
        for a, b in edges:
            if b == a + 1:
                path = f"M460,{8+a*102+78} L460,{8+b*102-4}"
            else:
                x, outside = (908, 917) if a % 2 else (12, 3)
                path = f"M{x},{8+a*102+38} H{outside} V{8+b*102+38} H{x}"
            parts.append(f'<path d="{path}" fill="none" stroke="#657080" stroke-width="2" marker-end="url(#arrow)"/>')
        parts.append("</svg>")
        (folder / f"{name}.svg").write_text("\n".join(parts), encoding="utf-8")
        path = f"pipeline_figs/overview/{name}.svg"
        self.assets.add(path)
        self.html.append(f'<img class="diagram" src="{path}" alt="{html.escape(name)}">')
        self.html.append('<details><summary>Mermaid 图源</summary><pre>' + html.escape(source) + "</pre></details>")
        self.md.append("```mermaid\n" + source + "```\n")

    def layers(self, stem):
        root = f"pipeline_figs/{stem}"
        layers = [(f"source_{s}", f"来源 {s}", False) for s in ("H1", "B", "G0.05", "C")]
        layers += [("labels", "真值标签色", False), ("v1", "V1 分数色", True),
                   ("boundaries", "全部切块边界", False), ("v1_accepted", "V1 通过阈值轮廓", True),
                   ("gt", "真值轮廓", True)]
        parts = ['<section class="viewer"><fieldset><legend>第 1–4 步图层</legend>',
                 '<label>底图 <select class="base-select"><option value="t2">t2</option><option value="t1">t1</option></select></label>']
        for key, label, visible in layers:
            parts.append(f'<label><input type="checkbox" data-layer="{key}" {"checked" if visible else ""}>{label}</label>')
            self.assets.add(f"{root}/layer_{key}.png")
        parts.append(f'</fieldset><div class="canvas"><img class="base" src="{root}/t2.png" data-t1="{root}/t1.png" data-t2="{root}/t2.png" alt="示例底图">')
        for key, label, visible in layers:
            parts.append(f'<img class="overlay" data-layer="{key}" src="{root}/layer_{key}.png" alt="{label}" {"" if visible else "hidden"}>')
        parts.append("</div></section>")
        self.html.append("\n".join(parts))
        self.md.append("HTML 版提供同一组相对路径 PNG 的图层开关；下列静态图展示相同数据。\n")


def result_gallery(page, info, keys, prefix="result"):
    items = []
    for key in keys:
        number = info["numbers"][key]
        caption = f"{LABELS[key]}：F1 {pct(number['pixel_f1'])}；ΔF1 {delta(number['delta_f1'])} 点"
        items.append((caption, f"pipeline_figs/{Path(info['image']).stem}/{prefix}_{key}.png"))
    page.gallery(items)


def example_page(page, info):
    stem = Path(info["image"]).stem
    root = f"pipeline_figs/{stem}"
    page.heading(f"{info['role']} · {info['image']} · V1 ΔF1 {delta(info['delta_f1'])} 点")
    page.links([("本图数字与规则", root + "/example.json"), ("全部切块分数", root + "/pieces.json")])
    page.gallery([(view, root + f"/{view}.png") for view in ("t1", "t2", "gt", "prob", "base_mask")])
    page.paragraph(f"原预测使用保存 PNG；它与保存概率再取 >0.5 相差 {info['saved_vs_prob_pixels']} 像素。"
                   "候选筛选仍沿用建池时的 prob>0.5，最终结果与统一指标均沿用保存 PNG。")
    page.layers(stem)
    page.heading("1. 各来源候选", 3)
    page.paragraph("H1 用 AnyChange（SAM ViT-H）比较两期特征；B 用 SAM3 六类语义做差；"
                   "G0.05 从 prob>0.05 的连通域提出候选，筛掉被 prob>0.5 覆盖超过一半的区域；"
                   "C1 找未匹配建筑实例，C2 找已匹配实例的外观差异。C2 沿用池中的原始集合。"
                   "来源颜色：H1 橙、B 紫、G0.05 青、C 粉；黄线是真值。")
    page.gallery([(s, root + f"/source_{s}.png") for s in info["per_source"]])
    page.table(["来源", "候选数", "面积合计 px（含重叠）", "面积加权纯度", "并集面积 px", "并集纯度"],
               [[s, n["candidates"], n["area_sum"], pct(n["purity"]), n["union_area"], pct(n["union_purity"])]
                for s, n in info["per_source"].items()])
    page.heading("2. 全部候选", 3)
    n = info["candidates_all"]
    page.gallery([("全部来源及各来源轮廓", root + "/candidates_all.png")])
    page.paragraph(f"候选共 {n['count']} 个；去重并集 {n['union_area']} 像素，占全图 {pct(n['union_fraction'])}；"
                   f"并集里真变化占 {pct(n['union_purity'])}。重叠区域可在图层开关中逐个查看来源。")
    page.heading("3. 全部补漏切块", 3)
    n = info["pieces"]
    page.gallery([("绿正、红负、灰无标签，白线切块边界", root + "/pieces_all.png")])
    page.paragraph(f"共 {n['total']} 块：正 {n['positive']}、负 {n['negative']}、无标签 {n['unlabeled']}。"
                   f"有标签正例率 {pct(n['positive_rate_labeled'])}；正块占全部切块 {pct(n['positive_rate_all'])}。"
                   "≥50% 真值为正、<10% 为负，中间部分不用于监督训练，但继续参加推理。"
                   "全来源均展示，重叠块保留各自 ID；标签色重叠时按 piece_id 后绘制者覆盖，边界取全部块轮廓。")
    page.heading("4. V1 打分热图", 3)
    page.gallery([("V1 全部补漏切块；重叠像素显示最高分", root + "/v1_heat.png"),
                  ("V1 分数色条与精确阈值", root + "/v1_colourbar.png")])
    page.paragraph(f"V1 none 阈值 {info['v1_threshold']!r}，来自 exp6/verifier/none/thresholds_second_cv.json 的 "
                   "recall.B+G0.05+H1；白线只标出该组合中 score≥阈值的块。分数来自 none/second/scores.npz。"
                   "C1/C2 分数也显示，结果比较固定使用 B+G0.05+H1。")
    page.heading("5. V2a 打分热图", 3)
    page.gallery([("V2a 同一批全部补漏切块", root + "/v2a_heat.png"),
                  ("sigmoid(logit 差) 色条", root + "/v2a_colourbar.png")])
    page.paragraph(f"复用 V2 384×384 红色轮廓裁块；不筛除无标签块。验证 recall-80 logit 差阈值为 "
                   f"{info['v2a_threshold_logit_diff']!r}，色条对应概率 {info['v2a_threshold_probability']:.9f}。"
                   "接受规则直接比较 logit 差，白线同样只标 B+G0.05+H1；阈值出处为 "
                   "exp6/v2/a/eval/sample/validation_thresholds.json。")
    page.heading("6. 补漏结果对比", 3)
    result_gallery(page, info, ("1", "5", "v2a", "2_BGH1"))
    page.paragraph("绿 TP、红 FP、蓝 FN。三种补漏均使用 B+G0.05+H1；oracle 使用真值挑块。"
                   "V1 与 V2a 各自沿用验证集已冻结的阈值，两者阈值选择目标不同。")
    page.heading("7. 边缘质量", 3)
    page.gallery([("青框为放大窗口", root + "/edge_where.png")])
    page.paragraph(f"放大框 xyxy={info['zoom']['box']}。{info['zoom']['rule']}。"
                   "这是突出边缘变化的可复现展示规则，不宣称找到了人工感知上最锯齿的区域。"
                   "红线为预测边界，黄线为真值；局部图下的 F1 是整图指标。")
    result_gallery(page, info, ("1", "4_BGH1", "4a_BGH1", "4b_BGH1"), "edge")
    result_gallery(page, info, ("2_BGH1", "2a_BGH1", "2b_BGH1"), "edge")
    page.paragraph("直接调用 batch14 的 eval_metrics_run.reconstruct。2a/4a：已接受切块内保留 prob>0.05，"
                   "再与该块外扩 2 像素相交；按此先后顺序，相交不会增加切块外像素。"
                   "2b/4b：对最终整图做半径 2 欧氏圆盘开运算再闭运算，图外为背景。"
                   "两路合并为 cleaned ∪ (accepted ∖ original)。")
    page.heading("8. 去误检分支", 3)
    page.gallery([("检测器均值；白线为删除块", root + "/remove_detector_heat.png"),
                  ("检测器均值阈值", root + "/remove_detector_colourbar.png"),
                  ("V1 分数；白线为删除块", root + "/remove_v1_heat.png"),
                  ("V1 删除阈值", root + "/remove_v1_colourbar.png")])
    page.table(["方式", "pred 块数", "删除阈值（严格小于）", "删除块数", "删除像素", "阈值出处"],
               [[k, n["pieces"], repr(n["threshold"]), n["rejected"], n["removed_pixels"], n["threshold_source"]]
                for k, n in info["removal"].items()])
    page.paragraph("pred 掩码与分数均按 6b 池的 image、piece_id 对应，使用 V1 none 的同一 scores.npz。"
                   "6e 的独立 pieces.jsonl 编号不用于关联这些分数。两个删除阈值均由 297 张验证图的 micro F1 选定。"
                   "V1 none 尚未用 pred 切块训练。")
    result_gallery(page, info, ("1", "remove_detector_mean", "remove_verifier", "3"))
    page.heading("本图六个数", 3)
    page.paragraph("前五项由 six_numbers 计算本图；最后一项为全测试集下降占比。V2a 只有三图推理，"
                   "最后一项未评估。实际去误检两行另遍历完整测试集计算下降占比，并核对 6b 保存的 TP/FP/FN。")
    page.table(["结果", "像素 F1", "IoU", "BF2", "目标 F1", "新增误检目标", "全测试集下降占比", "ΔF1 点"],
               [[LABELS[k], pct(n["pixel_f1"]), pct(n["pixel_iou"]), pct(n["boundary_f1_d2"]),
                 pct(n["object_f1"]), n["new_false_objects"], pct(n["decline_fraction"]), delta(n["delta_f1"])]
                for k, n in info["numbers"].items()])
    page.links([("本图六数与各行下降占比出处", root + "/example.json"),
                ("统一指标全测试集汇总副本", "pipeline_figs/eval_metrics_summary.json"),
                ("实际去误检全测试集汇总", "pipeline_figs/removal_summary.json")])


def mllm_page(page, examples, complete):
    page.heading("MLLM 验证器：真实输入、生成回答与分数")
    page.paragraph(examples["rule"])
    page.paragraph("V2a 只答是/否，本身没有理由；V2b 先写理由后答；V2c 先答后写理由。"
                   "下文原文来自 model.generate，不用标签模板冒充回答。打分单独复用 score_one："
                   "a/c 在答案前缀后比较首词元，b 先生成至“答案：”再比较；b 的打分理由也保留。"
                   "完整生成与打分是两次调用，表中同时报告自然生成答案和验证阈值判断。")
    for item in examples["examples"]:
        root = "pipeline_figs/mllm/" + item["tag"]
        page.heading(f"{item['image']} / 切块 {item['piece_id']} / {'正' if item['label'] else '负'}例", 3)
        page.paragraph(f"来源 {item['source']}，面积 {item['area']}，真值占比 {pct(item['gt_frac'])}，crop={item['crop']}。")
        page.gallery([("实际 t1 输入，384×384", root + "/t1.png"), ("实际 t2 输入，384×384", root + "/t2.png")])
        page.table(["版本", "logit 差", "sigmoid 分数", "验证阈值", "阈值判断 / 对错", "生成答案 / 对错", "达到长度上限"],
                   [["V2" + v, f"{n['logit_diff']:.9g}", f"{n['score']:.9g}", repr(n["threshold"]),
                     f"{n['threshold_label']} / {n['threshold_correct']}",
                     f"{n['generated_label']} / {n['generation_correct']}", n["reached_token_limit"]]
                    for v, n in item["variants"].items()])
        page.paragraph("判断 1=是、0=否；None 表示未解析到合法答案，不计为答对。达到长度上限表示回答可能截断，原文完整保留。")
        for variant, result in item["variants"].items():
            page.heading("V2" + variant + " 完整提示词", 4)
            page.paragraph("输入顺序：Image 1 (T1, earlier): → t1；Image 2 (T2, later): → t2；以下正文。"
                           f"assistant 预填前缀：{result['assistant_prefix'] or '无'}；enable_thinking=False。")
            page.code(result["prompt"])
            page.heading("V2" + variant + " 原始生成文本（不含预填前缀）", 4)
            page.code(result["raw_generated_text"])
            if variant == "b":
                page.paragraph("用于 logit 打分的原始理由生成；状态：" + result["scoring_status"])
                page.code(result["scoring_raw"])
            page.links([("原始回答文件", root + f"/{variant}_raw.txt"),
                        ("序列化完整模型输入", root + f"/{variant}_model_input.txt"),
                        ("提示词文件", root + f"/{variant}_prompt.txt"), ("逐项记录", root + f"/{variant}.json")])
    page.paragraph(f"本次 V2a 对三图共 {complete['pieces']} 个补漏切块打分，其中无标签 {complete['unlabeled']} 个；"
                   f"本次作业记录耗时 {complete['seconds'] / 60:.1f} 分钟（含加载模型、打分和六次完整生成）。")


def training_and_progress(page, summary, complete, date):
    page.heading("训练流程与当前证据")
    data = read_json(OUT / "exp6/v2/data/data_report.json")
    sizes = {s: data["splits"][s]["sampled"] for s in ("train", "val", "test")}
    for v, meta in complete["models"].items():
        if meta["train_samples"] != sizes["train"] or meta["validation_samples"] != sizes["val"]:
            raise ValueError(f"V2{v} training metadata differs from data report")
    page.diagram("training", [
        ("已完成 · SECOND 训练数据", "2,968 对影像；训练 / 验证器验证 = 2,671 / 297 张"),
        ("已完成 · 三折交叉拟合检测器", "各训练 50k 步；留出折输出合计覆盖 2,968 张，F1 71.04"),
        ("已完成 · 候选工具与切块池", "2,982,956 个补漏切块；有标签正例率约 15.27%"),
        ("已完成 · 自动标签", "块内真值 ≥50% 为正、<10% 为负；其余退出监督训练"),
        ("已完成 · 验证器 V1 none", "11 维：概率均值/最大/最小/P90、log 面积、6 维来源 → MLP"),
        ("已完成 · 验证器 V2a / V2b / V2c", f"训练 {sizes['train']:,} 块；冻结视觉编码器；语言部分 LoRA r=16、alpha=32"),
        ("已完成 · 验证集选择及测试评估", f"V1 全测试集；V2 验证抽样 {sizes['val']:,} / 测试抽样 {sizes['test']:,}"),
    ], edges=[(0, 1), (1, 2), (2, 3), (3, 4), (3, 5), (4, 6), (5, 6)])
    page.paragraph("图中的 V1 和 V2 是同一候选池上的两种训练分支；V2 不接收 V1 的输出。"
                   "交叉拟合只用于准备验证器训练数据，测试仍用官方检测器。SLIC 中位面积沿用实验 5c 冻结值 298，"
                   "面积 >1192 才切细；补漏小于 10 像素的块不保留，pred 小块保留。")
    page.paragraph("V1 none 实际取 Pool.meta[:, :11]，包含六个来源指示（含 pred），不含是否切细。"
                   "带骨干的 V1 还取两期切块/外接框池化特征、绝对差、余弦相似度和完整 12 维元数据。"
                   "V1 为 512→256 隐层 MLP，dropout 0.15，按验证 AP 早停。V2 从验证器训练图中均匀无放回抽样，"
                   "不做类别平衡；视觉编码器和原语言参数冻结，仅语言部分线性层 LoRA 可训练。"
                   "正例理由由训练语义标签生成，负例用通用未变化理由；真值不进入推理输入。")
    page.links([("交叉拟合与候选池", "exp6a_pool.md"), ("V1 / V2 结果", "exp6b_verifier.md")])
    page.paragraph("训练规模与参数出处：exp6/v2/data/data_report.json、exp6/v2/{a,b,c}/best.json；"
                   "冻结范围与输入实现：code/probe/exp6v2_common.py 的 load_model/input_batch、"
                   "code/probe/exp6b_common.py 的 Features.batches。训练图交叉拟合 F1 71.04，测试 74.17；"
                   "候选检测器概率中位数 0.0041 / 0.0371，提示概率刻度不同，但尚不能据此确认失败原因。")
    stats = []
    for variant in ("a", "b", "c"):
        report = read_json(OUT / f"exp6/v2/{variant}/eval/sample/sample_report.json")
        frozen = report["frozen_threshold_test"]["validation_recall_80"]
        stats.append(["V2" + variant, f"{report['validation']['auc']:.4f}",
                      f"{report['classification']['overall']['auc']:.4f}", pct(frozen["test_fpr"]),
                      pct(frozen["test_recall"]), f"{report['timing']['seconds_per_piece']:.2f}",
                      f"{report['timing']['total_gpu_hours']:.1f}"])
    page.table(["模型", "验证 AUC", "测试抽样 AUC", "验证 recall-80 阈值的测试 FPR", "实际测试召回", "秒/块", "验证+测试全池 GPU 小时估计"], stats)
    page.paragraph("V2 全池预计约 330–1,500 GPU 小时，包含 pred 和无标签块；因此已有评估只做抽样。"
                   "V1 同一测试抽样 AUC 约 0.873，高于 V2。测试抽样按来源和标签分层，用总体权重统计；"
                   "抽样分类结果不能代替全测试集端到端像素指标。本页额外仅跑三图全部补漏块。"
                   "上表直接读取 exp6/v2/{a,b,c}/eval/sample/sample_report.json。")
    page.heading("全测试集统一指标")
    rows = []
    for key in summary["row_order"]:
        r = summary["rows"][key]
        m = r["micro"]
        rows.append([key, r["title"], pct(m["pixel_f1"]), pct(m["pixel_iou"]), pct(m["bf2_f1"]),
                     pct(m["object_f1"]), f"{r['distributions']['new_false_objects']['mean']:.3f}",
                     pct(r["delta_f1"]["decline_fraction"])])
    page.table(["行", "设定", "像素 F1", "IoU", "BF2", "目标 F1", "每图新增误检目标均值", "下降图占比"], rows)
    page.links([("实际读入的全测试集汇总", "pipeline_figs/eval_metrics_summary.json")])
    page.paragraph("BF2 使用掩码减 3×3 腐蚀提取边界，距离容差 2 像素；目标按二值 8 连通域，"
                   "分别以覆盖/重叠 ≥50% 算召回/精度。新增误检目标按零真值重叠连通域数的正向净增量。"
                   "全测试集像素、边界、目标指标均先累加计数再算 micro；下降占比按逐图 ΔF1<0。"
                   "历史按语义转移拆分的目标召回，与此二值目标 F1 口径不同。")
    page.heading("当前进度（" + date + "）")
    page.table(["部分", "最新记录与结论", "来源文件"], [
        ["检测器与候选", "SECOND 检测器 F1 74.17；无信号漏检占真值像素 14.9%；四来源覆盖率 52.2%。", "summary.md；exp5a_anychange.md"],
        ["6a", "交叉拟合、候选池和自动标签均完成；训练池 298 万补漏块，有标签正例率 15.27%。", "exp6a_pool.md"],
        ["6b V1", "none 测试 AUC 0.8745；B+G0.05+H1 测试 ΔF1 −1.3127 点；骨干版本出现过拟合。", "exp6b_verifier.md；exp6/verifier/none/eval_second.json"],
        ["6b V2", "a/b/c 训练和抽样评估完成，均低于同样本 V1；全测试集端到端 ⏳ 未做。", "exp6b_verifier.md 第4节；exp6/v2/*/eval/sample/sample_report.json"],
        ["6e", "oracle 去误检 SECOND +5.74、SYSU +4.07 点；SECOND 概率基线与 V1 实际去误检已完成，ΔF1 约0；pred 重训与 LEVIR/WHU ⏳。", "exp6e_fp_removal.md（oracle）；exp6b_verifier.md 第3节（较新实际结果）"],
        ["6f", "A1/A2、D、B1/B2、C 已完成；A3 ⏳。实际并入最接近零是 A2 DINOv3 B+G0.05，−0.00954 点；不并入的0不算收益。", "exp6f_v1_variants.md；exp6/variants/a2_dinov3/eval_second.json"],
        ["6g", "320 个样本盲审页面已就绪；人工答案 ⏳ 未返回，干净子集统计 ⏳。", "../audit/label_audit.html；../audit/samples.json"],
        ["6h", "6h-1 已完成；G1-0/cos/sm 测试 ΔF1 −0.29/−0.17/−0.35 点，三种子；6h-2 ⏳。", "exp6h_temporal_consistency.md"],
        ["6i", "配准诊断完成；可估计负例约57%偏移>3px，V1 AUC差约−0.029；可估计范围仅约11–12%，暂不处理。", "exp6i_misregistration.md"],
        ["6c / 6d / V3", "泛化数据与预测已准备，验证器泛化评估 ⏳；RingMo-SAM ⏳，Zero-OVCD 停止；V3 待定。", "summary.md；exp6d_backlog.md"],
    ])
    page.paragraph("进度与历史数字依据 2026-10-21 已读取的结果文档；提交日期由 Claude 填写。"
                   "6e 旧页标题仍写只完成上界，本页同时采用更新的 6b 实际去误检记录。"
                   "6g 没有人工答案，不能给出审核后的准确率或标签噪声结论。")
    page.links([(p, p) for p in ("summary.md", "exp6a_pool.md", "exp6b_verifier.md", "exp6e_fp_removal.md",
                                "exp6f_v1_variants.md", "exp6h_temporal_consistency.md", "exp6i_misregistration.md")]
               + [("6g 审核页", "../audit/label_audit.html")])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-out", type=Path, default=DEFAULT_OUT / "doc")
    parser.add_argument("--v2", type=Path, default=DEFAULT_OUT / "v2")
    parser.add_argument("--submission-date", default="【提交日期待 Claude 填写】")
    args = parser.parse_args()
    results = args.doc_out / "results"
    fig = results / "pipeline_figs"
    generated = read_json(fig / "figures_complete.json")
    complete = read_json(args.v2 / "complete.json")
    if generated["v2"] != complete:
        raise ValueError("Figure and V2 runs differ")
    selection = generated["selection"]
    summary = read_json(Path(selection["metrics_dir"]) / "summary.json")
    examples = read_json(args.v2 / "examples.json")
    shutil.copytree(args.v2 / "examples", fig / "mllm", dirs_exist_ok=True)
    for filename in ("a_scores.npz", "a_scores.jsonl", "examples.json", "complete.json"):
        shutil.copy2(args.v2 / filename, fig / ("v2_" + filename))
    write_json(fig / "eval_metrics_summary.json", summary)
    write_json(fig / "selection.json", selection)
    page = Page(results)
    page.heading("补漏与去误检：方法流程", 1)
    page.paragraph("输入两期遥感影像，检测器给出初始变化图；候选工具寻找遗漏区域，验证器逐块打分，"
                   "最后按阈值补漏或去误检。本页展示真实 V1/V2a 输出，同时保留 oracle 理想上界。")
    page.diagram("inference", [
        ("1 · 输入", "同一地点两期 RGB：t1（前期）、t2（后期）"),
        ("2 · 检测器", "一个 MambaSCD-Base → 概率图和 0.5 二值预测"),
        ("3 · 候选工具", "H1 AnyChange / B SAM3 六类做差 / G0.05 低阈值区域 / C 建筑实例差"),
        ("4 · SLIC 切块", "候选区域 → 补漏切块；检测器预测区域 → 去误检切块（pred）"),
        ("5 · 验证器", "V1：表格特征 + MLP；V2：两期 384×384 裁块 + Qwen3.5-4B LoRA"),
        ("6 · 最终变化图", "补漏：高分块并入；去误检：低分 pred 块删除；两路分别作用于原预测内外"),
    ], edges=[(0, 1), (0, 2), (1, 2), (1, 3), (2, 3), (3, 4), (4, 5)])
    page.paragraph("总览中检测器预测同时送到切块步骤，t1/t2 同时供候选工具使用。"
                   "本页 V2a 只展示补漏；去误检展示检测器平均概率与 V1。")
    page.heading("先看懂几个词")
    page.table(["词", "意思"], GLOSSARY)
    page.heading("示例选择")
    page.paragraph(selection["rule"])
    page.paragraph(f"统一指标 per_image.jsonl 行5：V1 none，{selection['combo']}，精确阈值 {selection['threshold']!r}。"
                   "示例的极端提升或下降不能代表全测试集平均收益。")
    page.table(["用途", "图名", "真值变化像素", "原 F1", "V1 后 F1", "ΔF1 点"],
               [[r["role"], r["image"], r["gt_pixels"], pct(r["before_f1"]), pct(r["after_f1"]), delta(r["delta_f1"])]
                for r in selection["examples"]])
    page.links([("选图清单与完整规则", "pipeline_figs/selection.json")])
    for info in generated["examples"]:
        example_page(page, info)
    mllm_page(page, examples, complete)
    training_and_progress(page, summary, complete, args.submission_date)
    for asset in page.assets:
        if not (results / asset).is_file():
            raise FileNotFoundError(results / asset)
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("{{CONTENT}}") != 1:
        raise ValueError("Template needs exactly one CONTENT placeholder")
    (results / "pipeline.html").write_text(template.replace("{{CONTENT}}", "\n".join(page.html)), encoding="utf-8")
    (results / "pipeline.md").write_text("\n".join(page.md), encoding="utf-8")
    write_json(fig / "page_complete.json", dict(examples=selection["examples"], assets=len(page.assets),
               submission_date=args.submission_date, content="HTML and Markdown emitted from shared content calls"))
    print(json.dumps(selection["examples"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
