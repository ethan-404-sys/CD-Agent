# 旧拆除核验增益的定向人工审查

本审查读取已完成的旧 pilot 结果，不重问模型、不更改旧结果。按贡献较大案例选取，存在明确选择偏差；不代表随机人审准确率。

Top20 structured 相对 four：总 TP 增加 56，FP 减少 35567。train_1322 的 FP 从 44843 降至 2771，减少 42072，是净收益主要来源。structured 相对初始答案新增 TP 共 22404，其中 train_119 贡献 20760（约 92.7%）。因此总量收益集中，需保留逐图结果和图像级区间，不能外推为稳定普遍提升。

人工查看 UID `second_disappeared:train_1322:1` 的实际输入 `../missed_verifier_20261114/pilot/answers/four/inputs/second_disappeared__train_1322__1/0.jpg` 与 `1.jpg`：框内是完整长方形厂房，前后位置与轮廓基本一致，蓝屋顶变为灰褐条纹。four 原始响应承认 footprint remains constant，但将 roof structure changed 解释为 rebuilt，回答 yes。structured 原始响应判断 same_object=true、appearance_only=true，回答 no；finish_reason=stop，输出 131 tokens，没有截断。

任务规则另有直接证据：同目录实际 `request.json` 的 system scope 仅接受 “a building demolished (present in T1, absent in T2)”，并明确同址替换 “answer no, type rebuilt”；user 末段要求仅计 disappeared，但前面通用问题仍询问 newly constructed、demolished 或 replaced。four 的 `change=yes,type=rebuilt` 因而首先是明确的任务 scope 遵循错误：即使现实存在翻修或同址替换，按本任务也不能以 rebuilt 接受。通用问句与方向限定并置，可能增加遵循难度；这是机制推断，非受控归因。

区分三层结论：视觉层只观察到屋顶颜色/纹理变化和仍在的建筑轮廓；规则层已能判定 rebuilt→yes 与明确排除规则冲突；评分层该候选 TP=0、FP=42614，仅是像素收益 proxy，不充当语义真值。structured 回答 no 符合本任务，但其将纹理差异具体归因为季节或光照仍未由图像证明。该案例不能证明结构化问法在所有拆除场景更优，也不能用单图 smoke 的负面结果否定旧完整组收益。

原始响应证据：旧 pilot `answers/four/second_disappeared__train_1322__1.json` 与 `answers/structured/second_disappeared__train_1322__1.json`。本审查没有运行批量计算或读取 GT 来选择在线候选。
