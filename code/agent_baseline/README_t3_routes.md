# T3 开发路线诊断

`analysis/t3_routes.py` 只接受现有开发清单，先按 `t3_subset.pick(seed=1016)` 选每图一任务，再核对开发60图。只读诊断结果及旧缓存，不读取报告集。`report` 只做小规模元数据汇总；其它运行命令通过 Slurm。

第6条直接汇总 `t3_diag/report.json`。固定门限0.6；F1/P/R累计TP/FP/FN后计算。实例上限按每个真值组件独立寻找IoU≥0.5的提议，像素上限按提议并集覆盖。两项与旧诊断一样排除示例。目标类是任一期含5建筑或6运动场，其余为地表类。标签核对实际 `cls_gray.json` 与 `second_labels.ENCODING`。

路线A (`route-a`) 调用既有SegEarth实现，使用共同六类加background的概率图；对示例区域每类概率取平均，再在六个前景类中取最大。background不是SECOND转换类别。全图两期argmax结果求源类T1与目标类T2交集；预测同类时为空。示例从预测、真值中移除，类别对准确率按任务计，包括移除示例后无剩余真值的任务。真实标签只在评分阶段使用。A是Agent可自主选择的策略的离线参照，不是数学保证的上限；不加入固定路线或在线工具。新增worker沿用 `DynamicBackend.semantic`，仅将既有类别名文件写入改为I/O重试和原子替换。

路线B (`prepare-b`, `verify`, `score-b`) 修示例匹配链。可复用入口在 `analysis/t3_routes_match.py`：`ranked_indices`、`build_messages`、`filtered_union`、`match_ranked`。无真值输入。DINOv2分数只粗排序，无0.6过滤；分数相同时按原提议下标。两期提议全部保留，含重复和示例自身；每任务前k，网格4/8/16/32。取前32后复用答案评估全部k，不把各k请求数相加。删除明确no，yes取并集。未知答案不当作no；停止后不能报完整分数。

`build_messages(..., ask_variant='default')` 保持既有四图对应关系问法。`t3_transition` 显式启用示例与候选各四图，共八张384像素裁图；复用 `ask_images`、`encode_image` 和Planner的flash单价/请求参数。独立分析入口不会修改在线 `ApiAsk` 的默认问法。若后续效果好，可将公共函数接入 `match_exemplar`，届时固定路线也应同步；本批未改变在线工具。

费用来自已有flash四图ledger的token均值，输入估计为两倍均值加512，输出至少128；保守输入再翻倍、输出256，缓存按全价。每个k候选数来自旧诊断的提议数，精确计数无需读大数组。价格使用 `planner.request_cost`。只是估算，实际响应usage记账。API只会由明确的 `verify --ask-variant t3_transition` 发起。

`verify` 支持 `--concurrency` (1–16，默认2)、`--max-cost-cny` 和 `--resume`。每个候选答案原子单独保存；恢复时复用已完成答案并从成功/失败记录重建费用。每次派发前预留32000输入+256输出的费用，控制在途请求。实际用量超过预留会停止；已发出的请求仍须收尾记账。429/403/402/欠费立即写 `route_b/STOP.json`，不重试API；其他请求错误同样停下待复核。STOP保留第一条原因。清除STOP之前先查看失败记录；涨预算不自动清除STOP。只支持单进程管理并发，不支持多进程共用答案目录。

持久进度：A每图写 `route_a/progress.json`；B准备每图写 `route_b/prepared.json`；核验每请求写 `route_b/answers/`。设置不一致不能续跑。新文件I/O使用 `io_retry` 的三次尝试；`io_retry.exists`用stat重试，防止EIO被误认成缓存不存在。结果及NPZ临时文件替换，模型/API不会被I/O重试包裹。旧缓存通过只读适配器访问，不启动SAM/DINO worker。真实SegEarth及网络尚需Claude验证。

补充要求：`t3_routes_agent sample` 从固定60任务按 `random.Random(1016).sample(sorted(..., id),10)` 抽样，保持原JSONL字段和任务指令。Claude用现有harness同时启用 `match_exemplar` 与 `classify/segment_semantic`，不改提示。`metrics` 接受直接包含任务id子目录的 `--sessions`，读取 `steps.jsonl`、实例存档和最终掩码，排除示例后报F1/P/R。

轨迹统计同时给工具调用率和示例判断证据：classify中至少一个提议与示例IoU≥0.5；或语义结果与初始示例mask#0求交且规划器文字明确描述示例类别判断。仅调用全图语义不计作示例判断。几何相关不能证明主观意图，未覆盖的Python取样等情况保留人工复核项，不把自动数值称作精确的意图统计。无两期证据不会标为完整类别对判断。

运行命令、资源估计与本批实际验证见顶层NOTES.md。所有本批输出写在当前目录，公开报告的已有产物引用使用相对R的路径。

本代码副本需配套 `OVCD_CODE=$R/code_w/ovcd_v0` 并将该目录放入PYTHONPATH；默认旧工作树缺少scope参数。Slurm脚本已显式设置，不修改既有提示。当前共享盘在Python导入阶段也多次EIO；`code/t3_routes_bootstrap.py` 对导入的读文件、目录缓存和安装元数据读取限三次重试。它不重跑模块业务、模型或API。Slurm脚本及路线A子进程均使用此启动器。

## 2026-11-12 几何路线第一轮

假设：原 DINO 排序的候选粒度/外观偏离示例；先筛几何和两期颜色可降低错误候选。
`analysis/t3_geometry.py` 读取固定 dev60 与原诊断缓存；预先固定面积比 [1/4,4]、长宽比 [1/3,3]、两期 RGB 均值 RMS 距离≤0.25，再按 DINO 排序。对照是不筛选的同一排名；k=4/8/16/32，分别报告目标/地表及全部的像素 F1/P/R，排除示例区域。标签仅评分，不参与筛选。
不修改 match_exemplar，不产生新数据、不调用 API。产物先放 `.runtime/continuation-1112/t3_geometry.json`，验证完成再归档；失败时记录实际异常，不把未运行当负结果。

首轮实际结果（作业183283，5分59秒，API ¥0）：60任务全部完成。
几何+颜色 k8 全部 F1=9.83、目标类18.06；相同 k8 的纯 DINO 分别5.36、12.88。
但仍未超过既有固定路线全部10.71、目标类21.38；不扩大到报告集。
失败定位：k16 时目标类从纯DINO15.01降到13.76，说明过滤在不同候选预算下会丢失有效候选；目前只是聚合证据，不能据此认定颜色或几何哪项是根因。
产物：`results/agent_baseline/t3_geometry_dev60_20261112/metrics.json`，逐任务记录含ID与TP/FP/FN。
下一步：用已有候选按任务分解筛除的TP/FN和候选重复，不继续盲调筛选门限；此轮未证明达到目标类+5分门槛。
