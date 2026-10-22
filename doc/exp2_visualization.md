# 任务：实验 2（SAM3 真值框上界）的流程可视化

给服务器上的 Claude。`results/exp2_oracle.md` 只有表格和文字，Ethan 看不出每一步实际是怎么处理的。本任务只用已有的 exp2 产物（`exp2/<数据集>/prompts.jsonl`、`sam3/*.npz`、`objects.jsonl`、`metrics.json`）出图、做页面，**不重跑 SAM3、不训练任何模型**。缺少某张图需要的中间结果时，可以按 `probe/exp2_compose.py` 的同一逻辑在 CPU 上复算，但不改动原结果。

## 页面要讲清楚的事

### 一、流程总览（一张图）
从检测器 0.5 掩码和真值出发：
1. 真值按连通域拆成目标，SECOND 按变化类型拆（同实验 1）；
2. 每个真值目标判定为“命中”“部分命中”或“整目标漏检”，写明判定规则；
3. 取真值外接框，按 max(3 × 长边, 128 px) 裁块，送 SAM3 交互头（`predict_inst`，单掩码）；
4. 时相选择（WHU 用两期标签判新建 / 拆除；LEVIR、SYSU、SECOND 两期都跑，取真值 IoU 高的一期）；
5. 合成规则：2a（删去匹配的预测连通域，贴 SAM3 掩码，保留落在其他真值目标上的像素）、2a_strict、2a_select（SAM3 更好才替换）、2b（整目标漏检直接并入）、2c（部分命中只并不删）、both / both_select。

图中明确标出：**哪些信息来自真值**（目标位置、外接框、正点、时相选择、2a_select 的取舍），哪些来自检测器。

### 二、示例（四个数据集都要）
每个数据集选 3 张图，选图规则写明并保存为 JSON：
- **2a 帮忙最多**的图：2a_select 相对原始 ΔF1 最大；
- **2a 伤害最大**的图：2a（不带 select）ΔF1 最小，LEVIR、SYSU 上应能看到 SAM3 边界比检测器差的情况；
- **2b 补漏收益最大**的图。
只选真值变化像素 ≥ 1,000 的图；同分按文件名升序。

每张图展示：
1. t1、t2、真值、检测器 0.5 掩码，以及检测器的 TP / FP / FN 着色图；
2. 真值目标的分类叠加图：命中、部分命中、整目标漏检三种颜色，并画出每个目标的真值外接框；
3. **逐目标面板**：从该图中挑 2–4 个代表目标（至少包括一个 SAM3 更好、一个 SAM3 更差、一个漏检被补回；如有，加一个 IoU < 0.5 的补漏失败）。每个目标一行：
   - 送入 SAM3 的裁块（t1 或 t2，写明用哪期），叠加框提示和正点；
   - SAM3 输出掩码；
   - 检测器在该目标上的原掩码；
   - 真值；
   - 两者与真值的 IoU，以及在 2a、2a_select、2b 下这个目标被怎样处理（替换 / 保留 / 并入）。
4. 2a、2a_select、2b、both_select 四种合成后的整图 TP / FP / FN 着色，并写出每种的整图 F1 和 ΔF1。
5. 如有，展示一个 **2a_strict 误删** 的例子：一个预测连通域同时覆盖两个真值目标，整块删除会误删相邻目标（`results/exp2_oracle.md` 中 WHU `test_255` 是现成的例子）。

### 三、汇总
- 把四个数据集的 F1 表（框提示，Ethan 用的那一组检测器：LEVIR SChanger-base、WHU SChanger-small、SYSU SChanger-small、SECOND MambaSCD-Base）放在页面开头，列出原始、2a、2a_select、2b、both_select 的 ΔF1。
- 每个数据集一句话结论，直接引用 `results/exp2_oracle.md` 的结论，不新造结论。
- 写清楚这些上界的局限：提示只跑一轮、不按误差迭代；修边界只处理命中目标；补漏只处理整目标漏检。

## 形式
- 做成一个 HTML 页面，中文，图片用相对路径或内联，不依赖外网，并发布为 Claude artifact（与之前的流程页同样方式）。
- 同一个页面内，每个数据集一节；逐目标面板可以做成可展开的卡片。
- 图放 `results/exp2_figs/<数据集>/<图名>/`，页面源文件放 `results/exp2_visual.html`，代码放 `code/probe/exp2_viz_*.py`。

## 交付
- `results/exp2_visual.html` 和图，发布后的 artifact 链接写进 `results/exp2_oracle.md` 末尾和 `results/summary.md` 实验 2 一节。
- 提交说明列出每个数据集选中的三张图名和各自 ΔF1。
