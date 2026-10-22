# batch28：四个数据集的去误检

本目录交付 exp8 第4步的执行代码。本次仅做源码、语法和接口静态检查，未导入实验模块、运行真实冒烟、训练、复算指标或提交 Slurm；未联网或安装包。所有本次新增文件位于本目录，TASK.md 和 batch16–27、probe 的文件保持原样。当前目录不是可用的 Git 仓库，无法提供 Git 差异。

## 文件

| 文件 | 用途 |
|---|---|
| `fp_common.py` | 四数据集路径和划分；原池 pred 切块读取；U4 对象交集；逐图掩码及特征缓存；6b 训练接口适配 |
| `fp_fit.py` | SECOND、SYSU 对象单元的 CPU MLP 训练，完整逐 epoch 轨迹、最佳模型和验证分数 |
| `fp_run.py` | 历史复核、准备、阈值冻结、验证/测试、中文主表、边界第二表和 A–E 附表 |
| `static_check.py` | 标准库 AST 语法和已解析函数签名检查，README Bash 语法检查；不执行实验代码 |
| `static_checks.txt` | 本次静态检查的实际输出 |

解释器沿用 `/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python`，依赖已有 NumPy、PyTorch、Pillow、SciPy、scikit-image 及本地模块。使用 batch18 `split_objects` / 单元存储、batch16 `metrics_cd.py`、6b MLP/标准化/分类指标，以及 batch19 的 SYSU 分组清单。本批禁写依赖目录中的字节码。

## 划分和输入

下面路径均相对于 `/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/`。数据图片和标签沿用 `probe/common.py`。

| 数据集 | 训练 / 验证 / 测试 | 预测 | 自动划分 |
|---|---|---|---|
| SECOND | 2671 / 297 / 1694，`exp6b_common.verifier_split()` | `preds/{second_cv,second}/mambascd_base` | train：`exp8_batch20/automatic/second_cv_train`；val/test：`exp7_batch16/automatic/{second_cv,second}` |
| SYSU | batch19 `pool_train` 3200 / `pool_val` 800 / 4000 | train/val：`exp8_batch19/preds/sysu_val/schanger_small`；test：`preds/sysu/schanger_small` | train/val：`exp8_batch19/automatic/sysu_val`；test：`exp8_batch18/automatic/sysu` |
| LEVIR | 无训练 / 官方val 64 / test 128 | val：`exp8_batch18/preds/levir_val/schanger_base`；test：`preds/levir/schanger_base` | `exp8_batch18/automatic/{levir_val,levir}` |
| WHU | 无训练 / 无验证 / test 690 | `preds/whu/schanger_base` | `exp8_batch18/automatic/whu` |

SYSU 的3200/800划分直接读取 batch19 `exp8_data.py`，核对原航片组完整且不跨划分。官方 SChanger-small 权重是否用过官方 val 选优无法确认，按已用过处理；800张验证小图来自32个原图组。

SLIC 单元直接读 `exp6/pool/{second_cv,second,sysu,levir,whu}/{pieces.jsonl,pieces_summary.json,masks}` 中 `kind=pred` 的切块。SYSU train/val 读取 `exp8_batch19/exp6/pool/sysu_val`。LEVIR val 没有既有 pred 池，用 `exp6a_pieces.geometry('levir')` 的训练尺度重建，参数与6e一致：六通道两期RGB、compactness=10、`n_segments=max(4,int(H*W/median))`，只切面积大于4倍尺度的8连通域，保留全部小切块。LEVIR/WHU 既有尺度文件位于 `exp6/geometry/{levir,whu}.json`。SECOND 298、SYSU 972像素是历史由测试集得到的既定尺度，本次保持原值并在报告中标明限制。

对象单元从保存的检测器0.5掩码按8连通取区域，与缓存 NPZ 的 `labels` 求交。自动划分是7b按两期SAM自动掩码生成的共同细分。复用 batch18 `split_objects`，交集≥10像素；预测域及交集互不重叠，所以按面积稳定排序即可得到与 `u4_units` 相同的结果，无需同时构造所有全图掩码。两期SAM均未覆盖、以及不足10像素的交集所对应的预测像素保留。

2026-10-22已检查上述池根目录、SYSU合并池清单、SECOND训练自动划分目录、LEVIR val预测目录和几何配置文件存在；未逐图检查所有输入的完整性。正式作业逐图读入并核对尺寸、单元覆盖、重叠和清单数量，缺失或不符直接失败。

## 历史复核及训练

`reference` 是后续流程的前置条件。正式模式先遍历 SECOND、SYSU 全部测试图，仅复算既有 SLIC oracle，并与旧 `exp6e/<dataset>/oracle.json` 的原始/删除后 TP、FP、FN、删除TP/FP和切块数逐项相等比较；同时核对报告 ΔF1 +5.74/+4.07（允许0.015个百分点的显示舍入误差）。SECOND 原池 pred 单元的检测器平均概率 AUC 必须与0.784相差不超过0.0005。任一失败均写出检查结果并停止，不能继续正式准备/训练/冻结。

oracle 严格沿用6e：面积≥10且真值占比<10%才删除；小切块保留。这一规定只约束 oracle。检测器/MLP 操作按全部单元的 `score < threshold` 删除；中间标签也参与推理。概率分数和MLP分数均解释为“保留”的置信度。历史复核中的测试标签只用于复现已有结果，不能选择参数；后续测试直接复用这些图的原预测和SLIC oracle指标，避免重评同一结果。

MLP 只在 SECOND、SYSU 对象单元上训练，不为 SLIC 另训模型，也不为 LEVIR/WHU 训练。特征共12维：6b none的概率mean/max/min/p90、log面积、六来源位（仅pred为1），再按任务要求追加单元平均概率。末维与首维数值相同，是明确保留的任务定义；不加入6b none原本不用的split位。真值只用于独立标签和评估，不进入特征。

标签≥50%为保留、<10%为删除，其余不参与监督损失与AUC/AP。标准化和正例权重仅统计监督训练单元。沿用batch22：512→256→1、ReLU、dropout=0.15、AdamW(lr=0.001, weight_decay=0.0001)、加权BCE、seed=1020、batch_size=2048、最多50 epoch，验证AP连续7 epoch未提升早停，AP相同保留较早epoch。每轮记录优化loss及**全部监督训练/验证单元**在eval模式下的loss、AUC、AP、ROC80/90、最佳epoch和耗时，逐行刷新。

检查点早停沿用batch22的验证AP；端到端阈值和方案选择仅使用验证像素F1/IoU。正式保存最佳checkpoint；冒烟只保存轨迹和最佳验证分数，不保存checkpoint。

## 阈值冻结与评估

- 每个单元/评分器遍历全部验证分数断点；使用浮点64位 `nextafter(score,+inf)` 表示严格小于阈值时刚好删除该分数组，另加0表示不删。因单元互斥，按删除TP/FP累计得到精确micro曲线，中间标签也在曲线内。
- 先最大化像素F1，再看IoU，完全相同取较低阈值。原预测“不删除”也参与验证方案比较。方案完全并列时按 baseline、slic_detector、object_detector、object_mlp 顺序选择。
- SECOND/SYSU 冻结两种单元的概率规则和对象MLP；LEVIR冻结两种概率规则。WHU分别迁移对应LEVIR val阈值，同时报告不删除基线。WHU的冻结验证指标属于LEVIR val，不能解读为WHU验证成绩。
- `frozen.json` 保存全部对照、主方案、阈值、模型run_id、训练/验证/测试图像清单和限制。冒烟仅写 `smoke_calibration.json`。
- 验证必须生成完整报告，且逐图真实掩码指标与阈值曲线一致后，才能运行测试。WHU依赖LEVIR验证报告。测试按冻结阈值一次评估全部方案；不可用测试结果改阈值或重选主方案。
- 已完成逐图JSON和分数NPZ会复用；测试中断可继续尚未完成图。单图在完成标记写入前中断可能重做。模型或协议改变须开新输出目录，不能覆盖已冻结测试。训练不支持断点续训。

每个数据集的主表包含每图单元数、oracle F1/IoU变化、操作阈值和来源、测试F1/IoU及变化、删除像素精度、AUC/AP。删除精度=删除FP/(删除TP+删除FP)，未删除时为null。AUC/AP仅用有标签单元，正类为保留。边界第二表紧随主表，含BF1/2/4/5、两种Boundary IoU及粗糙度。JSON中的比例为0–1，Δ字段明确使用百分点；Markdown中的指标显示百分数。LEVIR/WHU每个方案均标注 ΔF1≥−0.3 是否满足，包括基线和oracle。

A–E完整复用batch16：像素、边界、目标面积桶/拆分/粘连、粗糙度/碎片、逐图变化/有害编辑率。没有根据边界或目标指标选阈值。

分类汇总另存`all_units`和`excluded_middle`，明确分类指标的分母。`summary.json`在报告及附表生成成功后写入，作为允许后续测试的完成标记。

## 输出

- `protocol.json`：所有阶段共用协议。
- `reference/verification.json`、`reference/{second,sysu}/images/*.json`：历史复核、逐图原预测及SLIC oracle指标。冒烟标为partial且historical_verified=false。
- `units/<dataset>/<split>/<slic|object>/{images,masks,features}/`：逐图完成记录、压缩局部掩码、紧凑特征及独立标签/像素计数。
- `models/{second,sysu}/{train_log.jsonl,training.json,validation_scores/}`，正式另有`model.pt`。
- `curves/<dataset>/*.npz`、`frozen.json`或`smoke_calibration.json`。
- `evaluation/<dataset>/<val|test>/{images,scores}/`，以及 `summary.json`、`report.md`、`appendix_A_E.json`、`appendix_micro.csv`。
- `logs/`：Slurm stdout/stderr。每个长作业由沿用的`probe/run.sbatch`在成功或失败时发QQ通知，包含任务、状态、耗时和带`--out`的完整命令。

## 后续提交命令（本次未执行）

以下命令供以后获准运行时使用。先检查分区、重复作业和输入是否完整。沿用batch22的submit写法，函数改名`submit28`，CPU申请4G；不用GPU。

```bash
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch28
SMOKE_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch28_smoke
FINAL_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch28
scontrol ping
squeue -u guisongxia
sinfo -p gre
submit28() {
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

历史复核阶段只读取两个历史测试集各前20图，检查复核链路，**不会声称复现全量+5.74/+4.07或0.784**；其partial结果只能放行本冒烟目录。准备与训练取各训练/验证划分前20图，两个epoch；评估只跑验证，不运行冻结测试，不保存checkpoint。若有标签集合只有一个类别，明确失败，应在正式测试前增加冒烟规模并使用新输出目录。

```bash
r=$(submit28 b28-smoke-reference 01:00:00 4G '' "$SMOKE_OUT" \
  "$STAGE/fp_run.py" reference --limit 20)
p=$(submit28 b28-smoke-prepare 02:00:00 4G "$r" "$SMOKE_OUT" \
  "$STAGE/fp_run.py" prepare --limit 20)
a=$(submit28 b28-smoke-second 01:00:00 4G "$p" "$SMOKE_OUT" \
  "$STAGE/fp_fit.py" --dataset second --epochs 2 --limit 20)
b=$(submit28 b28-smoke-sysu 01:00:00 4G "$a" "$SMOKE_OUT" \
  "$STAGE/fp_fit.py" --dataset sysu --epochs 2 --limit 20)
f=$(submit28 b28-smoke-freeze 00:30:00 4G "$b" "$SMOKE_OUT" \
  "$STAGE/fp_run.py" calibrate --limit 20)
for dataset in second sysu levir; do
  v=$(submit28 "b28-smoke-$dataset-val" 02:00:00 4G "$f" "$SMOKE_OUT" \
    "$STAGE/fp_run.py" evaluate --dataset "$dataset" --split val --limit 20)
  submit28 "b28-smoke-$dataset-report" 00:30:00 4G "$v" "$SMOKE_OUT" \
    "$STAGE/fp_run.py" report --dataset "$dataset" --split val --limit 20
done
```

验收：两个模型均有2轮完整train/val轨迹；没有`model.pt`或正式`frozen.json`；三份验证报告各覆盖20图；真实删除掩码的F1/IoU与冻结曲线一致；分类统计包含中间标签排除数量；边界第二表、A–E JSON/CSV均生成。WHU迁移阈值在冒烟冻结文件内可检查，真实WHU无害性只能由正式冻结测试确认。

### 正式全量复核、训练、冻结与测试

完整历史复核失败时依赖链停住，应先查清原池/预测/标签差异，不调整验收数值。历史复核和准备必须完成后才训练。

```bash
r=$(submit28 b28-reference 12:00:00 4G '' "$FINAL_OUT" \
  "$STAGE/fp_run.py" reference)
p=$(submit28 b28-prepare 12:00:00 4G "$r" "$FINAL_OUT" \
  "$STAGE/fp_run.py" prepare)
a=$(submit28 b28-second-fit 08:00:00 4G "$p" "$FINAL_OUT" \
  "$STAGE/fp_fit.py" --dataset second)
b=$(submit28 b28-sysu-fit 08:00:00 4G "$a" "$FINAL_OUT" \
  "$STAGE/fp_fit.py" --dataset sysu)
f=$(submit28 b28-freeze 02:00:00 4G "$b" "$FINAL_OUT" \
  "$STAGE/fp_run.py" calibrate)
validation_deps=''
for dataset in second sysu levir; do
  v=$(submit28 "b28-$dataset-val" 06:00:00 4G "$f" "$FINAL_OUT" \
    "$STAGE/fp_run.py" evaluate --dataset "$dataset" --split val)
  q=$(submit28 "b28-$dataset-val-report" 01:00:00 4G "$v" "$FINAL_OUT" \
    "$STAGE/fp_run.py" report --dataset "$dataset" --split val)
  validation_deps="${validation_deps:+$validation_deps:}$q"
done
for dataset in second sysu levir whu; do
  test_deps=''
  for shard in 0 1 2 3; do
    t=$(submit28 "b28-$dataset-test-$shard" 08:00:00 4G "$validation_deps" "$FINAL_OUT" \
      "$STAGE/fp_run.py" evaluate --dataset "$dataset" --split test --shard "$shard/4")
    test_deps="${test_deps:+$test_deps:}$t"
  done
  submit28 "b28-$dataset-test-report" 01:00:00 4G "$test_deps" "$FINAL_OUT" \
    "$STAGE/fp_run.py" report --dataset "$dataset" --split test
done
```

各分片图像互斥；不要再启动同一数据集、同一分片的并行副本。全量参考复核按图缓存，可在原目录续跑；prepare同样可续跑。训练中断后使用新的输出目录重做训练链；冻结和已完成测试不得覆盖。统计报告只读取已有结果，可重复生成。

## 资源估计与待确认项

每作业1 CPU、4G，CPU训练，无GPU需求。batch20–24约1.3G峰值只作参考，本批尚未测量。按图保存掩码、不在内存展开全池掩码；训练缓存每单元约65字节的特征/标签/像素计数，另有拼接临时数组、模型、优化器、排序与报告开销。实际单元总量和峰值内存需冒烟及全量账单确认。参考复核/准备各预留12小时、单模型8小时、阈值2小时、验证6小时、测试每分片8小时、汇总1小时，均为待实测预算，不是运行结果。

待确认：

1. 全量6e计数和SECOND 0.784能否复现；未通过前不能形成新方案结论。
2. 各上游目录的逐图完整性、缓存`labels`和原pred池的真实对应关系；静态接口检查不能替代实际读入。
3. CPU用时、峰值内存、SLIC val重建耗时；以后按冒烟逐图`seconds`及Slurm账单调整资源。
4. SECOND跨拟合/官方检测器概率偏移，SYSU权重可能已用官方val，WHU从LEVIR迁移阈值；这些限制会进入冻结记录和结果报告。
5. LEVIR/WHU是否满足−0.3个百分点约束、对象MLP是否超过概率规则；均待正式运行，当前没有数值结论。

本地静态复查命令（不会执行实验代码）：

```bash
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch28
PYTHONDONTWRITEBYTECODE=1 /guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python \
  static_check.py > static_checks.txt
```
