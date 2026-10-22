# batch18：实验 8 第 1 步执行包

本批交付代码、盘点结果和提交命令。2026-10-22 仅进行了文件检查和语法检查；没有提交 Slurm、运行实验、联网或安装包。所有本批文件写在本目录。下列命令供后续执行，尚未执行。正式运行输出到 `/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch18/`。

## 文件与复用关系

| 文件 | 用途 |
|---|---|
| `exp8_runtime.py` | 数据划分、候选提取、原池读取、局部掩码存储和已有方法接口 |
| `exp8_prepare.py` | LEVIR val 检测器预测、合法面积中位数、U4 两期自动掩码、耗时估计 |
| `exp8_sam3.py` | 在 omniovcd 环境中复用 6c 的 SAM3 提取程序，补齐缺失缓存 |
| `exp8_pool.py` | 四种对象去重、U0/U4/U5 构建、逐图指标与单元保存 |
| `exp8_report.py` | 完整汇总、验证集选 U5 倍数、test_759 同样式绘图 |
| `exp8_verify.py` | 对真实产物回读验收，核对历史 SECOND U0/U4 和 batch17 对象 oracle |

batch17 的公共读写和局部掩码函数来自 `exp8_common.py`。实际去重函数在同批的 **`exp8_dedup_viz.py`**，本批直接导入 `groups_for`、`union_object`，没有另写去重算法。优先查 `probe/batch17`，当前实际使用 `wyh/exp6_staging/batch17`。7b 的 `period_partition`、`temporal_partition`、`disjoint_units`、`oracle`、`coverage` 直接导入 `probe/batch16/exp7_units.py`；全部边界、目标指标取精确路径 `probe/batch16/metrics_cd.py`。6a 的连通域、SLIC、目标定义和常量直接复用。未修改被复用文件及其默认行为。

test_759 绘图复用 `wyh/exp6_staging/all_pieces/all_pieces.py` 的 `panel`，已查看旧 `before_split.png`：全幅 t2、随机色半透明填充、黑轮廓、白色真值轮廓、红色 piece 619 框。

## 已核实的输入

下面路径以 `Chang-Agent/outputs/CD_1016_probe/` 为基准。计数来自目录和现有 JSON，未重新运行推理。

| 划分 | 图像数 | SAM3 原始缓存 | 6a 掩码池 | 7b U4 缓存 |
|---|---:|---:|---:|---:|
| SECOND test | 1694 | `exp4/second/sam3`：1694 | `exp6/pool/second`：1694 | `exp7_batch16/automatic/second`：1694 |
| SECOND second_cv 验证 | 297 | `exp4/second_cv/sam3` 包含全部 2968 train 图 | 全部 2968 train 图 | `exp7_batch16/automatic/second_cv`：297 |
| SYSU test | 4000 | `exp4/sysu/sam3`：4000 | `exp6/pool/sysu`：4000 | 无，需新跑 |
| LEVIR test 原图 | 128 | `exp4/levir/sam3`：128 | `exp6/pool/levir`：128 | 无，需新跑 |
| WHU test | 690 | `exp4/whu/sam3`：690 | `exp6/pool/whu`：690 | 无，需新跑 |
| LEVIR 官方 val 原图 | 64 | 未找到 | 未找到 | 未找到 |

SECOND 的 297 张严格取 `exp6/second_cv/manifest.json` 中 `verifier_val_images`，通过 6b 的 `verifier_split()` 校验。LEVIR 使用 `datasets/LEVIR-CD/{val_original,test_original}`，分别为 64/128 张 1024² 原图，不使用 test_256 的 2048 张切块。WHU 使用 `datasets/lim1/WHU-CD/test`，SYSU 使用 `datasets/lim1/SYSU-CD` 的 `test_*`。

检测器：SECOND `mambascd_base`；SYSU `schanger_small`；LEVIR/WHU `schanger_base`。测试预测在 `preds/<dataset>/<detector>/{prob,mask}`。目前 `preds/levir` 只有三个测试检测器目录，未发现 `levir_val` 预测目录。新增入口复用 `predict_schanger.run`，保持官方权重、训练归一化、非重叠 256² tile、无 TTA；验证输出单独写入本批 `preds/levir_val/schanger_base`。

6c 证据：`logs/cd1016-6c-sam3-levir-151437.out`、`cd1016-6c-sam3-whu-151440.out` 显示已完成 128/690 张；相应 `sam3_report.json` 总耗时 203.8/834.4 秒。`6c-h1-levir-151438`、`6c-h1-whu-151441` 的输出是 `exp5/{levir,whu}/anychange`，本批来源不含 H1，因而不调用它们。`6c-median-levir-151439`、`6c-median-whu-151442` 已生成训练几何 JSON；`6a-pieces-levir-151512` 与池摘要确认 LEVIR 切块已存在。

**复用边界：** LEVIR/WHU 旧 6a B 是“全部类别之间的差异”，与本批建筑有无差异不一致。因此复用现有 SAM3 类别图和检测器概率，重新提取建筑 B，并按同一 6a SLIC 规则重建本批 U0（包含 B、G0.05）。没有重跑测试集 SAM3，也没有把旧多类 B 当作建筑候选。若这些原始缓存缺失，`exp8_sam3.py` 可按 6c/5c 上游流程补齐。

## 单元、标签、选择规则

- SECOND 来源 B+G0.05+H1；其余 B+G0.05。候选面积 ≥10，`prob > 0.5` 的预测覆盖比例 ≤0.5。G 是 `prob > 0.05` 的 8 连通域。
- LEVIR/WHU B = `(cls_t1 == 5) XOR (cls_t2 == 5)` 的 8 连通域；仅保留建筑有无差异。另一时期类别 0 也视为“未预测为建筑”，这包含未识别建筑造成的假变化；不能找回建筑→建筑变化。SECOND/SYSU 保持原 B 定义：两期类别都 >0 且不同。
- 去重只发生在不同来源之间：IoU ≥0.5 或交集/较小面积 ≥0.8。默认 `objects_direct_pairs` 按原候选对 IoU 降序匹配，每个候选最多一次，不传递。对照 `objects_none`、`objects_nms_area`、`objects_transitive` 完全采用 batch17 原函数。保存成员 ID、来源集合、对象膨胀和最大对象面积。
- U0 保留旧切块重叠，对应 7b 的 **U0_pool**。SECOND/SYSU 直接读取现有池的选定来源；LEVIR/WHU 使用训练几何 864/3999，六通道 SLIC、compactness=10、start_label=1、`n_segments=max(4,int(H*W/median))`，仅切面积 >4×median 的候选，丢弃 <10 像素交集。LEVIR val 使用验证集本批中位数。若其他现成池缺失，采用同一 SLIC 规则与合法本批中位数重建，并记录来源。
- 历史 SECOND/SYSU U0 的 298/972 尺度源于旧测试统计，仅作为既定历史对照；**U5 完全不读取这些数值**。它们不是本批选出的参数。
- U4 按 7b 的 ViT-H 默认自动掩码，t1/t2 各一次。每期按 predicted_iou 降序、面积升序分配像素；两期标签共同细分后与原候选求交，交集 ≥10；再直接调用 7b 的小面积优先像素去重。两期都未覆盖的区域不产生单元。SECOND 的缓存只读，缺失时报错，禁止重新生成。
- U5 在直接对合并对象上，面积 **严格大于** `median×{4,8,16}` 才按 U4 标签划分，交集 ≥10。小对象保持完整。为满足这一要求，U5 保留对象之间的重叠，不对小对象再执行 U4 跨候选像素扣除；该口径记录在配置中。大对象内两期均未覆盖的像素会丢失，覆盖率如实报告。
- 标签：真值占比 ≥0.5 为正，<0.1 为负，其余中间。oracle 为原预测并上全部正单元，不删除预测像素。无信号目标沿用 6a：面积 ≥20、概率 >0.5 的覆盖 <10%；SECOND 按语义转移拆目标，其余为二值 8 连通目标。目标覆盖要求单个单元覆盖 ≥50% 且面积 ≤4×目标外接框面积；像素覆盖用所有单元的并集。
- SECOND 面积中位数只由 297 张 second_cv 验证图计算。既有 `exp6/geometry/second_cv_train.json` 的 verifier_val 中位数为 **272**，供运行后核对；代码重新按这些验证图计算，不硬编码 272。LEVIR 中位数来自 64 张官方 val 的无信号目标，需要先生成 val 预测。
- SYSU/WHU 没有 val。当前也未发现 `preds/{sysu_train,whu_train}`；因此分别从本地 16000/1260 张训练图的全部 ≥20 像素真值目标估计中位数，记为 `train_all_gt`。SYSU 本地 train 可能混有官方 val，无法还原分组，但不含 test；本批只读取这些标签估尺度，**不构建 SYSU 训练候选池**。WHU 既有训练中位数 3999 可供复核。若未来完整训练概率出现，程序使用无信号目标；部分概率缺失明确报错，不混用两种口径。
- SECOND/LEVIR 在完整验证图上按 micro 像素 F1 降序、IoU 降序选择 U5；精确并列才取较小倍数。其他指标不参与选择。测试必须读取冻结文件，只评所选 U5。SYSU/WHU 三个倍数均报告，状态 `sensitivity_unfrozen`（敏感性，未冻结），不产生选择文件。

## 环境与安装命令

代码保留在本暂存目录，通过绝对路径交给 `probe/run.sbatch`。现有环境可复用：exp5 中已找到 torch、timm、torchange、NumPy、SciPy、scikit-image、Matplotlib 的安装元数据；omniovcd 中有 SAM3 和 torch。**omniovcd 没有 SciPy、scikit-image、torchange，因此只用独立的 `exp8_sam3.py`。** 这些是静态盘点，不代表本批完成了导入或 GPU 验证。

现有环境无需安装。下面是将来重建 exp5 环境时的离线安装模板，当前未执行；`BATCH18_WHEELHOUSE` 必须指向准备完整的本地 wheel 目录。已知 `wheels/exp5/` 仅有 torchange wheel，不能把它当作完整依赖目录。

```bash
PY=/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python
: "${BATCH18_WHEELHOUSE:?需要完整的离线 wheel 目录}"
"$PY" -m pip install --no-index --find-links="$BATCH18_WHEELHOUSE" \
  torch==2.5.1 torchvision==0.20.1 timm==1.0.30 torchange==0.0.4 \
  numpy pillow scipy==1.17.1 scikit-image==0.26.0 matplotlib==3.11.2
```

SAM3 直接使用现有 omniovcd 环境和本地模型；ViT-H 模型路径直接导入 `exp7_refine.SAM_CKPT`；不下载模型。

## 提交准备与公共函数

以下均为未来提交命令。执行前确认有权写正式输出目录，核对控制面、分区、空闲资源和重复作业。当前任务要求不运行 Slurm，因此本批没有执行这些命令。`run.sbatch` 的退出钩子会发送成功/失败 QQ 通知，命令参数含实际输出路径；stdout/stderr 全部进入本批 `logs/`。

```bash
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch18
PROBE=/guisongxia01/pangchao/wangyihan/Chang-Agent/probe
EXP8=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch18
scontrol ping
squeue -u guisongxia
sinfo -p gre
mkdir -p "$EXP8/logs"

# 参数：名字、时限、主机内存、GPU数(0/1)、环境、前置Job ID列表(冒号分隔)、脚本及参数。
submit18() {
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

### 先跑 20 张真实图冒烟

SECOND 验证图可直接复用已有 U4，无需 GPU。冒烟面积中位数只来自首 20 张验证图，带 partial 标志，不可冻结。冒烟与正式目录分开。

```bash
SMOKE="$EXP8/smoke20"
s1=$(submit18 b18-smoke-geometry 00:20:00 4G 0 exp5 '' "$STAGE/exp8_prepare.py" \
  geometry --dataset second_cv --out "$SMOKE" --limit 20)
s2=$(submit18 b18-smoke-median 00:10:00 4G 0 exp5 "$s1" "$STAGE/exp8_prepare.py" \
  geometry --dataset second_cv --out "$SMOKE" --limit 20 --merge)
s3=$(submit18 b18-smoke-pool 01:00:00 8G 0 exp5 "$s2" "$STAGE/exp8_pool.py" \
  --dataset second_cv --out "$SMOKE" --limit 20)
s4=$(submit18 b18-smoke-report 00:10:00 4G 0 exp5 "$s3" "$STAGE/exp8_report.py" \
  aggregate --dataset second_cv --out "$SMOKE" --limit 20)
s5=$(submit18 b18-smoke-verify 00:30:00 8G 0 exp5 "$s4" "$STAGE/exp8_verify.py" \
  --dataset second_cv --out "$SMOKE" --limit 20)
```

先看 `smoke20/pool/second_cv/report.md`、`verification_0of1.json`。GPU 冒烟仍输出正式 automatic 缓存，因为 U4 不依赖标签和参数，可直接续跑；每个数据集先 20 张，避免一次启动全部全量任务。

```bash
for ds in sysu levir whu; do
  submit18 "b18-auto20-$ds" 00:30:00 16G 1 exp5 '' "$STAGE/exp8_prepare.py" \
    automatic --dataset "$ds" --out "$EXP8" --limit 20
done
# LEVIR val 先验证预测，再验证 SAM3。两个程序均支持 --shard i/n。
lv_pred20=$(submit18 b18-val-pred20 00:30:00 12G 1 exp5 '' "$STAGE/exp8_prepare.py" \
  predict --dataset levir_val --out "$EXP8" --limit 20)
submit18 b18-val-sam320 00:30:00 16G 1 omniovcd "$lv_pred20" "$STAGE/exp8_sam3.py" \
  --dataset levir_val --out "$EXP8" --limit 20
submit18 b18-val-auto20 00:30:00 16G 1 exp5 '' "$STAGE/exp8_prepare.py" \
  automatic --dataset levir_val --out "$EXP8" --limit 20
```

`timing/<dataset>_automatic_0of1.json` 保存逐图生成秒数分布。20 张全部计入，包含首次 GPU 调用；尚未包含进程启动、模型加载和磁盘写入耗时。将均值乘完整图数，再留 20% 余量估总 GPU 小时。后续可以将同样 20 图流程用于 SYSU/WHU/LEVIR 的池验收：先完成下文相应完整 geometry；SYSU/WHU 在独立冒烟目录生成/汇总 geometry 后，可执行 `exp8_pool.py --limit 20`；LEVIR test 必须先完成官方 val 冻结。不要用测试分数替代验证步骤。

### 完整 SECOND 验证、冻结、测试

```bash
g1=$(submit18 b18-second-geometry 01:00:00 4G 0 exp5 '' "$STAGE/exp8_prepare.py" \
  geometry --dataset second_cv --out "$EXP8")
g2=$(submit18 b18-second-median 00:10:00 4G 0 exp5 "$g1" "$STAGE/exp8_prepare.py" \
  geometry --dataset second_cv --out "$EXP8" --merge)
v1=$(submit18 b18-second-val 04:00:00 8G 0 exp5 "$g2" "$STAGE/exp8_pool.py" \
  --dataset second_cv --out "$EXP8")
v2=$(submit18 b18-second-val-report 00:20:00 8G 0 exp5 "$v1" "$STAGE/exp8_report.py" \
  aggregate --dataset second_cv --out "$EXP8")
v3=$(submit18 b18-second-val-verify 02:00:00 8G 0 exp5 "$v2" "$STAGE/exp8_verify.py" \
  --dataset second_cv --out "$EXP8")
f1=$(submit18 b18-second-freeze 00:10:00 4G 0 exp5 "$v3" "$STAGE/exp8_report.py" \
  freeze --dataset second_cv --out "$EXP8")
test_deps=''
for shard in 0 1 2 3; do
  job=$(submit18 "b18-second-test-$shard" 08:00:00 8G 0 exp5 "$f1" "$STAGE/exp8_pool.py" \
    --dataset second --out "$EXP8" --shard "$shard/4" --frozen "$EXP8/frozen/second_cv.json")
  test_deps="${test_deps:+$test_deps:}$job"
done
t1=$(submit18 b18-second-test-report 00:30:00 8G 0 exp5 "$test_deps" "$STAGE/exp8_report.py" \
  aggregate --dataset second --out "$EXP8")
t2=$(submit18 b18-second-test-verify 04:00:00 8G 0 exp5 "$t1" "$STAGE/exp8_verify.py" \
  --dataset second --out "$EXP8")
submit18 b18-test759 00:20:00 8G 0 exp5 "$t2" "$STAGE/exp8_report.py" \
  plot --dataset second --out "$EXP8"
```

### 完整 LEVIR 官方 val 与 test

先完成 20 张冒烟并检查结果，再提交全量。所有路径仍在本批输出目录，原预测不被覆盖。

```bash
p1=$(submit18 b18-levir-val-pred 01:00:00 12G 1 exp5 '' "$STAGE/exp8_prepare.py" \
  predict --dataset levir_val --out "$EXP8")
b1=$(submit18 b18-levir-val-sam3 01:00:00 16G 1 omniovcd "$p1" "$STAGE/exp8_sam3.py" \
  --dataset levir_val --out "$EXP8")
a1=$(submit18 b18-levir-val-auto 01:00:00 16G 1 exp5 '' "$STAGE/exp8_prepare.py" \
  automatic --dataset levir_val --out "$EXP8")
a2=$(submit18 b18-levir-test-auto 02:00:00 16G 1 exp5 '' "$STAGE/exp8_prepare.py" \
  automatic --dataset levir --out "$EXP8")
g1=$(submit18 b18-levir-geometry 01:00:00 4G 0 exp5 "$p1" "$STAGE/exp8_prepare.py" \
  geometry --dataset levir_val --out "$EXP8")
g2=$(submit18 b18-levir-median 00:10:00 4G 0 exp5 "$g1" "$STAGE/exp8_prepare.py" \
  geometry --dataset levir_val --out "$EXP8" --merge)
v1=$(submit18 b18-levir-val 04:00:00 12G 0 exp5 "$b1:$a1:$g2" "$STAGE/exp8_pool.py" \
  --dataset levir_val --out "$EXP8")
v2=$(submit18 b18-levir-val-report 00:20:00 8G 0 exp5 "$v1" "$STAGE/exp8_report.py" \
  aggregate --dataset levir_val --out "$EXP8")
v3=$(submit18 b18-levir-val-verify 01:00:00 12G 0 exp5 "$v2" "$STAGE/exp8_verify.py" \
  --dataset levir_val --out "$EXP8")
f1=$(submit18 b18-levir-freeze 00:10:00 4G 0 exp5 "$v3" "$STAGE/exp8_report.py" \
  freeze --dataset levir_val --out "$EXP8")
t1=$(submit18 b18-levir-test 06:00:00 12G 0 exp5 "$f1:$a2" "$STAGE/exp8_pool.py" \
  --dataset levir --out "$EXP8" --frozen "$EXP8/frozen/levir_val.json")
t2=$(submit18 b18-levir-test-report 00:20:00 8G 0 exp5 "$t1" "$STAGE/exp8_report.py" \
  aggregate --dataset levir --out "$EXP8")
submit18 b18-levir-test-verify 02:00:00 12G 0 exp5 "$t2" "$STAGE/exp8_verify.py" \
  --dataset levir --out "$EXP8"
```

### SYSU、WHU：只做 test 池，三个倍数均为敏感性

先核对 GPU 冒烟计时。SYSU 按 20 秒/图的保守估计是 **22.22 GPU 小时，超过 12 小时**；四分片不改变总耗时。下列是可执行的分片模板，应根据真实 20 张计时更新预算后使用。

```bash
for ds in sysu whu; do
  geometry_deps=''
  automatic_deps=''
  for shard in 0 1 2 3; do
    g=$(submit18 "b18-$ds-geometry-$shard" 02:00:00 4G 0 exp5 '' "$STAGE/exp8_prepare.py" \
      geometry --dataset "$ds" --out "$EXP8" --shard "$shard/4")
    geometry_deps="${geometry_deps:+$geometry_deps:}$g"
    a=$(submit18 "b18-$ds-auto-$shard" 07:00:00 16G 1 exp5 '' "$STAGE/exp8_prepare.py" \
      automatic --dataset "$ds" --out "$EXP8" --shard "$shard/4")
    automatic_deps="${automatic_deps:+$automatic_deps:}$a"
  done
  g=$(submit18 "b18-$ds-median" 00:20:00 4G 0 exp5 "$geometry_deps" "$STAGE/exp8_prepare.py" \
    geometry --dataset "$ds" --out "$EXP8" --merge)
  pool_deps=''
  for shard in 0 1 2 3; do
    p=$(submit18 "b18-$ds-pool-$shard" 10:00:00 8G 0 exp5 "$g:$automatic_deps" "$STAGE/exp8_pool.py" \
      --dataset "$ds" --out "$EXP8" --shard "$shard/4")
    pool_deps="${pool_deps:+$pool_deps:}$p"
  done
  r=$(submit18 "b18-$ds-report" 00:30:00 8G 0 exp5 "$pool_deps" "$STAGE/exp8_report.py" \
    aggregate --dataset "$ds" --out "$EXP8")
  submit18 "b18-$ds-verify" 06:00:00 8G 0 exp5 "$r" "$STAGE/exp8_verify.py" \
    --dataset "$ds" --out "$EXP8"
done
```

## 资源估计

均为单 GPU 累计小时，除明确标为既有实测外，都是运行前估计。7b SECOND 的 U4 单元构造耗时约 3.14 秒/图，包含两期自动掩码与 CPU 构造，不能直接当作其他数据集实测。自动掩码推理会缩放输入，小图耗时也不保证按像素数成比例下降。

| 项目 | 图数 | 每图参考/保守估计 | GPU 小时参考 / 保守 | 资源建议 |
|---|---:|---|---:|---|
| SECOND U4 | 1991 | 全部复用 | 0 / 0 | CPU |
| SYSU U4 | 4000 | 3.14 / 20 秒 | 3.49 / **22.22，超过12** | 1 GPU、16G 主机内存，4 分片 |
| LEVIR test U4 | 128 | 3.14 / 20 秒 | 0.11 / 0.71 | 1 GPU、16G |
| LEVIR val U4 | 64 | 3.14 / 20 秒 | 0.06 / 0.36 | 1 GPU、16G |
| WHU U4 | 690 | 3.14 / 20 秒 | 0.60 / 3.83 | 1 GPU、16G |
| LEVIR val SChanger | 64 | 未实测，预留 1–10 秒 | 0.02–0.18 | 1 GPU、12G |
| LEVIR val SAM3 | 64 | 旧 test 1.59 秒/图，保守 10 秒 | 0.03 / 0.18 | 1 GPU、16G |
| 候选、SLIC、全部 oracle/指标 | 每图 7–9 方案 | 暂估 2–30 秒；1024² 更慢 | 0 | 单 CPU；通常 8G，LEVIR 12G |
| geometry/汇总 | 逐图读标签或 JSON | 10 分钟–2 小时/分片 | 0 | 单 CPU、4–8G |

若未来测试 SAM3 缓存需要全部补齐，LEVIR 的 128 张按 10 秒/图约 0.36 小时，WHU 690 张约 1.92 小时；当前无需这部分 GPU。CPU 指标复用完整边界/目标实现，包含距离变换和凸包；未实测总耗时，以冒烟计时调整时限，超时后直接续跑。掩码按外接框压缩，单图流式计算，汇总只累积面积、指标和计数，不把全数据集掩码载入内存。

## 产物与断点续跑

- `geometry/<dataset>/<image>.json` 保存合法来源的逐图面积；`geometry/<dataset>.json` 是完整中位数、图像清单、来源、限制。`--merge` 必须等该来源所有分片完成。只读训练标签估尺度不构成训练池。
- `automatic/<dataset>/<image>.npz` 保存与 7b 相同的 labels/gpu_seconds；SECOND 始终读取旧缓存。SAM3 缓存在 `sam3/`，尚未完成的 SAM3 分片保留在 `sam3_work/`，重新执行相同分片会复用已完成图。
- `pool/<dataset>/<unit>/<image>.npz` 保存所有单元局部掩码；对应 `images/<image>.json` 保存来源/成员/父对象、标签、每单元面积、全部指标和计时。JSON 只在该图全部 NPZ 写完后原子落盘，作为完成标志。不要让两个作业同时处理同一图。
- `pool/<dataset>/summary.json` 和 `report.md` 包含每图单元数均值/P50/P90、单元面积 P10/P50/P90/最大、标签数和比例、无信号目标/像素覆盖、micro F1/IoU/ΔF1；报告开头列出 U5 中位数及实际来源，SYSU/WHU 明标“敏感性，未冻结”。完整边界与目标指标在 JSON 和附表中。对象膨胀、面积加权混杂度、扣除原预测后的真值比例也有附表。
- `frozen/{second_cv,levir_val}.json` 保存验证分数与唯一倍数；不覆盖不同的已有冻结文件。完整测试执行拒绝 partial geometry，SYSU/WHU 无冻结命令。
- `figures/test_759/new_pool.png` 是冻结 U5；`new_objects.png` 是直接对合并；`counts.json` 与 `README.md` 列出旧 1302 切块/221 候选和本批各单元数。旧参考含六来源，另列本批相同来源 U0，避免把来源减少全算成去重收益。
- 失败处理：缺输入、部分训练概率、配置变化、缺分片或旧统计不一致均明确失败。相同参数、相同输出目录重提即可续跑；每图提交后不会重复评估。更改参数/来源须用新目录，不能在冻结测试目录里调参。汇总和验收可重复，不会重新产生测试预测或重选参数。

## 验收和待确认项

已执行：6 个 Python 文件的语法解析、源码接口检查、历史目录与摘要检查；README Bash 代码块语法检查。没有运行数据计算、模块导入测试、模型推理、Slurm 或绘图。真实结果表、新图、GPU 峰值和完整运行耗时均待按上述命令产生。

真实验收由 `exp8_verify.py` 执行：回读真实 NPZ，核对面积、标签和 oracle TP/FP/FN；完整 SECOND 的 U0 对比 7b U0_pool、U4 对比 7b U4 的精确像素计数；完整 SECOND/SYSU 的四种去重逐项对比 batch17。20 张冒烟只报告其覆盖范围，不作为全量通过。当前仓库已有修改未触碰，`TASK.md` 保持不变。

尚待确认或测量：

1. SYSU 官方 val/原航片分组仍需 Ethan 决定；不影响本批 test 与训练标签尺度统计，训练候选池没有建立。
2. LEVIR val 的预测、SAM3 和 U4 目前不存在，需要按命令运行；若官方权重曾用 val 选优，本次 val 参数选择也有这一局限，目前未核实。
3. SYSU/LEVIR/WHU 的实际 U4 每图耗时和显存未测；尤其 SYSU 保守预算超过 12 GPU 小时。
4. 默认直接对合并沿用本次任务约定；既有去重例图是否已获 Ethan 最终确认，本地文档仍写“待检查”，本批未发送或发布任何内容。
