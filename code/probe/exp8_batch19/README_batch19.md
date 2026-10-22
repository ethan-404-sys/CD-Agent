# batch19：SYSU 官方 val 建池与验证冻结

本批按 `TASK.md` 交付代码和后续执行命令。2026-10-22 只检查源码、目录和已有 JSON 元数据，进行语法及静态检查；未运行 Slurm、推理、候选构建、指标计算或训练，未联网或安装包。当前目录不属于可用的 Git 工作树，无法提供 Git diff。写入范围限于 batch19，batch16/17/18 和 probe 原文件保持原状。

后续正式输出根目录为 `/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch19`，下文记为 `$EXP8`。本次没有创建这个输出目录，也没有生成实际冻结结果。

## 文件与依赖

| 文件 | 行为与复用 |
|---|---|
| `exp8_data.py` | 标准库读取正式 split，检查 4,000 张及 128/32 个完整原图块；定义 `pool_train` / `pool_val`；查找已有 SAM3 缓存 |
| `exp8_runtime.py` | 按绝对路径导入 batch18 runtime，注册 `sysu_val`、本批输出和固定 U0 尺度；修改仅在当前进程生效 |
| `exp8_compare.py` | 调用 `predict_schanger.run('sysu', 'small')`，对固定 8 张测试影像做预测一致性比对；参考为已有预测掩码，读取测试标签次数为零 |
| `exp8_prepare.py` | manifest、`predict_schanger.run('sysu_val', 'small')`、仅 `pool_val` 的无信号面积中位数；直接调用 batch18 `automatic`；检查 U4 缓存 |
| `exp8_sam3.py` | 沿用 batch18 独立 SAM3 入口方式，调用同一 `exp4_sam3_extract.main`；支持缓存复用、分片与续跑 |
| `exp8_pieces.py` | 直接调用 6a `process`，保存 B+G0.05 补漏块及预测块，合并 JSONL/NPZ，保留后续 6b 所需元数据 |
| `exp8_verifier.py` | 既有 6b Pool、Features 和训练接口的路径适配；本批不执行训练 |
| `exp8_pool.py` | batch18 pool 适配：直接导入 U4 构造、单元描述和测试 U0；验证池三个倍数，测试池只取冻结倍数 |
| `exp8_report.py` | 沿用 batch18 汇总和指标算法，增加两个子划分报告、ΔIoU、限制说明与 SYSU 冻结；导入原报告格式化函数 |
| `exp8_verify.py` | 沿用 batch18 保存掩码回读流程和像素计数核对，增加划分、6a/U0、子划分汇总和冻结检查 |
| `static_check.py` | 仅用标准库检查 Python 编译、README 命令语法、split 元数据和关键上游接口；不导入实验模块 |
| `static_checks.txt` | 本次静态检查的实际输出与未执行项 |

去重直接用 batch17 `groups_for` / `union_object`；候选、目标定义和 SLIC 用 probe 6a；U4 分区、扣除、oracle 和覆盖用 probe/batch16；指标用 batch16 `metrics_cd.py`。上游均只读。运行环境沿用 `exp5-env`（预测、U4、CPU 分析）及 `omniovcd-env`（SAM3）。SAM3 入口独立，不导入 SciPy、scikit-image 或 torchange。

## 数据与固定口径

- 划分唯一来源：`/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/interim/exp8/sysu_val/split.json`。`train_images` 对应 `pool_train`（3,200 张，128 块），`val_images` 对应 `pool_val`（800 张，32 块）。不重新抽样。6b 清单的 `verifier_train` / `verifier_val` 是相同名单的接口别名。
- 所有图像均为官方 val 对应的本地 `datasets/lim1/SYSU-CD/{img1,img2,label_unch0chi255}/train_12000.png`–`train_15999.png`。同一原图块 `(n-12000)//25` 的 25 张不跨划分。分组证据和官方逐图匹配见 CD-Agent 的 `results/exp8_object_pool.md`。
- 权重固定 `models/SChanger/schanger_small_sysu.pth`。与旧 SYSU test 相同的训练归一化、256² 非重叠 tile、无 TTA、关闭 TF32。保存 float16 概率及由 float32 概率阈值 0.5 得到的 PNG；候选过滤使用保存概率的 `prob > 0.5`，沿用 6a。
- B 为两期 SAM3 类别均 >0 且不同的 8 连通域；G0.05 为 `prob > 0.05` 的 8 连通域。候选面积 ≥10、原预测覆盖比例 ≤0.5。SAM3 prompts 和阈值与测试提取程序相同，保留完整 NPZ 字段。
- 默认对象为 `objects_direct_pairs`：只在不同来源间，IoU ≥0.5 或交集/较小面积 ≥0.8；按最高 IoU 配对，每个候选最多参与一对。另保留 none、nms_area、transitive 三种对照，均复用旧算法。
- **U0 固定历史 SYSU 尺度 972**。六通道 SLIC，compactness=10、start_label=1、`n_segments=max(4,int(H*W/972))`；只切面积严格大于 4×972 的候选，丢弃补漏交集 <10 像素。test 优先读取旧 6a U0；缺少整个旧池时由 batch18 原函数按 972 重建。972 原本来自历史测试统计，本批将其作为既定 U0 对照，不重新估计，也不用于 U5。
- U4 直接调用 batch18 的 ViT-H 默认自动掩码流程，两期分别生成再联合细分，按 7b 小面积优先扣除。test 只读 `exp8_batch18/automatic/sysu`。
- **U5 中位数只取 `pool_val` 800 张**：二值真值 8 连通目标，面积 ≥20 且 `prob >0.5` 覆盖 <10%。不取 `pool_train`，不读取 test 标签。无合格目标明确失败。
- 全部 4,000 张均保存 U0、U4、四种对象及 U5×4/8/16。U5 只切面积严格大于倍数×验证中位数的直接对合并对象，小对象完整保留，允许对象间重叠。
- 单元标签：真值占比 ≥0.5 正，<0.1 负，其余中间；oracle 将正单元并入原预测。`pool_train` 和 `pool_val` 分别报告每图单元数、面积、标签分布、中间占比、无信号覆盖、micro F1/IoU 及其增量，边界、目标和纯度附表沿用 batch18。
- 仅用 `summary_pool_val.json` 按 micro 像素 F1 降序、IoU 降序选倍数；精确并列取较小值。`frozen/sysu_val.json` 保留 batch18 SECOND 冻结文件的全部基础字段，增加验证名单、子划分、U0 来源和限制。冻结前要求完整 4,000 张回读验收通过。test 仅产生所选 U5 和非参数单元，报告状态 `frozen_validation`。

**限制进入 manifest、geometry、冻结文件和每份报告：官方权重可能使用过 val 选优，按已使用处理；验证部分只有 32 个原图块，800 张小图具有块内相关性。** U0 的历史尺度来源单独说明，不将其表述为验证集估计值。

## 已有缓存盘点与预测比对判据

本次只列文件名：`exp4/sysu/sam3` 有 4,000 份测试缓存，其中官方 val 文件名数量为 0；`exp4/sysu_val/sam3`、`exp4/sysu_train/sam3`、`exp8_batch18/sam3/sysu_val` 尚不存在。运行时按上述目录顺序查找，再查本批 `sam3/sysu_val`；存在即只读复用。需要补齐的图由原提取程序写入分片工作目录，完成后移入本批最终缓存；中断后原提取程序跳过已完成 NPZ。

检查时 `exp8_batch18/automatic/sysu` 有 2,149 份 NPZ，这是仍在增长的快照。TASK 提供的作业为 153155/153156；本次未查询 Slurm 状态。测试池前必须执行完整缓存检查。

预测比对固定选 `test_{0,571,1142,1713,2285,2856,3427,3999}.png`，覆盖编号范围。每张 PNG 像素必须完全一致；概率须有限、形状相同、最大绝对误差 ≤0.001（float16 保存精度量级，rtol=0）。逐图报告最大/平均误差和掩码差异像素；任一不满足则失败并阻断正式验证预测，不能按测试 F1 放宽判据。结果在 `compatibility/prediction_comparison.json`。

`predict_schanger.run` 的模型计算保持原样。其末尾原本读取标签计算指标，比对入口只将该引用替换为真实旧预测掩码，并把指标字段明确改名为 `agreement_with_cached_mask`。既有测试预测保持只读，新比对预测写本批 `compatibility/preds/sysu/schanger_small`。

## 提交公共函数

以下均为后续执行命令，本次未执行。沿用 batch18 `submit18` 的调用方式，函数改名 `submit19`。`probe/run.sbatch` 固定 gre 分区，提供离线环境和成功/失败 QQ 完成通知（任务名、状态、耗时、含输出参数的命令与日志路径）。每个 GPU 作业申请 1 卡；CPU 作业申请 1 核。所有 stdout/stderr 写本批 `logs/`。

```bash
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch19
EXP8=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch19
scontrol ping
squeue -u guisongxia
sinfo -p gre
# 在提交前核对既有作业实测内存；本次没有运行此命令。
sacct -j 153123,153124,153125,153126,153149,153150,153151,153152 \
  --format=JobID,State,Elapsed,MaxRSS,ReqMem
mkdir -p "$EXP8/logs"

# 名字、时限、内存、GPU数(0/1)、环境、前置Job ID(冒号分隔)、脚本与参数。
submit19() {
  local task_name=$1 task_time=$2 task_mem=$3 task_gpu=$4 task_env=$5 task_dep=$6
  shift 6
  local task_cpus=1
  local -a task_extra=()
  if [[ "$task_gpu" == 1 ]]; then
    task_extra+=(--gres=gpu:1)
    task_cpus=2
  fi
  if [[ -n "$task_dep" ]]; then task_extra+=(--dependency="afterok:$task_dep"); fi
  sbatch --parsable --exclude=gpu04,gpu06,gpu20,gpu37 \
    --export=ALL,JOB_TMP_ROOT=/tmp,PYTHONPATH=/guisongxia01/pangchao/wangyihan/Chang-Agent/probe,PYTHONDONTWRITEBYTECODE=1 \
    --job-name="$task_name" --cpus-per-task="$task_cpus" --mem="$task_mem" --time="$task_time" \
    --output="$EXP8/logs/%x-%j.out" --error="$EXP8/logs/%x-%j.err" \
    "${task_extra[@]}" probe/run.sbatch "$task_env" "$@"
}
```

控制面异常或有重复任务时先处理，不重复提交。以下各段在保留该函数及变量的同一 Bash 会话中执行。

## 20 张冒烟

单独写 `$EXP8/smoke20`。池取完整名单前 20 张（属于训练部分），几何另取 `pool_val` 前 20 张，因此预测共 40 张。冒烟只验证运行链路，不能冻结；若该 20 张验证图没有合格无信号目标，换新冒烟目录扩大样本，不替换尺度。`--limit` 在分片前截取；报告子划分取这个池子集与 split 的交集。

```bash
SMOKE="$EXP8/smoke20"
c=$(submit19 b19-smoke-compare 00:20:00 6G 1 exp5 '' "$STAGE/exp8_compare.py" --out "$SMOKE")
m=$(submit19 b19-smoke-manifest 00:10:00 4G 0 exp5 '' "$STAGE/exp8_prepare.py" \
  manifest --dataset sysu_val --out "$SMOKE")
p=$(submit19 b19-smoke-predict 00:20:00 6G 1 exp5 "$c:$m" "$STAGE/exp8_prepare.py" \
  predict --dataset sysu_val --limit 20 --out "$SMOKE")
pv=$(submit19 b19-smoke-valpred 00:20:00 6G 1 exp5 "$p" "$STAGE/exp8_prepare.py" \
  predict --dataset sysu_val --scope pool_val --limit 20 --out "$SMOKE")
s=$(submit19 b19-smoke-sam3 00:30:00 16G 1 omniovcd "$p" "$STAGE/exp8_sam3.py" \
  --dataset sysu_val --limit 20 --out "$SMOKE")
a=$(submit19 b19-smoke-auto 00:30:00 12G 1 exp5 "$m" "$STAGE/exp8_prepare.py" \
  automatic --dataset sysu_val --limit 20 --out "$SMOKE")
g=$(submit19 b19-smoke-geometry 00:20:00 4G 0 exp5 "$pv" "$STAGE/exp8_prepare.py" \
  geometry --dataset sysu_val --limit 20 --out "$SMOKE")
gm=$(submit19 b19-smoke-median 00:10:00 4G 0 exp5 "$g" "$STAGE/exp8_prepare.py" \
  geometry --dataset sysu_val --limit 20 --merge --out "$SMOKE")
u=$(submit19 b19-smoke-pieces 00:30:00 4G 0 exp5 "$s" "$STAGE/exp8_pieces.py" \
  --dataset sysu_val --limit 20 --out "$SMOKE")
um=$(submit19 b19-smoke-pieces-merge 00:10:00 4G 0 exp5 "$u" "$STAGE/exp8_pieces.py" \
  --dataset sysu_val --limit 20 --merge --out "$SMOKE")
pool=$(submit19 b19-smoke-pool 01:00:00 4G 0 exp5 "$gm:$a:$um" "$STAGE/exp8_pool.py" \
  --dataset sysu_val --limit 20 --out "$SMOKE")
r=$(submit19 b19-smoke-report 00:15:00 4G 0 exp5 "$pool" "$STAGE/exp8_report.py" \
  aggregate --dataset sysu_val --limit 20 --out "$SMOKE")
submit19 b19-smoke-verify 00:30:00 4G 0 exp5 "$r" "$STAGE/exp8_verify.py" \
  --dataset sysu_val --limit 20 --out "$SMOKE"
```

查看 `smoke20/pool/sysu_val/report.md`、`verification_0of1.json`、`timing/sysu_val_automatic_0of1.json` 后，再按实测耗时调整正式任务时限。

## 全量预测、SAM3、U4、6a 单元与验证池

```bash
c=$(submit19 b19-compare 00:20:00 6G 1 exp5 '' "$STAGE/exp8_compare.py" --out "$EXP8")
m=$(submit19 b19-manifest 00:10:00 4G 0 exp5 '' "$STAGE/exp8_prepare.py" \
  manifest --dataset sysu_val --out "$EXP8")
pred_deps=''
auto_deps=''
pieces_deps=''
for shard in 0 1 2 3; do
  p=$(submit19 "b19-predict-$shard" 01:00:00 6G 1 exp5 "$c:$m" "$STAGE/exp8_prepare.py" \
    predict --dataset sysu_val --shard "$shard/4" --out "$EXP8")
  pred_deps="${pred_deps:+$pred_deps:}$p"
  s=$(submit19 "b19-sam3-$shard" 04:00:00 16G 1 omniovcd "$p" "$STAGE/exp8_sam3.py" \
    --dataset sysu_val --shard "$shard/4" --out "$EXP8")
  a=$(submit19 "b19-auto-$shard" 02:00:00 12G 1 exp5 "$m" "$STAGE/exp8_prepare.py" \
    automatic --dataset sysu_val --shard "$shard/4" --out "$EXP8")
  auto_deps="${auto_deps:+$auto_deps:}$a"
  u=$(submit19 "b19-pieces-$shard" 03:00:00 4G 0 exp5 "$s" "$STAGE/exp8_pieces.py" \
    --dataset sysu_val --shard "$shard/4" --out "$EXP8")
  pieces_deps="${pieces_deps:+$pieces_deps:}$u"
done
g=$(submit19 b19-geometry 01:00:00 4G 0 exp5 "$pred_deps" "$STAGE/exp8_prepare.py" \
  geometry --dataset sysu_val --out "$EXP8")
gm=$(submit19 b19-median 00:15:00 4G 0 exp5 "$g" "$STAGE/exp8_prepare.py" \
  geometry --dataset sysu_val --merge --out "$EXP8")
um=$(submit19 b19-pieces-merge 00:30:00 4G 0 exp5 "$pieces_deps" "$STAGE/exp8_pieces.py" \
  --dataset sysu_val --merge --out "$EXP8")
ac=$(submit19 b19-check-val-auto 00:20:00 4G 0 exp5 "$auto_deps" "$STAGE/exp8_prepare.py" \
  check-automatic --dataset sysu_val --out "$EXP8")
pool_deps=''
for shard in 0 1 2 3; do
  p=$(submit19 "b19-val-pool-$shard" 04:00:00 4G 0 exp5 "$gm:$um:$ac" "$STAGE/exp8_pool.py" \
    --dataset sysu_val --shard "$shard/4" --out "$EXP8")
  pool_deps="${pool_deps:+$pool_deps:}$p"
done
report_deps=''
for scope in all pool_train pool_val; do
  r=$(submit19 "b19-report-$scope" 00:45:00 6G 0 exp5 "$pool_deps" "$STAGE/exp8_report.py" \
    aggregate --dataset sysu_val --scope "$scope" --out "$EXP8")
  report_deps="${report_deps:+$report_deps:}$r"
done
v=$(submit19 b19-val-verify 04:00:00 4G 0 exp5 "$report_deps" "$STAGE/exp8_verify.py" \
  --dataset sysu_val --out "$EXP8")
f=$(submit19 b19-freeze 00:10:00 4G 0 exp5 "$v" "$STAGE/exp8_report.py" \
  freeze --dataset sysu_val --out "$EXP8")
```

几何也支持 `--shard i/n`，合并需等待全部几何分片。它固定使用 800 张，`--scope` 仅控制 predict/automatic，不能改变几何样本。训练部分报告不参与选参。

## SYSU test：冻结后建池、报告与验收

以下承接上面的冻结作业变量 `$f`。先查询 U4 作业状态：若两者已成功完成且已退出可依赖的作业保留窗口，将 `TEST_U4_DEP` 设为空，由全量缓存检查确认；若仍在运行，保留下列依赖；若失败，先恢复原缓存任务。本批不生成 test U4。

```bash
sacct -j 153155,153156 --format=JobID,State,Elapsed,MaxRSS
TEST_U4_DEP=153155:153156
check=$(submit19 b19-check-test-auto 00:20:00 4G 0 exp5 "$TEST_U4_DEP" "$STAGE/exp8_prepare.py" \
  check-automatic --dataset sysu --out "$EXP8")
test_deps=''
for shard in 0 1 2 3; do
  t=$(submit19 "b19-test-pool-$shard" 04:00:00 4G 0 exp5 "$f:$check" "$STAGE/exp8_pool.py" \
    --dataset sysu --shard "$shard/4" --out "$EXP8" --frozen "$EXP8/frozen/sysu_val.json")
  test_deps="${test_deps:+$test_deps:}$t"
done
tr=$(submit19 b19-test-report 00:45:00 6G 0 exp5 "$test_deps" "$STAGE/exp8_report.py" \
  aggregate --dataset sysu --out "$EXP8")
submit19 b19-test-verify 04:00:00 4G 0 exp5 "$tr" "$STAGE/exp8_verify.py" \
  --dataset sysu --out "$EXP8"
```

测试输出为 `$EXP8/pool/sysu/`。测试报告与验收同样检查冻结文件和单元列表；只有一个 `U5_x*`。标签仅用于最终单元标注、覆盖率及 oracle 评价，不进入冻结选择。

## 产物与后续 6b 使用

- `compatibility/`：8 张预测比对及逐像素差异报告；正式 val 预测要求比对通过。
- `exp6/sysu_val/manifest.json`、`pool_train.txt`、`pool_val.txt`：完整固定划分；另写 6b 接口别名清单。
- `preds/sysu_val/schanger_small/{prob,mask}`：4,000 张官方预测。
- `sam3/sysu_val/`、`sam3_work/sysu_val/<i>of<n>/`：新增缓存与可续跑的分片工作目录；已有缓存只读。
- `automatic/sysu_val/`、`timing/`：4,000 张 U4 分区及耗时。测试 U4 位于 batch18。
- `geometry/sysu_val/<image>.json`、`geometry/sysu_val.json`：仅 800 张验证图的面积记录及中位数。U0 固定尺度直接记录在各池配置，不生成训练中位数文件。
- `exp6/pool/sysu_val/masks/`、`images/`、`pieces.jsonl`、`pieces_summary.json`、`compatibility.json`：完整 6a 补漏/预测单元、标签及元数据。预测块用于后续 6b，未计入本批补漏 oracle。
- `pool/sysu_val/{U0,U4,objects_*,U5_x4,U5_x8,U5_x16}/` 与逐图 `images/`：完整保存各单元掩码和来源/成员、面积、标签、指标。U0 与 6a recall 掩码逐块一致。
- `pool/sysu_val/summary{,_pool_train,_pool_val}.json`、对应 `report*.md`：全部及两个子划分的独立汇总。
- `frozen/sysu_val.json`、`pool/sysu/`、`verification_0of1.json`：冻结选择、最终测试池和验收记录。

后续 6b 可在计算作业中导入本目录 `exp8_verifier`，调用 `open_pool(out)` 和 `verifier_split(out)`。`training_module(out)` 将原训练入口的固定 Pool 调用绑定为 SYSU val 的真实 Pool；本批没有调用训练函数。U0 的所有单元（包括中间标签）和原预测块均保留，后续无需重新运行 SLIC。

## 资源估计

CPU pool 使用每作业 1 核、4G，汇总 6G。进入本任务前已有 README 记录 pool 约 1.4G、U4 约 7G 的内存估计，但未附可复核的 MaxRSS；本次所读 SECOND/WHU 日志也没有 MaxRSS 行，因此不把这些数值当成本次实测。上文给出相关 Job ID 的 `sacct` 命令，提交时可按核对结果调整。没有申请 batch18 早期模板中普遍使用的 8–12G CPU pool 内存。

| 阶段 | 依据和预计耗时 | 资源与时限 |
|---|---|---|
| SChanger 4,000 张 | 旧 SYSU 报告 442.4 秒，约 0.123 GPU 小时；预留启动和读盘时间 | 每片 1 GPU、2 CPU、6G，4 片各 1 小时 |
| SAM3 4,000 张 | 旧 SYSU 报告 3,763.6 秒，约 1.05 GPU 小时；10 秒/图保守预算为 11.11 GPU 小时 | 每片 1 GPU、2 CPU、16G，4 片各 4 小时 |
| val U4 4,000 张 | batch18 SYSU 20 张均值 2.7017 秒/图，外推约 3.00 GPU 小时；加 25% 为 3.75 小时 | 每片 1 GPU、2 CPU、12G，4 片各 2 小时 |
| test U4 | 只读原作业缓存 | 本批新增 GPU 时间为 0 |
| 6a / pool | 未实测本批 SYSU；已有 SECOND 日志约数秒/图、WHU 常为 0.4–1.7 秒/图，仅供预算参考 | 每片 1 CPU、4G；6a 3 小时、pool 4 小时 |
| 几何 / 合并 / 汇总 / 验收 | 800 张几何；4,000 张掩码回读；未测实际峰值和耗时 | 1 CPU，几何/验收 4G，汇总 6G；时限见命令 |

GPU 时间按累计用时估计，分片只减少墙钟时间；SAM3 的保守估计仍低于 12 GPU 小时。U4 计时不含模型加载和写盘。正式时限和内存最终参考真实冒烟及既有作业账目。

## 失败处理与验收边界

缺文件、几何来源不符、配置变化、未通过预测比对、缺少冻结文件均明确失败。每图池 JSON 在所有 NPZ 写入成功后作为完成标志；同参数重新提交可续跑。SAM3 复用分片工作缓存；变更分片安排前先停止原分片，避免同图并发。改变配置或从冒烟转全量使用新目录，旧 batch19-v1 的尺度和配置不能续用。

回读验收核对 NPZ 形状、单元数、面积、真实标签和 oracle TP/FP/FN；6a 记录与 U0 掩码一致；所有图在 split 中，3,200/800 名单互斥，同一原图块不跨划分；两个子划分计数之和等于全池；test 四种去重计数与 batch17 完全一致，且 U5 仅有冻结倍数。

测试标签的代码路径：manifest 只检查 val 文件存在；val 预测只读取注册为 `sysu_val` 的标签；SAM3/U4 不读标签；几何固定 `data.image_names(scope='pool_val')` 并显式调用 `load_label('sysu_val', name)`；pieces 仅接受 `sysu_val`；选参只读 `summary_pool_val.json`。预测一致性比对读取旧预测掩码，完全不读 test 标签。最终 `--dataset sysu` 的 pool/verify 才读取 test 标签用于评价。本次静态交付没有执行这些标签加载；后续验收中的 `test_gt_used_for_selection=false` 表达此流程边界，不表示最终测试评价不需要标签。历史 U0 的 972 来源不属于本批标签读取。

本次静态检查命令：

```bash
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch19
python -B static_check.py
```

已通过：11 个 Python 文件的解析与编译、5 个 Bash 代码块语法、10 个上游源码接口、真实 split 的 3,200/800 图和 128/32 原图块检查，以及几何标签路径与比对参考适配的静态核对。记录见 `static_checks.txt`。这些检查没有执行实验模块。

未执行的验证：实验模块实际导入、GPU 比对、全量推理、6a/U4/U5 计算、真实掩码回读及冻结；原因是 TASK 明确要求仅做语法与静态检查。实际 U5 中位数、倍数和分数尚未产生。

待确认项：8 张预测比对能否满足既定容差；实际运行时的 MaxRSS 和耗时；batch18 U4 作业完成状态及全量缓存可读性；GPU 环境的实际导入与执行。官方权重是否用 val 选优尚无确定证据，已按用过记录；32 个原图块的限制保留在所有报告中。
