# S5 缓存重匹配核查

对照原生成快照 `exp6_staging/batch99_find_missed_pre/code/agent_baseline/analysis/missed_sources_gpu.py` 的 `SAM3.visual`：appeared 只取 t2 reference，disappeared 只取 t1 reference，any 取两者；循环所有已核验 yes seed，将各缓存 masks 加入统一 found[t1/t2]，最后一次调用 `change_candidates`。原始实现不是每个 reference 独立匹配再合并。

`missed_sources.change_candidates` 先用全部两期实例按 overlap=0.25 匹配，再按方向选未匹配对象，union 后扣初始答案，最后分连通组件。当前 `night_missed_candidates.visual_instances` 与 `rematch` 保留此顺序；它们删除完全重复的实例，但 presence 判断只要求存在一个覆盖实例，且最终输出为 union，因此重复删除不改变该语义。没有发现“合并全部 reference”造成的算法偏差，不应凭此重构原来源。

已完成 LEVIR train_106 和 train_109 的实际记录均为 old_rematch_equals_cache=true，分别读取 200 和 188 个 NPZ，DINO 缓存可用。提取已从 20/30 推进至 22/30，不是停滞。每图当前重复四次计算两期实例交集（旧0.25、一组0.10、一组0.50、一组IoU0.25），这是较大实例池的明确性能成本；后续可复用一次交集表，不能为加速改变 presence-any 语义。本轮在跑代码保持不动。

新增单元案例覆盖跨 reference 的相同对象匹配、完全重复实例去重和匹配后扣起点，直接与原 `change_candidates` 输出比较。测试仅准备，待下个 Slurm CPU 轮次执行。若后续存在 mismatch，须核查实际缓存文件集合与原 seed/reference 范围，并记录逐例差异；不能把 mismatch 排除计数当作原因已经解决。
