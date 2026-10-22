# batch24：SYSU 第 3 步训练、冻结评估与级联统计

本批交付 SYSU U5 full / clean、检测器概率规则和新训 U0 V1 none 的执行代码。此次仅在 batch24 写文件，完成静态检查；未提交或查询 Slurm，未联网、安装包、导入实验模块或运行训练/评估。当前目录不是可用的 Git 工作树，无法取得 Git diff。`TASK.md` 保持原样。

## 文件和复用关系

| 文件 | 用途 |
|---|---|
| `exp8_config.py` | SYSU 路径、划分、来源、冻结文件、15/11 维特征定义、测试前置条件 |
| `exp8_reuse.py` | 每次运行读取当前 batch22 源码，核对并应用小范围接口补丁，然后按模块导入 |
| `exp8_bootstrap.py` | 先装载 SYSU config，再导入 batch22 features/fit/evaluate；适配 batch21 清洗编号及报告文字 |
| `exp8_u0.py` | batch19 `open_pool` / 6b Pool 的 SYSU U0 适配、B/G0.05 过滤、原始 piece_id 和 6a 掩码读取 |
| `exp8_fit.py` | `--variant full/clean/u0`，调用 batch22 原训练循环 |
| `exp8_evaluate.py` | `--pool u5/u0` 的 calibrate / validation / test / report，调用 batch22 原算法 |
| `exp8_report.py` | 联合 freeze；将两种单元的报告合并成四评分器同表，输出完整 A–E 附表 |
| `static_check.py` / `static_checks.txt` | 标准库静态检查及本次实际记录；不导入或执行实验模块 |

没有复制 batch22 的特征提取、MLP 优化、阈值搜索、像素并集、A–E 或级联算法。每次入口读取相邻 `batch22` 的当前代码，未涉及补丁位置的修复自动带入。补丁位置发生变化时明确失败，需检查接口再更新适配，不忽略变化。所有替换只在当前 Python 进程生效，不改 batch19/20/21/22 或 probe。

batch22 中需要本地适配的固定条件逐项如下：

1. `exp8_features` 的额外特征数组宽度固定为 8：改由 config 提供；U5 为 4，U0 为 0。其余提取和批次算法原样导入。
2. `cleaned_selection` 固定读取 batch20 路径和单元：替换为 batch21 `selected.json`、所选规则对应的 `selected_training_ids/<rule>/<unit>.jsonl`；核对划分、来源记录、原始 archive、监督标签和编号。
3. `exp8_fit` 的 variant 仅有 full/clean：U0 进程提供 `('u0',)`。U0 复用相同训练循环和原 6b `Features(backbone='none')` 的 11 维输入，不加来源个数或组合。
4. `exp8_evaluate` 的模型列表固定 full/clean、默认带 detector：改由 config 提供，U0 仅有一个新训评分器。U5 保留三种评分器。
5. SECOND 旧 U0 引用：移除 aggregate 中的历史引用调用，SYSU 使用本批新训 U0 的完整结果；不访问旧 SECOND 结果。
6. 报告 SECOND 标题和固定并列说明：改为 SYSU 及配置顺序；附加实际研究限制和级联 ΔIoU 表。原报告已有 ΔF1、完整附表和 JSON 中的 ΔIoU。
7. U0 原始 `piece_id` 并非过滤后连续编号：缓存和评估分数保留原编号，按编号读历史 6a 掩码。U5 保持原 NPZ 的从 0 开始编号。
8. 测试入口增加联合冻结/联合验证报告检查，确保 U0 与 U5 都在第一次测试之前登记。

SYSU 池读取复用 batch21 common，其内部先注册 batch19 runtime/pool 再复用 batch20 检查和 batch16 指标。6b 来自 `/guisongxia01/pangchao/wangyihan/Chang-Agent/probe`。本入口与原 SECOND CLI 应在各自独立进程执行。

## 输入和输出

以下路径相对 `/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/`：

- U5 训练和验证：`exp8_batch19/pool/sysu_val/`，同一完整池按原图块 split 划为 3,200 / 800 张，逐图核对 `pool_split`。split 来自 `wyh/CD-Agent/interim/exp8/sysu_val/split.json`，128 / 32 个原图块，每块 25 张。
- U5 测试：`exp8_batch19/pool/sysu/`，4,000 张。每次运行读取 `exp8_batch19/frozen/sysu_val.json`，从 multiplier 得到单元名，不写死所选倍数。2026-10-22 本次文件检查为 **U5_x4，阈值 4,056 px**。
- clean：`exp8_batch21/cleaning/selected.json`。本次检查所选规则为 **R1**，代码按其中规则名、`main_unit` 和 `training_ids` 定位，不写死 R1。
- 原检测器：SChanger-small。train/val 读 `exp8_batch19/preds/sysu_val/schanger_small/`，test 读公共 `preds/sysu/schanger_small/`。二值标签由原 SYSU loader 读取。
- U0 训练/验证：`exp8_batch19/exp6/pool/sysu_val/` 的 6a 元数据与掩码。经 batch19 `open_pool`、`verifier_split` 核对同一划分，仅保留 B、G0.05；full U0 不清洗。测试只读历史 `exp6/pool/sysu/`，过滤相同两来源。
- U0 none 不执行 `exp6b_features.py` 的图像骨干提取；该流程的 none 输入实际由 `exp6b_common.Features` 提供。复用其 Pool、标准化、MLP、预测、分类指标及 `exp6b_train.weighted_bce`；像素搜索复用 batch22 所调用的 `exp6b_eval.curve_counts`。不修改 6b 默认行为。

所有入口的 `--out` 都接收批次输出根目录。fit/evaluate 自动追加 `u5/` 或 `u0/`；联合 report 不追加。可覆盖 `--batch19`、`--batch21`、`--u0-test`（历史 exp6 根目录）。同一次运行须保持一致。

正式根目录 `outputs/CD_1016_probe/exp8_batch24/`；冒烟根目录 `outputs/CD_1016_probe/exp8_batch24_smoke20/`。这些是后续运行产物位置，本次未创建。

- `u5/`、`u0/` 各含 `protocol.json`、`features/{train,val}/`、`models/`、`curves/`、冻结文件及 `evaluation/{val,test}/`。
- 三份模型目录为 `u5/models/full/`、`u5/models/clean/`、`u0/models/u0/`，各含逐 epoch `train_log.jsonl`、`training.json`、`validation_scores.npz`；仅正式训练保存 `model.pt`。
- 根目录 `frozen.json` 登记四方案及全部操作阈值、级联阈值和主方案；各单元目录也有自己的冻结文件。冒烟统一使用 `smoke_calibration.json`，不能用于测试。
- 每个评估目录保存逐图 JSON、分数、像素分数直方图和 `summary.json`、`report.md`、`appendix.json`、`appendix_micro.csv`。根目录 `evaluation/<split>/` 将 U5 和 U0 合并到同一主表和附表；逐图原值在对应 `u5/`、`u0/` 下。

## 训练与选择口径

U5 为 **15 维**：6b none 的检测器概率 mean/max/min/p90、log(area)、六个来源位（11维），再加来源数、B/G0.05/B+G0.05 三个组合位（4维）。当前来源只有 B、G0.05，其余来源位恒为 0。U0 为原 none 的 **11 维**，单一来源位保留，其余组合特征不加入。真值与输入特征分开存放。

训练完全沿用 batch22/6b：512→256→1 MLP，ReLU，dropout 0.15，AdamW，lr=0.001，weight_decay=0.0001，加权 BCE，默认 seed=1020、batch_size=2048、最多50 epoch。每个版本分别使用自己的监督训练单元计算标准化和负/正样本权重。标签正例 ≥0.5、负例 <0.1，中间单元不参加监督，保留在推理、像素阈值和工作量统计中。clean 的验证仍使用完整 pool_val。

按任务要求保留 batch22 的 **验证 AP 早停**（7 epoch 无提升，AP 精确并列保留较早 epoch），用于保存模型训练状态。**操作阈值和四方案主方案仅按验证像素 F1 / IoU 选择**；其他评估指标不参与这两项选择。每个 epoch 立即刷新完整监督训练/验证的 eval-mode loss、AUC、AP、ROC80/90、训练优化 loss、best_epoch 和耗时；训练监控不抽样。

四个评分器为 full、clean、detector（U5 单元内平均概率）和 u0。各自在全部验证分数断点上计算接受单元并集，扣除原预测后累计新增 TP/FP，选择 micro F1 最大阈值，再以 IoU、更高阈值打破并列。联合主方案再按其验证 F1/IoU 选择，完全并列依次 full、clean、detector、u0。没有新增像素的候选也在搜索范围内。

每个评分器同时冻结验证召回至少80%的最高阈值作对照。测试只应用冻结阈值，报告实际召回、FPR 和新增像素精度。新增像素精度按接受掩码并集减原预测计数，与**同一划分实际原预测的 F1/2**比较，SYSU 约42%仅作背景参考，不写成常量。AUC/AP 及当前划分 ROC80 排序诊断保留；测试 ROC80 使用同一次推理保存的直方图描述，不据此重选端到端阈值。

A–E 直接复用 batch16 全部实现：像素、BF d1/2/4/5、Boundary IoU、目标面积桶、拆分/粘连、粗糙度、碎片、逐图变化和有害编辑率。U0 与 U5 同口径计算。所有完整评估完成后才能生成汇总，不跳过缺图。

级联为 q10/q25/q50 下阈值 × q75/q90/q95 上阈值，共9组/评分器；分位数仅在验证全部单元分数上求值并冻结。score > upper 接受，score < lower 丢弃，两边界包含在不确定区间。报告每图不确定均值/P50/P90、正负中间数量、两种分母的正例率、仅并入接受部分的 ΔF1/ΔIoU。GPU小时按不确定数×0.44/3600，并按每图均值外推到 **4,000 张**，不含模型加载、裁块和通信。本批不调用 MLLM。

## 后续提交命令（本次未执行）

使用既有 `conda_envs/exp5-env/bin/python` 和 NumPy、PyTorch、Pillow、SciPy、scikit-image、torchange 及 probe 本地依赖。设备默认 CPU，无需 GPU。正式运行前须确认 batch19 完整池回读验收和 batch21 选择产物已完成，再执行真实冒烟。下面沿用 batch22 的 `submit22` 写法，改名 `submit24`。`probe/run.sbatch` 提供成功/失败 QQ 通知；日志归入输出根目录的 `logs/`。

```bash
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch24
FINAL_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch24
SMOKE_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch24_smoke20
scontrol ping
squeue -u guisongxia
sinfo -p gre
submit24() {
  local task_name=$1 task_time=$2 task_mem=$3 task_dep=$4 task_out=$5
  shift 5
  local -a task_extra=()
  if [[ -n "$task_dep" ]]; then task_extra+=(--dependency="afterok:$task_dep"); fi
  mkdir -p "$task_out/logs"
  sbatch --parsable --partition=gre --exclude=gpu04,gpu06,gpu20,gpu37 \
    --export=ALL,JOB_TMP_ROOT=/tmp,PYTHONDONTWRITEBYTECODE=1 \
    --job-name="$task_name" --cpus-per-task=1 --mem="$task_mem" --time="$task_time" \
    --output="$task_out/logs/%x-%j.out" --error="$task_out/logs/%x-%j.err" \
    "${task_extra[@]}" probe/run.sbatch exp5 "$@" --out "$task_out"
}
```

### 20图真实冒烟

训练和验证各取自己划分前20张，三份模型各2 epoch。使用正式完整输入池和清洗选择；不读测试池，不保存 checkpoint，不产生正式 frozen.json。不要将已有正式输出根目录当作冒烟目录。

```bash
a=$(submit24 b24-smoke-full 00:30:00 4G '' "$SMOKE_OUT" \
  "$STAGE/exp8_fit.py" --variant full --device cpu --limit 20 --epochs 2)
b=$(submit24 b24-smoke-clean 00:30:00 4G "$a" "$SMOKE_OUT" \
  "$STAGE/exp8_fit.py" --variant clean --device cpu --limit 20 --epochs 2)
c=$(submit24 b24-smoke-u0 00:30:00 4G "$b" "$SMOKE_OUT" \
  "$STAGE/exp8_fit.py" --variant u0 --device cpu --limit 20 --epochs 2)
cal_deps=''
for pool in u5 u0; do
  q=$(submit24 "b24-smoke-cal-$pool" 00:20:00 4G "$c" "$SMOKE_OUT" \
    "$STAGE/exp8_evaluate.py" calibrate --pool "$pool" --limit 20)
  cal_deps="${cal_deps:+$cal_deps:}$q"
done
f=$(submit24 b24-smoke-freeze 00:10:00 4G "$cal_deps" "$SMOKE_OUT" \
  "$STAGE/exp8_report.py" freeze --limit 20)
val_deps=''
for pool in u5 u0; do
  v=$(submit24 "b24-smoke-val-$pool" 01:00:00 4G "$f" "$SMOKE_OUT" \
    "$STAGE/exp8_evaluate.py" validation --pool "$pool" --limit 20)
  r=$(submit24 "b24-smoke-report-$pool" 00:20:00 4G "$v" "$SMOKE_OUT" \
    "$STAGE/exp8_evaluate.py" report --pool "$pool" --split val --limit 20)
  val_deps="${val_deps:+$val_deps:}$r"
done
submit24 b24-smoke-joint 00:15:00 4G "$val_deps" "$SMOKE_OUT" \
  "$STAGE/exp8_report.py" report --split val --limit 20
```

冒烟验收：三份训练轨迹各2行，训练/验证 loss 和分类指标完整，U5 输入15维、U0输入11维；无 model.pt、正式 frozen.json 或 test 结果；联合主表含四评分器各两个策略，36组级联，U0/U5 原预测统计一致；新增像素 TP/FP、F1/IoU、F1/2、A–E 和 CSV 可回读。若任一监督子集没有两种标签，明确失败；使用新输出目录并扩大冒烟图数后重跑。

### 正式训练、联合冻结、完整验证和单次测试

先通过冒烟，再按实测时间和内存调整预算。测试可按固定分片续跑，已完成逐图记录与分数存在且 scheme_id 相同则跳过。变更模型或参数须使用新的实验目录，不能根据本次测试结果再次调参覆盖当前结果。

```bash
a=$(submit24 b24-full 08:00:00 4G '' "$FINAL_OUT" \
  "$STAGE/exp8_fit.py" --variant full --device cpu)
b=$(submit24 b24-clean 08:00:00 4G "$a" "$FINAL_OUT" \
  "$STAGE/exp8_fit.py" --variant clean --device cpu)
c=$(submit24 b24-u0 08:00:00 4G "$b" "$FINAL_OUT" \
  "$STAGE/exp8_fit.py" --variant u0 --device cpu)
cal_deps=''
for pool in u5 u0; do
  q=$(submit24 "b24-cal-$pool" 02:00:00 4G "$c" "$FINAL_OUT" \
    "$STAGE/exp8_evaluate.py" calibrate --pool "$pool")
  cal_deps="${cal_deps:+$cal_deps:}$q"
done
f=$(submit24 b24-freeze 00:15:00 4G "$cal_deps" "$FINAL_OUT" \
  "$STAGE/exp8_report.py" freeze)
val_deps=''
for pool in u5 u0; do
  shards=''
  for shard in 0 1 2 3; do
    v=$(submit24 "b24-val-$pool-$shard" 04:00:00 4G "$f" "$FINAL_OUT" \
      "$STAGE/exp8_evaluate.py" validation --pool "$pool" --shard "$shard/4")
    shards="${shards:+$shards:}$v"
  done
  r=$(submit24 "b24-val-report-$pool" 01:00:00 4G "$shards" "$FINAL_OUT" \
    "$STAGE/exp8_evaluate.py" report --pool "$pool" --split val)
  val_deps="${val_deps:+$val_deps:}$r"
done
e=$(submit24 b24-val-joint 00:30:00 4G "$val_deps" "$FINAL_OUT" \
  "$STAGE/exp8_report.py" report --split val)
test_deps=''
for pool in u5 u0; do
  shards=''
  for shard in 0 1 2 3 4 5 6 7; do
    t=$(submit24 "b24-test-$pool-$shard" 08:00:00 4G "$e" "$FINAL_OUT" \
      "$STAGE/exp8_evaluate.py" test --pool "$pool" --shard "$shard/8")
    shards="${shards:+$shards:}$t"
  done
  r=$(submit24 "b24-test-report-$pool" 01:00:00 4G "$shards" "$FINAL_OUT" \
    "$STAGE/exp8_evaluate.py" report --pool "$pool" --split test)
  test_deps="${test_deps:+$test_deps:}$r"
done
submit24 b24-test-joint 00:30:00 4G "$test_deps" "$FINAL_OUT" \
  "$STAGE/exp8_report.py" report --split test
```

训练中断后保留轨迹，使用新输出目录重新训练；训练不会覆盖已启动模型目录。特征缓存和逐图评估支持相同协议续跑，缺文件或协议不一致明确失败。不同时运行两个训练写共享 U5 特征缓存，也不要多个评估任务处理同一图。失败时先查对应 `logs/`，仅重提未完成阶段。

## 资源预算和待确认项

| 阶段 | 建议资源与时限 | 依据 |
|---|---|---|
| 单份训练 | 1 CPU，4G，8小时 | 无骨干；最多50 epoch，实际早停与耗时待冒烟 |
| 校准 | 每单元1 CPU，4G，2小时 | 800图，全分数断点像素累计 |
| 完整验证 | 每单元4片，各1 CPU，4G，4小时 | 每片200图，多种接受掩码的完整 A–E |
| 单次测试 | 每单元8片，各1 CPU，4G，8小时 | 每片500图，U5三评分器和U0一评分器 |
| 单元汇总 / 联合报告 | 1 CPU，4G，1小时 / 30分钟 | 只汇总已有分数与逐图记录 |

预算不是实测。batch20/21 的约1.3G仅作参考，本批含 PyTorch，先按4G。U5 紧凑特征主体约每单元73字节；U0约57字节，另有全池6b元数据、分数和训练批次开销。此次读取元数据记录 U0 官方val总池232,252块、历史test总池499,475块（含未用于本实验的来源/预测块）；内存缓存仅元数据，掩码逐图读取。以冒烟逐图 `seconds` 和后续作业 MaxRSS 校正资源，完整 A–E 通常比MLP推理更耗时。

待确认：

1. 实际实验模块导入、三份训练、真实掩码回读、阈值/级联/A–E 的完整链路和峰值内存尚未执行。本次 TASK 仅允许静态检查，禁止 Slurm。
2. batch19/batch21 完整产物验收状态需在后续正式运行前核对；本次检查真实配置和部分逐图元数据，不能据此宣称完整池验收通过。
3. 官方 SChanger-small 权重可能用官方 val 选过，按已使用记录；验证部分仅32个原图块，800张小图存在块内相关。U5倍数、清洗规则、训练早停和操作方案重复使用这部分验证数据，结论须保留此限制。
4. U0 972px 的历史尺度源于旧测试统计，作为既定对照沿用；本批不据测试重新估计。新 V1 的 ΔF1 是否转正以及 SYSU/SECOND 是否一致，本次没有实验结果。

静态复查命令（仅标准库，不运行实验）：

```bash
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch24
python -B static_check.py
```
