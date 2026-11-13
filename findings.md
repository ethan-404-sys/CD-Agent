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


## 8. 用户最新方向决定（2026-11-12，**不是实验数据**）

Ethan 明确指出：当前大多数任务已有不错的历史结果，多个任务的展示表记录其最佳历史 Harness 版本，这是有意保留；**不要求为了统一 G5/G6 重新逐项跑全任务**。当下研究重点转为较弱任务根因与可真正形成创新的方法开发。

主线：
- **轨迹行为分析**（尚未正式开始时优先完成）：必须区分 Harness/Toolbox Bug、工具能力不足与 Agent 规划/推理/调度不足；看全过程而不是只看最终分数。
- **T3 图像示例**：当前效果不能证明 Agent 真能利用图像示例；需要在开发集上持续尝试直到得到有效机制证据，遇到瓶颈换方法并参考论文，而不是一次 pilot 不理想就放弃。
- **多来源补漏 `find_missed`**：为核心创新候选，需要真正发现并补回漏检 FN，不应只是低门限工具包装。结果与完整成本/来源互补性要经开发集实证。
- **Harness Debug**：继续可靠性问题排查与修复，提供可回归的证据。

Ethan 2026-11-12 的既有完成/待办清单包含 Gate6（`view.original_size`、`compare_features` 分位数）、`analysis/trace_behavior.py`、T4.1 v3 64×3、xBD 已有开发 10 图问法、S1–S5 前置、T3.1 开发 60 任务攻关。**这些是任务指令，不代表实验已完成。**

**新增硬边界**：需要新收集/下载整备或人工标注数据的任务须等 2026-11-13 会议后讨论；**已存在的开发数据可继续用**。T3 / find_missed 是允许服务器 GPT 自主、多轮优化、必要时查文献并换技术路线的研究型任务；其余任务可以作为交付型任务完成即结束。

协作约定：复用 `README_ai.md` 和 `index.html` 作为技术历史及展示的一对镜像；`task_system.html` 是任务定义；`README_t3_routes.md` 和 `README_missed_sources.md` 是专项细节；复用原有 `doc/agent_baseline/README_ai.md` §0.2 汇总新共识。Web GPT 与服务器 GPT 都维护 GitHub 文档，不另立两套冲突权威。

**更正前文判断**：此前“应暂停 T3 和漏检工具、优先统一论文证据矩阵”的建议已被用户更高优先级的研究决定取代；现行计划以 `task_plan.md` 最新版为准。既有 F1、CI 不受本次研究决策改变。
## 2026-11-12 服务器实物核对

仓库没有 Gate6 或 trace_behavior.py；progress 原记录只有 Web 端计划，不能当执行记录。
S1–S5 的服务器结果已生成，先前“没有结果”的说法已过时：WHU dev60 起点 F1 60.909，整组件 TP>FP 选择参照 S1 65.666、全来源 76.987；它不是实际验证器收益，也不是数学最优上限。来源 `../exp6_staging/batch99_find_missed_pre/results/missed_sources.md`。
T3 原 B 的 k16 F1 7.47、A 11.11，来源 `../exp6_staging/batch98_t3_routes/results/t3_routes.md`。仍需做几何路线，不能重复原有 API 请求。

2026-11-13核实：T3几何实验并非未运行，60任务成功；未交付导致用户看不到结果。目标类最佳18.06仍低于历史固定21.38。轨迹扫描无结果的直接原因是1小时时限和全量末尾落盘设计；不是Slurm控制面故障。首次测试失败属我提交命令缺少包根参数，修正后401项通过。


## 2026-11-13 单一技术文档与 exp-loop 核对

最新约定取代前述“README_ai/index双份镜像＋专项README”：技术说明只维护 README_ai，流程三文件保留。T3、find_missed 与 Gate6 内容已合并，删除原专项文件；保留历史结果与轨迹。
实读工作区 exp-loop：tick 会在无hook等分支提交下一轮；record 未使用 min_gain 判小幅正提升；finalize 即使推送或通知失败仍置 finalized。以上为当前代码事实，不是对已修复机制的确认。未修改该共享框架、未调用推进/收尾命令、未查询其他作业。

exp-loop修复验证：16项测试通过；tick只补控制hook，不读指标或提交研究作业；min_gain真实参与停滞判断；add在队列耗尽后通过start hook恢复；收尾失败保持pending、可重试。用户要求取消预估费用拦截已落实，保留实际累计预算停止。真实Slurm链与自动推送/QQ验证另行记录，不以单测代替集群验证。

真实事件链证据：cd-loop-chain-20261113连续完成3轮，指标10、10.4、10.8，停滞计数2；第四脚本留在队列未提交。14.8秒完成实验阶段，自动推送与QQ已成功。该验证只证明调度/收尾机制，模拟指标不是研究结果。

## 2026-11-13 hook职责更正

旧hook只是按预置阈值调度，不把结果交给模型；这是之前机制与用户研究需求的实质差距。用户删除默认“两轮不足1分”，失败应先诊断修复。已确认当前App Server支持向同一会话注入toolOutput，实测事件已进入本模型上下文；计算节点不能用现有SSH登录管理节点，采用一次HTTP通知到轻量接收器、再交本地控制接口。此接收器不查询Slurm、不决定研究路线，cron只补送遗漏事件和既有兜底。

回传验证定位到具体网络问题：管理节点主机名同时解析127.0.1.1与内网地址，默认绑定选了回环，前两事件由cron补送。修复为明确内网地址后，event-0003（job183974/hook183975）从03:37:00记录到03:37:01被当前模型接受，delivery_via=hook_http。21项新回归通过，证据在results/agent_baseline/exp_loop_review_20261113；模型已根据三次事件做出continue、补修复验证和finish三次决定。

## 2026-11-13 Gate6实测口径

view.box_pixels把坐标越界与框顺序/空框合并成同一句bad_args。审计必须再按image_shape数值验证，不能按bad_args或该句文本直接计数；box_too_large是不同错误，不计入坐标越界。Gate6当前只提供原图尺寸与坐标说明，没有裁剪错误坐标或更改图像；是否降低模型错误须用开发10任务配对检验。

新分析回归9项已在计算节点通过，含remove_region保留ID的轮廓匹配与断点尾行恢复。抽核train_1136/T1.3/g5dev：历史错误为box_too_large，不是坐标越界；核验26个不同历史轮廓中21个参与最终决策，其余被后续重叠轮廓覆盖，需单列superseded而非误报缺失。正常submit预核验报告排除异常计数；未看图就服从核验意见不等同于已证实盲信。

百炼预检更正：旧脚本的代理探测与直连探测复用同一urllib Request，ProxyHandler会修改请求，原Connection refused不能证明计算节点直连失败。仅CSV workspace端点、清空代理、新建请求；保留HTTP状态，成功后移除临时预检。用户已提供gpu42 200/182模型的验证。

连接子阶段收尾：184009取消后184010正常回传，已add --finish，不再重试。事件中gpu43网络信息来自上一轮旧文件，不能算取消作业的结果。已删除tools/exp_loop/gate6_preflight.py；配对准备器采用用户确认的gpu42，移除旧失败记录拦截。


## 2026-11-13 解除开发筛选对全量扫描的依赖

此前配对未提交的直接依赖是 coordinate_audit 在全量扫描末尾才写 paired_tasks.jsonl，并非配对脚本未准备好。该依赖不必要：开发60清单内的历史会话足以筛选10任务。新增 select_gate6_dev 只读取该范围并复用已有严格坐标记录；与全量扫描共享的 journal 只读，不修复/截断它。全量扫描保留跨任务统计职责；修正全量审计第二阶段 metric 仍读取旧预检文件的问题。


2026-11-13 事件处理：基础历史扫描183951已完成19,777会话、278配置任务组、读取失败0，明确finish并交exp-loop收尾；完整四类行为结论仍待扩展队列，不能用基础报告替代。坐标审计183978保存20,301会话后因json_qwen37plus_r3b_s1/T5.1/val/val_58缺input失败；现场核实steps/messages均0字节，属于初始化空目录。初始失败与统计已另存audit_initial_*；新增空会话单列及回归，继续原队列02做断点补漏和其他bad_args差异复核，不重扫成功会话。
