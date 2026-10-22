# batch21：SYSU 第 2 步质量表、清洗规则与审核页

2026-10-22 交付代码。本次只在 batch21 写文件，未提交或查询 Slurm、未运行实验、未联网或安装包。batch18/19/20、probe 和已有输出保持不变。当前目录无法读取 Git 状态。下列提交命令供后续运行；当前没有生成质量数字、清洗选择或审核页面。

## 文件与复用方式

| 文件 | 用途 |
|---|---|
| `exp8_batch21_common.py` | 按路径载入 batch19 runtime/pool，适配划分、冻结参数和池目录，复用 batch20 掩码及元数据检查 |
| `exp8_quality.py` | `analyze`、`aggregate`、`define`、`select`；复用 batch20 的 `unit_flags`、`keys`、`aggregate`、表格输出和指标函数 |
| `exp8_audit.py` | SYSU 分层抽样；复用 batch20 `render/page_html` 和 6g 分配、轮廓、近景/上下文及离线答题工具 |
| `static_check.py` | 标准库静态检查：语法、复用接口、页面模板替换位置、现有划分 JSON、路径和 README Bash 语法；不导入实验模块 |
| `static_checks.txt` | 本次静态检查输出，区分已检查与未执行项目 |

batch20 common 会把 batch18 加入模块搜索路径。本包装先在当前进程按绝对文件路径注册 batch19 的 `exp8_data`、`exp8_runtime`、`exp8_pool`，再导入 batch20 common，让其命中已加载的 SYSU 适配模块；随后仅替换当前进程的划分/路径函数。batch19 内部仍复用 batch18 算法。不要把本入口和原 batch18/20 CLI 放在同一个 Python 进程运行。

质量分析循环保留 batch20 的计算流程，改为 SYSU 划分和 batch19 输入；训练定义、选择、审核抽样中的 SECOND 固定条件由本批适配。没有新建池、自动掩码或 GPU 阶段。

## 输入、划分与输出

路径以 `/guisongxia01/pangchao/wangyihan` 为工作区根目录：

- `--batch19` 默认 `Chang-Agent/outputs/CD_1016_probe/exp8_batch19/`，全程只读。
- `--dataset pool_train` 和 `--dataset pool_val` 都读 `pool/sysu_val/`。名单来自 `wyh/CD-Agent/interim/exp8/sysu_val/split.json`，各 3,200 / 800 张；分别核对逐图 JSON 的 `pool_split`。沿用 batch19 的原图块验证：128 / 32 个块，每块 25 张，整块互斥。
- `frozen/sysu_val.json` 在每次入口运行时读取，核对划分、几何、冻结状态和像素 F1 / IoU 判据。主单元为其中指定的 `U5_x{multiplier}`，对照为 U0、`objects_direct_pairs`；不把 ×4、×8、×16 中任何一个写死为所选值。
- `sysu_val` 的检测器预测读 batch19 的 `preds/sysu_val/schanger_small/`；测试预测沿用 probe 公共 `preds/sysu/schanger_small/`。影像/二值真值沿用现有 SYSU loader。来源固定 B+G0.05，没有 H1，也不使用语义真值。
- `--out` 默认 `Chang-Agent/outputs/CD_1016_probe/exp8_batch21/`。质量结果按 `quality/pool_train/`、`quality/pool_val/` 分开保存。
- 正式审核读取 batch19 `pool/sysu/` 的冻结 U5，默认直接输出到任务指定的 `wyh/CD-Agent/audit/exp8_sysu/`。`--audit-dir` 可指定独立目录。这里描述的是后续运行行为；本次没有向该位置写入。

所有阶段都要求完整的 batch19 冻结文件和正式池；`--limit 20` 只缩减本批统计名单，不使用 batch19 冒烟池。正式选择必须使用完整 3,200 / 800 张。质量命令不接受测试集，审核命令只接受 `--dataset sysu`。

## 与 batch20 一致的统计口径

| 标记 | 定义 |
|---|---|
| tiny50 / tiny100 | 面积严格小于 50 / 100 px |
| thin | 外接框短边 ≤3 px，或面积/外接框面积 <0.2 |
| fragment | 与任一其他单元的两两交集/自身面积 >0.5，取最大值 |
| middle | 真值占比 ≥0.1 且 <0.5；≥0.5 为正例，<0.1 为负例 |

碎片是几何代理；类别可以重叠，数量不能相加。报告数量、占池比例、正/负/中间数量和类内比例。`analyze` 回读真实掩码核对标签与完整 oracle TP/FP/FN。

oracle 在原检测器预测上并入正单元。贡献/损失为“完整池 oracle − 移除该类或规则后 oracle”的 micro 像素 F1 / IoU，单位为百分点；负值表示移除后改善。重叠正单元仍覆盖的像素不重复计算损失，另报失去的独有 TP / FP 像素。同图中移除的正单元集合相同则复用一次指标计算。边界、目标、逐图下降、有害编辑等指标继续计算，写在附表和 JSON，不参与选择。

训练、验证分别报告同样三组单元；验证表同时提供描述统计与规则损失。规则为逻辑或：

| 规则 | 移除条件 |
|---|---|
| R1 | tiny50 + middle |
| R2 | tiny100 + thin + middle |
| R3 | R2 + fragment |

顺序为训练 `analyze → aggregate → define`，再验证 `analyze → aggregate → select`。验证分析要求已有同配置的训练候选登记。`define` 登记三种单元下的候选规模、正例率与训练损失，并导出每个候选的训练编号。`select` 只用冻结 U5 的验证 oracle 像素 F1 降序、IoU 降序选规则，仍精确并列时按 R1/R2/R3 顺序；U0 和直接对合并对象沿用同一选中规则。

完整池仅作参照，不参与候选竞争；没有额外损失容限。监督正例率分母为保留的正+负单元，另报全部保留单元分母下的正例率。验证与测试不导出清洗编号，不改任何原池掩码。

## 产物与续跑

- `quality/<划分>/images/*.json`：逐图质量标记、移除后的完整指标、独有 TP/FP 损失；训练行另存每个候选保留的监督编号。
- `quality/<划分>/summary.json`、`quality.csv`、`report.md`：完整汇总、各类标签分布、oracle 损失和完整指标附表。
- `cleaning/candidates.json`：训练阶段登记 R1–R3、来源/冻结信息、三种单元的规模及正例率。
- `cleaning/training_ids/<R1|R2|R3>/<单元>.jsonl`：`define` 输出；每张训练图一行，包含 `image`、`dataset=sysu_val`、`pool_split=pool_train`、`unit`、`rule`、`unit_ids`、`archive`。编号对应原 NPZ 中从 0 开始的顺序，`archive` 指向 batch19 `pool/sysu_val/`。允许空编号列表，中间标签不进入监督。导出总数与训练汇总核对。
- `cleaning/selected.json`、`cleaning/report.md`：所选规则、每个候选的验证得分、三种单元的 ΔF1 / ΔIoU 损失、所选规则的训练规模及正例率。
- `cleaning/selected_training_ids/<所选规则>/<单元>.jsonl`：`select` 输出所选版本，位置记录在 `selected.json.training_ids`；第 3 步读取这些编号及其原始池档案。
- 审核目录内：`samples.json`、`label_audit.html`、`crops/*.jpg`、`page_report.json`。状态为 `generated_not_reviewed`，没有人工答案。

逐图统计同配置可续跑，缺图、错误划分、冻结信息不匹配、掩码标签或 oracle 计数不一致会明确失败。汇总等待所有分片成功；不要并发写同一分片或运行多个 define/select。改变参数使用新 `--out`，审核配置改变使用新 `--audit-dir`。训练编号先写临时文件再替换；定义/选择完成记录最后写入。审核中断后复用相同抽样编号重新渲染。

## 审核页

正式抽取 200 个不同的 `(image, unit_id)`，另随机抽其中 20 个生成重复项，换独立编号后打乱顺序。独立编号不保证来自不同图像或不同父对象。种子默认 **20261022**，SYSU 不额外加偏移。

四层互斥：正例、普通负例、高分负例、中间标签，目标各 50 个（各 25%）。高分负例为测试池所有负例中，单元内检测器平均概率最高的前 10%（向上取整）；同分按图名、单元编号排序，普通负例排除该组。某层不足时沿用 batch20：先用尽该层，再按其他层剩余容量用 6g Hamilton 分配补齐；总体不足 200 时失败。实际数量、比例、总体容量与高分边界写入 `samples.json`，实际分层比例也显示在页面。

页面显示冻结 U5 单元轮廓，保留近景/上下文、五个答案“变了 / 没变 / 部分变化 / 看不清 / 跳过”、键盘操作、本地保存及导入导出。页面说明写明分层定义、种子、审核者非专家、答案只作参考；不显示当前对象标签、分数和重复身份，不叠加真值轮廓。对应关系保存在抽样清单中。正式生成要求 SYSU 的完整 `selected.json`，审核结果仅用于冻结方案后的测试池描述。

每份页面预算 40 MB；200+20 项共渲染 880 张 JPEG。冒烟使用测试排序前 20 张图、20 个独立单元和 2 个重复项，输出到冒烟根目录的 `audit/exp8_sysu/`，不要求正式清洗选择，页面标明冒烟。

## 环境和资源预算

沿用 `conda_envs/exp5-env/bin/python` 及 batch19/20 已有依赖：NumPy、Pillow、SciPy、scikit-image、torch、torchange 和 probe 本地模块；不安装新依赖。不运行模型推理，不需要 GPU。

| 阶段 | 建议资源 | 预算依据 |
|---|---|---|
| 20 图质量冒烟 | 1 CPU、4G、1 小时 | 暂按 20–120 秒/图，约 7–40 分钟 |
| 训练质量，3,200 图 | 8 分片，每片 1 CPU、4G、16 小时 | 暂估合计 17.8–106.7 CPU 小时，每片 2.2–13.3 小时 |
| 验证质量，800 图 | 4 分片，每片 1 CPU、4G、8 小时 | 暂估合计 4.4–26.7 CPU 小时，每片 1.1–6.7 小时 |
| 汇总 / define / select | 每项 1 CPU、4G、30 分钟 | 逐图读取 JSON；define 一次读图、同时写九份候选编号文件 |
| 正式审核 | 1 CPU、4G、2 小时 | 扫描测试池负例排名，渲染 880 张图；时间和内存待实测 |

以上是预算，未做本批性能测量。先用逐图 `seconds` 和作业内存记录校正正式资源；若冒烟超过预算，应调整分片/时限后再提交正式任务。

## 后续提交命令（本次未执行）

等 batch19 官方 val 全池、`frozen/sysu_val.json` 和测试池构建/验收完成后执行。沿用 batch20 的 `submit20` 写法，改名 `submit21`；所有调用 GPU 参数均为 0。提交前检查控制面、分区、资源和重复任务，控制面异常即停止。`probe/run.sbatch` 已带成功/失败 QQ 通知，包含作业、状态、耗时和命令；命令带产物目录，日志位于本批 `logs/`。

```bash
set -euo pipefail
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch21
EXP8=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch21
INPUT19=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch19
AUDIT=/guisongxia01/pangchao/wangyihan/wyh/CD-Agent/audit/exp8_sysu
scontrol ping
squeue -u guisongxia
sinfo -p gre
test -f "$INPUT19/frozen/sysu_val.json"
test -d "$INPUT19/pool/sysu_val/images"
test -d "$INPUT19/pool/sysu/images"
mkdir -p "$EXP8/logs"
submit21() {
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

### 训练、验证及页面冒烟

训练与验证各取自己划分排序前 20 张。只读正式 batch19 池，产物全放独立 `smoke20/`。冒烟不执行正式 select。

```bash
SMOKE="$EXP8/smoke20"
q=$(submit21 b21-smoke-train 01:00:00 4G 0 exp5 '' "$STAGE/exp8_quality.py" \
  analyze --dataset pool_train --batch19 "$INPUT19" --out "$SMOKE" --limit 20)
r=$(submit21 b21-smoke-train-report 00:15:00 4G 0 exp5 "$q" "$STAGE/exp8_quality.py" \
  aggregate --dataset pool_train --batch19 "$INPUT19" --out "$SMOKE" --limit 20)
d=$(submit21 b21-smoke-define 00:15:00 4G 0 exp5 "$r" "$STAGE/exp8_quality.py" \
  define --dataset pool_train --batch19 "$INPUT19" --out "$SMOKE" --limit 20)
qv=$(submit21 b21-smoke-val 01:00:00 4G 0 exp5 "$d" "$STAGE/exp8_quality.py" \
  analyze --dataset pool_val --batch19 "$INPUT19" --out "$SMOKE" --limit 20)
submit21 b21-smoke-val-report 00:15:00 4G 0 exp5 "$qv" "$STAGE/exp8_quality.py" \
  aggregate --dataset pool_val --batch19 "$INPUT19" --out "$SMOKE" --limit 20
submit21 b21-smoke-audit 00:40:00 4G 0 exp5 '' "$STAGE/exp8_audit.py" \
  --dataset sysu --batch19 "$INPUT19" --out "$SMOKE" --limit 20 --seed 20261022 \
  --audit-dir "$SMOKE/audit/exp8_sysu"
```

后续验收应确认两个划分均有三组单元的表，候选登记和九份训练编号文件完整、每份 20 行；审核清单为 20+2 项、页面五个选项和近景/上下文可用。浏览器交互、真实运行耗时和指标验收本次均未执行。

### 正式训练质量与候选登记

```bash
train_deps=''
for shard in 0 1 2 3 4 5 6 7; do
  q=$(submit21 "b21-train-quality-$shard" 16:00:00 4G 0 exp5 '' "$STAGE/exp8_quality.py" \
    analyze --dataset pool_train --batch19 "$INPUT19" --out "$EXP8" --shard "$shard/8")
  train_deps="${train_deps:+$train_deps:}$q"
done
tr=$(submit21 b21-train-report 00:30:00 4G 0 exp5 "$train_deps" "$STAGE/exp8_quality.py" \
  aggregate --dataset pool_train --batch19 "$INPUT19" --out "$EXP8")
definition=$(submit21 b21-define 00:30:00 4G 0 exp5 "$tr" "$STAGE/exp8_quality.py" \
  define --dataset pool_train --batch19 "$INPUT19" --out "$EXP8")
```

### 验证表、规则选择、正式审核

```bash
val_deps=''
for shard in 0 1 2 3; do
  q=$(submit21 "b21-val-quality-$shard" 08:00:00 4G 0 exp5 "$definition" "$STAGE/exp8_quality.py" \
    analyze --dataset pool_val --batch19 "$INPUT19" --out "$EXP8" --shard "$shard/4")
  val_deps="${val_deps:+$val_deps:}$q"
done
vr=$(submit21 b21-val-report 00:30:00 4G 0 exp5 "$val_deps" "$STAGE/exp8_quality.py" \
  aggregate --dataset pool_val --batch19 "$INPUT19" --out "$EXP8")
selection=$(submit21 b21-select 00:30:00 4G 0 exp5 "$vr" "$STAGE/exp8_quality.py" \
  select --dataset pool_val --batch19 "$INPUT19" --out "$EXP8")
submit21 b21-audit-sysu 02:00:00 4G 0 exp5 "$selection" "$STAGE/exp8_audit.py" \
  --dataset sysu --batch19 "$INPUT19" --out "$EXP8" --seed 20261022 --audit-dir "$AUDIT"
```

正式验收要求训练/验证为完整 3,200 / 800 图，候选九份和所选三份编号文件各 3,200 行；档案路径均指原 batch19 训练池；验证选择与规定排序一致；正式审核 200 个唯一图像/单元组合、20 个重复项、880 张 JPEG、实际分层比例已披露且总目录 ≤40 MB。人工审核另行进行。

## 本次检查与待确认项

静态检查可直接在管理节点运行，只解析源码和少量元数据：

```bash
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch21
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python -B static_check.py > static_checks.txt
```

检查覆盖 Python 内存编译、复用接口名称/签名、6g 页面替换位置、已有 split JSON 的整块划分、README Bash 语法及路径存在性。它不导入实验模块、不执行 README 提交命令，也不以伪造数据验证结果。

未执行模块导入、真实 analyze/aggregate/define/select/audit 和浏览器交互：本次任务只允许静态检查且禁止 Slurm。交付时 batch19 的冻结文件、`pool/sysu_val/`、`pool/sysu/` 尚不存在，不能填写冻结倍数或任何质量数字；以 `static_checks.txt` 的路径记录为准。输入缺失在正式运行时明确报错，不自动建池或补算。

待后续确认：batch19 全池与冻结完成状态；真实运行资源、审核分层容量及页面效果；研究上是否另设清洗损失容限（当前按任务严格排序）。官方 SChanger-small 权重是否使用 val 选优未知，按已使用记录；验证集仅 32 个原图块，且本次清洗规则与 U5 选择共用这 800 张验证图。U0 继续使用 batch19 记载的历史 SYSU 尺度 972；这些限制随报告保留，不能把验证得分解释成独立测试结论。
