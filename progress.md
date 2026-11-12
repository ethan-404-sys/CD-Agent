# progress.md — Change Detection Agent 工作日志

> 三文件协同：先读 `task_plan.md`（计划）→ `findings.md`（证据）→ 本文件（实际动作）；日期使用 2026-11-12，所有历史成果均标为“仓库已有”，避免误作本轮新实验。

## 2026-11-12 — Web GPT：切换正确分支并建立论文规划

**当前阶段**：P0 文档级盘点完成；P1 统一论文证据表待做。  
**基线**：`ethan-404-sys/CD-Agent` 的 `claude/project-thread-2tuaib` @ `527f7a899ebb64edd1282569502de14787171448`。  
**写入方式**：独立分支 `planning/cd-agent-paper-20261112`，拟通过 PR 提交给主开发分支；不触碰 main/服务器运行档案。

### 本轮确实执行

1. 通过已连接的 GitHub 读取分支、commit 祖先关系：`main` 落后研究分支 101 commits；`cs7jsz` 比后者少 39 commits。
2. 阅读 `doc/agent_baseline/README_ai.md` 的总表、G1、G3/G4/G5、SECOND、WHU、T3/T4/T5 的核心章节；阅读实际指标、补漏归因、错误分类、G5/通用化/T4.1 v3 文档。
3. 核对最新 `527f7a8` 提交：T4.1 v3 dev2 10 图试跑 Agent 72.24、fixed+area 74.42；提交报告 397 测试通过、2 跳过；**尚无 v3 的 64×3 全量实验**。
4. 检索当前 ChatGPT 项目附件和可用 Skill：未发现已安装的 `planning-with-files` Skill 说明或 ChatGPT Space 页面，因此按用户指定的三文件常用约定 `task_plan.md`、`findings.md`、`progress.md` 在 GitHub 项目中落地。若服务器环境装有该 Skill，应对齐其实际规范而不是假定已在 Web 端执行。
5. 在三文件中分离：论文论点与优先级（plan）、有来源的结论与限制（findings）、具体执行和待办（progress）。

**本轮未做**：没有运行 Slurm/GPU/API；没有改 Harness 代码、提示词、验证集或历史结果；没有让服务器 GPT 自动执行任务；没有宣称新的 F1 改进。

### 接下来的动作（未完成）

- [ ] 服务器 GPT/用户确认分支 SHA 和最新服务器运行档案是否领先于 GitHub。
- [ ] 建立 `paper/evidence_matrix.md` + CSV，逐行核对 SECOND C1 G2/G3/G5 与报告/测试/验证集口径。
- [ ] 低成本重算论文主张的统计与消融（优先已有缓存/轨迹）。
- [ ] Ethan 决定最小论文主线：方向/区域/更新任务、方法主图与核心表。
- [ ] 若确有必要，新实验按小样本→开发集→冻结→报告集执行，并先估成本；否则不要启动新 GPU 工作。

### 当前障碍和不确定性

- Web 端没有服务器 Slurm 及完整轨迹文件系统；基于已提交的仓库结果，不等于独立复跑。
- `README_ai.md` 顶部总表有时混合版本，需每项从原始 config 和指标源核实。
- 技术 novelty 尚未完成文献检索，论文强弱取决于同工具固定/规则对照的说服力。
- 不要把 GitHub PR 的创建等同于服务器 GPT 已收到执行命令；要等人/机器人明确接管。

### 交接规则

下一位 Agent 在本日志追加新日期条目：实际命令、Git SHA、样本清单/数据划分、运行目录、费用、耗时、结果文件、失败/回滚与下一步。不要只改计划而不留下日志，也不要为了更新规划改写历史实验原始结果。
