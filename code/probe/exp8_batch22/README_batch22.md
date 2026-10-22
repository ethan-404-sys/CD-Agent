# batch22：SECOND 对象池 V1 训练与冻结评估

本批交付第3步第1–4项的执行代码。仅在本目录写文件；未运行 Slurm、训练、数据计算、模块导入、联网或安装包。当前只完成源码接口核对和语法检查，下面的命令供后续执行。`TASK.md` 保持原样。

## 文件

| 文件 | 用途 |
|---|---|
| `exp8_config.py` | 数据集路径、划分、U5配置、来源、历史引用路径和级联分位数；后续数据集适配集中修改此处 |
| `exp8_features.py` | 逐图读取 batch18/20 真实对象；提取19维特征；适配6b `Pool` / `Features`；读取清洗编号 |
| `exp8_fit.py` | full / clean 两份训练，完整逐 epoch 轨迹、最佳验证分数、正式模型 |
| `exp8_evaluate.py` | 验证集校准、冻结、验证/测试逐图评估、级联统计、历史引用、Markdown与A–E附表 |

按路径导入相邻 `batch20/exp8_batch20_common.py`，经它复用 batch18 的池格式和 batch16 `metrics_cd.py`。6b 从 `/guisongxia01/pangchao/wangyihan/Chang-Agent/probe` 导入。未修改这些依赖。本批只处理 SECOND，不访问其他并行批次的输出目录。

## 输入与依赖

输出基准目录：`/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch22/`。

相对 `outputs/CD_1016_probe/` 的输入：

- `exp8_batch20/pool/second_cv_train/{images,U5_x4}/`：2,671张完整训练池，`images/<stem>.json` 为完成标志，包含 `units.U5_x4.details`；NPZ保存局部掩码。按 `README_batch20.md` 格式读取。
- `exp8_batch20/cleaning/selected.json` 及 `cleaning/training_ids/U5_x4.jsonl`：clean必需。核对图名、规则、单元编号和原NPZ路径，仅使用 `unit_ids` 保留的监督单元。
- `exp8_batch18/pool/second_cv/{images,U5_x4}/`：297张完整验证池；`pool/second/`：1,694张完整测试池。
- `exp8_batch18/frozen/second_cv.json` 与 `geometry/second_cv.json`：沿用U5×4、1,088 px，不重选单元。
- 概率、原预测和真值路径沿用 `exp8_runtime` / `common`；train、val对应 `second_cv`，test对应 `second`。
- `exp6/second_cv/manifest.json`：沿用6b `verifier_split()`，按图像分离训练和验证。
- `exp6/verifier/none/{eval_second_cv,eval_second,thresholds_second_cv}.json` 和已有分数路径：旧U0结果引用。测试A–E旧附表引用 `eval_metrics/summary.json` 第5行。

2026-10-22文件检查时，训练池的 `images/` 和 `cleaning/selected.json` 尚不存在。代码按约定接口编写；缺少输入会明确失败。正式运行须等待 batch20 产物完整、既有掩码回读验收通过。full可先训练，clean及最终冻结需要清洗结果。

解释器使用已有 `conda_envs/exp5-env/bin/python`。依赖：Python标准库、NumPy、PyTorch、Pillow、SciPy、scikit-image，以及上述本地模块的既有依赖；不添加包。程序禁写依赖目录中的字节码；提交时也设置 `PYTHONDONTWRITEBYTECODE=1`。

## 特征与训练

总计19维：

1. 6b none实际使用的11维：单元内检测器概率mean/max/min/p90、log(area)、B/G0.05/C1/C2/H1/pred六个来源位。新对象允许多个来源位同时为1；本池C1/C2/pred恒为0。6b none不使用 `split`，本批也不加入。
2. 来源个数1维。
3. 七种非空来源组合的one-hot：B、G0.05、B+G0.05、H1、B+H1、G0.05+H1、B+G0.05+H1，按位编码顺序固定。每个单元恰有一个组合位为1。

特征只由对象掩码、来源、检测器概率得到。真值标签分开存放，不进入模型输入。正例真值占比≥0.5，负例<0.1，中间标签不参与损失、AUC/AP和召回分母；验证与测试保留所有单元用于推理、阈值像素搜索和级联工作量统计。

复用6b 512→256→1 MLP、ReLU、dropout=0.15、AdamW(lr=0.001, weight_decay=0.0001)、加权BCE、标准化及预测函数。pos_weight=该版本训练负例数/正例数，标准化只统计该版本保留的监督训练单元。默认seed=1020、batch_size=2048、最多50 epoch、验证AP连续7 epoch未提升早停；AP精确相同保留较早epoch。full和clean使用相同未清洗验证池。

每个epoch保留训练时加权loss，以及**全部监督训练单元**和全部监督验证单元在eval模式下的loss、AUC、AP、ROC80/90统计、最佳epoch、耗时。相比旧6b最多抽30万训练单元的监控，本批完整记录训练指标；监控不改变训练批次随机序列。`train_log.jsonl` 每行立即刷新，失败前的轨迹保留。

正式运行保存最佳AP checkpoint和包含中间标签的最佳验证分数。冒烟只保存轨迹、训练摘要和验证分数，**不保存checkpoint**。

## 阈值、测试与统计口径

`calibrate`只读取验证池和两份最佳验证分数，检测器规则使用同一U5单元内的平均概率。三种方案分别：

- 遍历所有验证单元分数断点，加0和2（2代表不接受任何单元）。按像素最大分数图求接受单元并集，再并入原检测器掩码；重叠像素不重复计数。
- 以micro像素F1选阈值，精确并列再看IoU，仍并列取更高阈值。边界和目标指标不参与选择。
- 单独冻结验证正例召回达到至少80%的最高阈值，平分按6b的 `score >= threshold` 计入。
- 以各方案验证**全部单元**分数的q10/q25/q50为下阈值，q75/q90/q95为上阈值，固定报告九种组合。高于上阈值接受，低于下阈值丢弃，两个边界及其间全部归不确定；分位值相同仍照此统计。

三种方案全部预先冻结作为对照，同时仅按其验证像素F1/IoU登记一个 `primary`（完全并列按full、clean、detector顺序）。测试不重新选方案。冻结写 `frozen.json`，含模型run_id、完整划分、来源与特征定义、U5冻结记录、两个操作阈值和九种级联组合。

`validation`按冻结方案计算完整A–E指标，`report --split val`完成汇总后，`test`才允许读取测试池。测试仅应用已冻结阈值；报告AUC/AP、验证recall80阈值下的**实际测试召回/FPR**、像素精度、F1/IoU及其变化。测试实际召回不保证等于80%，不在测试集重定操作阈值。

另报告当前划分的**描述性ROC80**：FPR、达到的召回、新增像素精度和F1/2。此项定义在首次测试前登记于冻结文件；同一次推理保存每图单元分数及扣除原预测后的像素最大分数直方图，汇总直接从这些计数求精度，不重新读取测试掩码或推理，也不将测试ROC80阈值用作端到端或级联操作阈值。

新增像素精度 = 接受单元并集扣除原预测后的TP / (TP+FP)，与同一划分原检测器F1/2比较；没有新增像素时为null。SECOND测试F1/2约37.08%，具体值由本次原预测计数给出。全部比例在JSON中为0–1，Markdown显示百分数；ΔF1/IoU以百分点显示。

级联报告每图不确定数均值/P50/P90、正/负/中间数量、正例率（全体分母及正+负分母均报告）、仅并入直接接受单元的ΔF1/IoU。工作量=不确定对象数×0.44秒/3600，另将验证每图数量外推到1,694张测试规模；测试汇总用实际不确定总数。此估计不含加载、裁块、通信时间，本批不调用MLLM。

A–E复用batch16完整实现，包括BF容差1/2/4/5、Boundary IoU两种宽度、目标面积桶、拆分/粘连、粗糙度、碎片、逐图变化和有害编辑率。主方案、recall80和每种级联接受部分均计算；同图同上阈值的三个组合共用一次指标及保存记录。旧U0仅引用原结果，不重训、不重新推理或重新评估旧掩码：

- 端到端引用B+G0.05+H1阈值 **0.819681704044342** 对应的结果。
- 旧AUC/AP总体和旧ROC80描述性像素精度使用五种补漏来源；报告明确标注范围并列出分来源指标，不冒充三来源总体。
- 旧A–E附表版本缺少BF d1/d4、拆分/粘连、有害编辑率和边界变化分布；保留缺项，CSV留空。本批新方案计算完整指标。

## 输出与失败处理

- `protocol.json`、`features/{train,val}/config.json` / `manifest.json` / `*.npz`：共享协议与紧凑特征；掩码按图读取，不驻留全池。
- `models/{full,clean}/train_log.jsonl`、`training.json`、`validation_scores.npz`、正式 `model.pt`。
- `curves/{full,clean,detector}.npz`：全部验证分数断点与TP/FP/FN、F1/IoU曲线。
- 正式 `frozen.json`；冒烟仅 `smoke_calibration.json`，不能用于测试。
- `evaluation/{val,test}/images/*.json`、`scores/*.npz`：完成的逐图指标、单元分数和新增像素分数直方图，含冻结方案编号。
- 每个划分的 `summary.json`、`report.md`、`appendix.json`、`appendix_micro.csv`；正式另有 `legacy.json`。

缺图、缺清洗记录、标签类别缺失、分数与对象编号不匹配、配置或模型run_id变化均失败并保留错误，不跳过。已完成逐图JSON要求对应分数文件存在，续跑只处理未完成图；报告只读已有结果，可重复生成。训练不覆盖已有轨迹或模型：训练中断后使用新输出根目录重新训练。不要并发运行两个训练任务写共享特征缓存，也不要两个评估任务处理同一图；评估分片应固定。完整测试与冒烟使用不同输出根目录。

单图提交前中断的计算可能在恢复时重做，已提交的测试图不重评。首次测试的全部对照一并冻结，之后不能根据测试结果改阈值再覆盖本目录。

## 资源预算

| 阶段 | 建议资源 | 预算（尚未实测） |
|---|---|---|
| 特征准备+一份MLP训练 | 1 CPU、4G、8小时 | 无骨干无需GPU；可改1 GPU、2 CPU、4G、2小时，并设 `--device cuda` |
| 验证阈值搜索 | 1 CPU、4G、1小时 | 297图，完整分数断点，逐图累计像素计数 |
| 验证完整指标 | 1 CPU、4G、3小时 | 每图三评分器×最多五种接受掩码及原预测 |
| 测试完整指标 | 4分片，各1 CPU、4G、8小时 | 共1,694图，每片约424图；按冒烟耗时调整 |
| 汇总 | 1 CPU、4G、1小时 | 不读取掩码，只汇总紧凑逐图记录与分数 |

batch20约1.3G是参考；本批额外加载PyTorch及特征数组，先申请4G。特征主体约每单元89字节（meta、extra、label、图号和单元号），训练batch和排序另有开销。GPU非必需。CPU时间、峰值内存和全池单元数均待真实冒烟测量，不能视为实测结论。

## 后续提交命令（本次全部未执行）

仅在输入完成后使用。先核对分区、可用资源、重复作业；控制面异常则停止。模板复用 `probe/run.sbatch`，由它在长作业成功/失败退出时发送QQ通知，内容含任务、状态、耗时和命令；命令的 `--out` 给出准确产物路径。stdout/stderr位于所选输出目录的 `logs/`。

```bash
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch22
FINAL_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch22
scontrol ping
squeue -u guisongxia
sinfo -p gre
submit22() {
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

固定取完整训练/验证池各前20张。依赖完整的batch20清洗选择，但只训练这20张对应的保留单元。不读取测试池、不保存模型；两份训练各2 epoch，再检查阈值、级联、A–E和报告链路。若前20张任一监督集合仅有一个类别，任务明确失败，应在运行正式测试前增加冒烟图数。

```bash
SMOKE="$STAGE/runtime_smoke20"
a=$(submit22 b22-smoke-full 00:30:00 4G '' "$SMOKE" \
  "$STAGE/exp8_fit.py" --variant full --device cpu --limit 20 --epochs 2)
b=$(submit22 b22-smoke-clean 00:30:00 4G "$a" "$SMOKE" \
  "$STAGE/exp8_fit.py" --variant clean --device cpu --limit 20 --epochs 2)
c=$(submit22 b22-smoke-calibrate 00:20:00 4G "$b" "$SMOKE" \
  "$STAGE/exp8_evaluate.py" calibrate --limit 20)
d=$(submit22 b22-smoke-val 00:40:00 4G "$c" "$SMOKE" \
  "$STAGE/exp8_evaluate.py" validation --limit 20)
submit22 b22-smoke-report 00:15:00 4G "$d" "$SMOKE" \
  "$STAGE/exp8_evaluate.py" report --split val --limit 20
```

验收：两份 `train_log.jsonl` 均含两个epoch的完整train/val指标；无 `model.pt`、无正式 `frozen.json`；`evaluation/val/summary.json` 覆盖20图、三种评分器、两个操作阈值及每种九组级联；micro F1/IoU与新增TP/FP一致，所有新增像素精度按并集统计；报告、CSV和完整A–E附表可读取。用逐图 `seconds` 预算正式时限，峰值内存由后续作业账单读取。

### 正式训练、验证冻结及单次测试

```bash
a=$(submit22 b22-full 08:00:00 4G '' "$FINAL_OUT" \
  "$STAGE/exp8_fit.py" --variant full --device cpu)
b=$(submit22 b22-clean 08:00:00 4G "$a" "$FINAL_OUT" \
  "$STAGE/exp8_fit.py" --variant clean --device cpu)
c=$(submit22 b22-calibrate 01:00:00 4G "$b" "$FINAL_OUT" \
  "$STAGE/exp8_evaluate.py" calibrate)
d=$(submit22 b22-val 03:00:00 4G "$c" "$FINAL_OUT" \
  "$STAGE/exp8_evaluate.py" validation)
e=$(submit22 b22-val-report 01:00:00 4G "$d" "$FINAL_OUT" \
  "$STAGE/exp8_evaluate.py" report --split val)
test_deps=''
for shard in 0 1 2 3; do
  t=$(submit22 "b22-test-$shard" 08:00:00 4G "$e" "$FINAL_OUT" \
    "$STAGE/exp8_evaluate.py" test --shard "$shard/4")
  test_deps="${test_deps:+$test_deps:}$t"
done
submit22 b22-test-report 01:00:00 4G "$test_deps" "$FINAL_OUT" \
  "$STAGE/exp8_evaluate.py" report --split test
```

失败后先检查对应日志、缺失输入与中间文件；修复后仅续跑尚未完成的阶段。命令未自动等待batch20作业，须在其完成后提交本链。

## 本次检查及待定事项

本次已做：阅读任务“设计确认”和第3步、适用规则、Git状态、旧实现及实际历史JSON字段；四个新Python文件的语法编译检查；本README Bash块的 `bash -n` 检查。未执行模块导入、真实冒烟、训练、阈值计算或测试。用户限制本次只做语法检查且禁止Slurm；上游训练池与清洗结果也尚未就绪。

待后续解决：

1. batch20产物完成后验证真实接口、训练曲线、时间及内存；新V1是否让ΔF1转正尚无结果。
2. 比较时须区分验证集冻结recall80操作结果与当前划分ROC80描述性统计，两者均已提供，后者不参与方案选择。
3. 旧U0五来源分类总体与三来源新池的范围不同，旧A–E还有明确缺项。当前按要求直接引用并注明，未增加旧模型重跑。
