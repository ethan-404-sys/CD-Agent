# 给 Codex：交叉审核科研进度与 exp8 任务文档

## 你的角色
你是这个遥感变化检测课题的第二审稿人。另一个模型（Claude）写了进度总览和下一步实验文档，请你**独立核对**，找出事实错误、逻辑漏洞和遗漏。不要重写文档，只给审核意见。

## 要读的材料
1. 进度总览：仓库 main 分支 `doc/research_progress.md`。
2. 新实验任务：仓库 `ethan-404-sys/CD-Agent` main 分支的 `doc/exp8_object_pool.md`。
3. 核对依据（main 分支）：`results/summary.md`、`results/exp2_oracle.md`、`results/exp5c_end_to_end.md`、`results/exp6b_verifier.md`、`results/exp6e_fp_removal.md`、`results/exp6f_v1_variants.md`、`results/exp6i_misregistration.md`、`results/env.md`、`doc/exp7_prompt_refinement.md`、`doc/eval_metrics.md`、`figures/exp6g/all_pieces/test_759/README.md`、`figures/exp6g/gt_clean/README.md`。
4. 6g 人工审核：`audit/samples.json`、`audit/audit_answers.json`，以及项目文件 `label_noise/human_vs_gt.md`。
5. 文献：你整理的论文知识库。

## 请逐项回答
1. **数字核对**：进度总览和 exp8“为什么做”一节里的每个数字，能否在上面的结果文件中找到出处？列出找不到出处或与原文不符的数字，给出原文位置和正确值。
2. **结论是否过度**：哪些判断超出了证据？例如“V1/V2 失败很大程度来自数据”“SYSU 标注问题比 SECOND 少”“以对象为单位在 SYSU 上损失不大”。说明证据够不够、缺什么实验。
3. **exp8 设计**：
   - 跨来源去重规则（IoU ≥ 0.5 或较小者 80% 落在较大者内）是否合理？文献里（DynamicEarth、AnyChange、Zero-OVCD 等）有没有更常用的做法？
   - 对象级单元会不会把真变化和没变的部分合在一个对象里，导致标签纯度反而下降？exp8 是否测得出这一点？
   - SYSU 用官方 val 预测建训练池，可能有什么偏差？有没有更好的办法（不重训检测器的前提下）？
   - 四份审核页各 200 个对象、按“正 / 负 / 高分负例 / 中间标签”分层，样本量和分层够不够回答“标签可信度”？
   - 有没有和 exp7（进行中，SAM3 提示精修）重复或冲突的地方？
4. **遗漏**：要回答“候选聚合是否该作为两条优化线的统一上游”，exp8 还缺什么对照或指标？
5. **文献补充**：遥感变化检测里，处理季节性伪变化、同类外观变化（屋顶换色）的标注约定或方法有哪些？SECOND 原论文对此有无明确定义？给出处。

## 输出格式
- 按上面 1–5 分节，每条意见一行：问题 → 依据（文件 + 位置或论文）→ 建议改法。
- 最后列出你认为**必须在发给服务器前修改**的不超过 5 条。
- 只说你核实过的；推测请标“推测”。
