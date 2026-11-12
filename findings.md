# findings.md — Change Detection Agent 研究事实与证据账本

> 更新：2026-11-12。审阅基线 `claude/project-thread-2tuaib` @ `527f7a899ebb64edd1282569502de14787171448`。本文件是历史实验**盘点**而非本轮新实验；全部 F1 为百分数/百分点，CI 依仓库对应报告；未重新执行服务器计算。

## 1. 仓库真相、分支与主要入口

- `main` @ `af968775` 不是项目最新状态；`claude/project-thread-cs7jsz` 比 main 多 62 commits，`claude/project-thread-2tuaib` 再多 39 commits；总计比 main 多 101 commits，且为祖先链。
- 完整主文档：[`doc/agent_baseline/README_ai.md`](doc/agent_baseline/README_ai.md) §0.1、§10.5–10.13；代码：[`code/agent_baseline/`](code/agent_baseline/)；结果：[`results/agent_baseline/`](results/agent_baseline/)。
- 以前 `results/summary.md` 的 Exp1–8、`code/ovcd_v0/` 11 轮主要用于误差摸底和固定编排教训，不是最新 Agent 基线；主工具是 SAM3、SAM、可选语义/提议/实例匹配、MLLM 核验、掩码操作。
- Harness 版本：G1 固定核验/提交门、G2 修提交门漏洞、G3 减少大框失败和路线问题、G4 新增当前答案提醒/提交框裁剪/用途说明、G5 增添 region-aware 的分割参数。6 个 skill 已有，H1 有无 skill 消融未确认收益；沙箱 Python 已实现但主配置默认关闭。G5 文档自述仅代码和单测，实际 G5 C1/C2/C4 实验则已在主 README §10.13 记录，不能混为一谈。

## 2. 当前可以进入论文主表的核心结果

| 任务/数据 | 配置与划分 | Agent F1 | 公平固定/规则对照 | 证据等级与限制 |
|---|---|---:|---|---|
| LEVIR T1.1×C0 | G1，**128 test**，一次 | 82.63 | 规则 80.97（同工具另需对照） | G1 测试集正式结果；不作为新轮调参依据，详 README §10.5 |
| LEVIR T1.1×C0 | G3，64 val × 3 | 85.76 | 规则 86.35 | Agent **未证明**优于规则 |
| LEVIR T5.1×C0 | G1，128 test，一次 | 81.19 | 规则 80.97 | 边际提升；测试集结果不可反向调参 |
| LEVIR T5.1×C0 | G1，64 val × 3 | 86.05±0.29 | 规则 86.35 | 与规则差异 CI 跨零 |
| SECOND T1.1×C1 新建 | G3，128 report × 3 | 71.31 | 相对方向规则 +7.57（原汇总） | 重要正结果；检查 G2/G3 报表一致性 |
| SECOND T1.1×C1 拆除 | G3，128 report × 3 | 48.45–48.54 | 相对方向规则 +10.49（原汇总） | 重要正结果；不同文件四舍五入/版本须锁定 |
| SECOND T5.1×C1 新建 | G3 + 指令说明，128 report × 3 | 67.68 | 方向规则 63.74；Δ +3.95 [1.01,7.43] | 成立；说明句在 dev 15 图选择过 |
| SECOND T5.1×C1 拆除 | G2，128 report × 3 | 37.52 | 规则 37.96；Δ −0.43 [−4.16,3.55] | 未确认胜规则 |
| SECOND T1.1×C2 | G5，128 report × 3 | 66.72 | G3 62.77；事后求交 66.71 | G5−G3 +3.96 [0.52,7.33]；与强事后求交相当 |
| SECOND T1.1×C4 | G5，128 report × 3 | 74.58 | G3 71.94；事后求交 76.12 | G5−G3 +2.64 [−0.37,5.77]，不能称确认 |
| WHU T1.1×C0 / C1 新建 | G3，**训练图块抽样**128 report × 3 | 64.27 / 72.86 | 规则 62.75 / 70.26 | 相对规则 CI 跨零；胜固定路线确认 |
| WHU T1.1×C1 拆除 | G3，训练图块抽样128 report × 3 | 25.86 | 规则 22.17 | +3.71 CI 跨零 |
| WHU T5.4 旧图更新 | G3，新问法，128 report × 3 | 69.87 | 固定 59.40；同问法规则 68.86 | 胜固定 +10.47 [1.79,18.44]；对规则 +1.01 [−4.10,6.82] |
| LEVIR T5.2 k=1/k=3 | G3，64 val × 3 | 84.19 / 84.59（总表） | 给定83.15；固定响应 C | 相对固定响应无确认优势 |
| LEVIR T5.3 | G3，64 val × 3 | 82.89 | 给定83.15 / 验证器挑选83.22 | 未确认增益 |
| SECOND T1.2 / T1.3 | G3，128 report × 3 | 41.22 / 46.53 | 固定46.26 / 60.88 | **显著失败**：−5.04 [−7.94,−2.19] / −14.35 [−17.28,−11.26] |
| SECOND T3.1 | G4，128 task × 3 | 15.59 | G3 20.32 | G4−G3 −4.73 [−7.67,−1.80]；更多工具调用却变差 |
| LEVIR T4.1 面积约束 | G3，64 val × 3 | 74.45 | fixed+area 77.13 / rule+area 78.48 | 显著低于两对照；v3 **仅 dev2 10 图试跑** |

**注意**：综合总表 `README_ai.md` §0.1 有部分历史中间版本数字，详细表 `results/agent_baseline/data/second_metrics.md` 给出的 SECOND G2 C1 为 72.30/50.29，不可误贴成 G3 的 71.31/48.45。统一表前需要通过 config 名称锁定每行。

## 3. 论文最强的“Agent 在做什么”证据

来源 [`results/agent_baseline/data/gap_attribution.md`](results/agent_baseline/data/gap_attribution.md)，从最终 mask 减去固定路线，并沿状态来源链追溯：
- SECOND 新建 G3：Agent 最终补入的 TP 占其总 TP 的 19.8%，整体 F1 71.31；离线去掉补漏为 63.69，差 +7.62 [3.29,13.14]。
- SECOND 拆除 G3：补入 TP 占 30.4%，F1 48.45；离线去掉补漏 40.98，差 +7.47 [−1.64,18.45]（CI 跨零）。
- 最广泛可推广的补漏来源为 `segment_text('building',score_thr=0.3)`，占 SECOND/WHU 补漏 TP 的约 39–53%，对应精确率约 40–67%。
- 点提示补漏 TP 占 SECOND 23–25% / WHU 25–41%，精确率约 93–99%，但集中于 1–3 张包含巨大建筑的图；不能当普遍增益。
- 事后删掉补漏的估计 **不是重新运行 Agent 的因果消融**；应配合固定低门限 + 相同验证器对照。

## 4. Harness 专项证据

- G1：LEVIR G1 val T1.1 85.54±0.44、T5.1 86.05±0.29；相比 G0 的整包变动在测试 T5.1 +1.53，但其中一张错误空掩码贡献巨大，不能拆成单项机制效应（README §10.5）。
- H1 Skill：相对 G1 T1.1 +0.34 [−0.97,+1.50]、T5.1 −0.42 [−1.79,+0.54]；**没有确认收益，故不进冻结主配置**。
- G5 region-aware（SECOND）：C2 对 G3 +3.96 [0.52,7.33]；C4 +2.64 [−0.37,5.77]；G5 C1 仅各一次回归，差 +0.57、−0.86（CI 均跨零）。C2 更重要的公平对照是 G3 事后 ∩ 框 66.71，与 G5 66.72 相当。
- G4 工具用途描述：T3.1 `match_exemplar` 调用会话从 74/384 (19%) 到 297/384 (77%)，结果 F1 却降低 4.73，说明 “成功调到工具” 不等于 “合理使用结果”。
- LEVIR G1 384 会话已在通用化文档中报告回放通过（6,423 个规划器请求和 21,926 个验证器重建请求）；后者并非从原始网络请求逐字节核验。

## 5. 主要失败、潜在机会与不该继续的岔路

来源 [`results/agent_baseline/data/error_taxonomy/error_taxonomy.md`](results/agent_baseline/data/error_taxonomy/error_taxonomy.md)：
- 固定路线：SECOND/WHU 大目标与整目标漏检明显；SECOND 整目标误检占 FP 约 79–93%，是 Agent/核验规则都能发挥的空间。
- Agent：SECOND 新建常见验证器误删 TP；SECOND 拆除常见规划器补进 FP；WHU C0/拆除有兜底问题；T1.2/T1.3 常选错/误用工具。
- T3 诊断（README §10.13）：SAM 提议覆盖真值像素上限 98.3%，但单提议实例 IoU≥0.5 对齐只有45.5%，DINOv2 示例选择 P 6.61%，SAM3 示例框召回很低；继续全量 T3 多变体性价比低。
- T4.1 全量（旧 v2）失败与“按建筑实例面积 vs 变化 mask 连通组件面积”口径不一致、部分面积换算错误和超预算相关；**2026-11-12 v3 仅 10 图小试** 72.24 vs 固定+面积 74.42，不能宣称全量修好。
- `p_yes` 区间策略在 SECOND 新建报告集令规则 F1 下降 0.88 [−1.88,−0.02]；不足以设计 G6。
- SECOND 放大 2× 的离线试验 −9.81；不应继续投入。

## 6. 立即需要核实的缺口（这些不是已完成事实）

1. 用代码逐行审计 SECOND C1 G2、G3、G5 的 config、结果、split、bootstrap 口径；明确哪条是同问法同工具同预算比较。
2. 补列按任务比较的成本/时延/失败率，并注明缓存与报错会话处理；对照规则是否承担同等验证器成本。
3. 把 G5 C2 的工具级 region 改动和最终硬裁剪分开做离线/在线对照，避免把“满足输出格式”写成“Agent 更会感知”。
4. 核实 T4.1 v3 正式实验 **未运行**；xBD 为代码与准备，不是已有完整性能结果。
5. 论文发表相关文献和 novelty 仍需独立检索；目前的“Agent Harness 首创”不可直接写入论文。

## 7. 资料导航

- 主开发记录：`doc/agent_baseline/README_ai.md`（特别是 §0.1、§10.5、§10.10–10.13）。
- 对照和批量结果：`results/agent_baseline/data/second_metrics.md`、`second_box_metrics.md`、`t12_metrics.md`、`t13_metrics.md`、`t52_metrics.md`、`t53_metrics_g3v2.md`。
- 机制：`results/agent_baseline/data/gap_attribution.md`、`error_taxonomy/error_taxonomy.md`、`pyes/pyes_band.md`。
- G4/G5：`code/agent_baseline/README_g4.md`、`README_g5.md`；G1 回归/通用化：`README_generalization.md`。
- T4.1 v3 小试：`results/agent_baseline/t41_v3_dev2/t41_trace_stats.md`，HEAD commit `527f7a8`。
