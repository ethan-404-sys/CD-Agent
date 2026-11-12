# T4.1 v3 维护说明

v2 常量和任务保留。v3 只把指令中的 `contiguous building change blocks` 换为
`connected building change regions`。`meta.version` 等字段沿用 v2，便于逐行核对。
生成命令：`python -B -m agent_baseline.analysis.make_t41_tasks --run-root "$R" --v3 --output results`。
清单生成不读取模型或真值，不重新抽样。也支持直接运行 `analysis/make_t41_tasks.py`。

面积任务由 `session.area_min_px is not None` 判断。只有此时 `select` 的 session schema
接受 mask 和 `set_current`。没有面积的任务继续使用原 schema、提示与报告，mask 输入继续
返回 `bad_handle: Expected a inst handle.`。回归夹具 `tests/fixtures/t41_before_v3.json`
在修改前从提供的代码副本生成；六类会话比较序列化 schema、首轮真实编码消息和报告的字节。

mask select 使用 common.components 的 8 邻域；`ids` 是 inspect/remove_region/面积报告
中的组件 ID。筛选完整组件并保留 ID；面积以像素计，`area_min`/`area_max` 均包含端点，
与原实例筛选一致。任务要求严格大于阈值时用 `where="area > 1040"`。
mask 的 where 仅支持 area，实例仍支持 score/area。条件取交集。空结果返回空 mask。
返回字段沿用 `_mask`：mask、area、component_count、added_px、removed_px；默认设为当前
答案并返回 current_mask。`set_current=false` 只生成候选，与 remove_region 一致。
实例选择仍返回 inst/ids/n_in/n_out，不更新当前答案。没有新增工具。

面积报告只在发现未达阈值组件、原面积约束行出现时追加连通性说明；不自动删改答案。

离线轨迹统计：

```bash
python -B -m agent_baseline.analysis.t41_trace_stats \
  --run-root "$R" --sessions t41v2dev_g3_any_s1 \
  --tasks t41/tasks_v2_dev2/T4.1_any.jsonl --output results/t41v2dev_trace_stats
```

`--sessions` 接受一个或多个配置根目录或会话目录。`--output` 是输出目录，产物为
`t41_trace_stats.json` 和 `.md`。相对参数路径相对 run-root；报告路径也相对 run-root。
`--fixed-dir` 可指定已有固定掩码目录；默认读取 init/sam3_imc_p85 的相应 train/val 分割，
再严格按任务面积过滤，复用上一轮 dev2 的评分口径。逐会话和总体报告 F1/P/R 百分数；
总体先累计 TP/FP/FN。只分析已完成会话，并列出缺失任务。

select 同时列请求和成功次数；remove_region 只统计面积报告出现后确实删除小组件的成功调用。
PNG 不保存 ID，统计器按 remove_region/mask select 的父子关系恢复保留 ID。
T2 整体纳入判定要求当前答案保留至少 80% 的 T2 建筑分割，且答案至少 80% 来自它。
另报 T1 重叠证据。它是人工复核候选，真实大规模新建也可能满足此条件。
形态学合并候选要求更新当前答案后组件减少，且是 close 或观察到像素扩张；排除 fill_holes。
脚本只读轨迹和离线掩码，I/O 复用 io_retry，无模型/API 依赖。
同样支持直接运行 `analysis/t41_trace_stats.py`。

测试覆盖真实假规划器循环和删除后非连续 ID 的轨迹恢复。
命令：`python -B -m unittest agent_baseline.tests.test_t41_v3`。全量命令见本批 NOTES.md。
