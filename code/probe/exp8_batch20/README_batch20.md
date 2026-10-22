# batch20：SECOND 训练池、质量统计、清洗选择与三个审核页

2026-10-22 交付执行代码。本次仅阅读文件、检查接口和语法；未运行 Slurm、推理、数据统计、绘图、联网或安装包。新文件全部位于本目录，`TASK.md` 保持不变。未读取或写入 batch19 输出。正式结果尚未产生，下面的命令均供后续执行。

## 文件与输入

| 文件 | 用途 |
|---|---|
| `exp8_batch20_common.py` | 按路径导入 batch18，核对划分、冻结参数与池文件；共享局部掩码操作 |
| `exp8_train.py` | `automatic` 新建训练自动掩码；`build` 建七种池；`verify` 回读真实掩码验收 |
| `exp8_quality.py` | `analyze` 逐图统计；`aggregate` 输出表；`define` 登记训练候选规则；`select` 验证选择、导出训练单元编号 |
| `exp8_audit.py` | 分层抽样、重复项、带轮廓的近景/上下文图和离线答题页面 |

代码按绝对位置导入 `../batch18/exp8_runtime.py`、`exp8_pool.py`，继续使用它们的 batch17 去重、6a U0、7b 分区和完整指标实现。未修改旧代码和缓存。6g 的页面、抽样文件在 `CD-Agent/code/probe` 与 `Chang-Agent/probe` 当前内容一致；执行时导入 `Chang-Agent/probe` 中的 `exp6g_sample.proportional` 和 `exp6g_page.HTML/Renderer/context_box/jpeg_bytes`。

路径以 `/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe` 为基准：

- 训练划分读取 `exp6/second_cv/manifest.json` 的 `verifier_train_images`，与已有 `verifier_split()` 核对，要求 2,671 / 297 张且互斥。训练影像、概率、SAM3、H1 和旧 U0 都使用已有 `second_cv` 数据路径。
- 冻结读取 `exp8_batch18/frozen/second_cv.json` 与 `geometry/second_cv.json`，要求 U5 ×4、阈值 1,088 px，中位数 272 px；不重新估计训练几何，也不重新选 U5 倍数。
- 新自动掩码仅写 `exp8_batch20/automatic/second_cv_train/`。两期各跑一次默认 ViT-H，复用 `period_partition`、`temporal_partition`。模型位置直接读取 `exp7_refine.SAM_CKPT`，不下载。
- 新池写 `exp8_batch20/pool/second_cv_train/`，包含 `objects_none`、`objects_direct_pairs`、`objects_transitive`、`objects_nms_area`、`U0`、`U4`、`U5_x4`。原始来源 B+G0.05+H1，全部去重/切分和面积门槛复用 batch18。
- 验证只读 `exp8_batch18/pool/second_cv/`。审核只读该批 `pool/{second,levir,whu}/`；SECOND 用 U5 ×4，LEVIR 用 U5 ×16，WHU 用直接对合并对象。
- 正式输出根目录固定约定为 `exp8_batch20/`；`--out` 可指定独立的冒烟目录。`--batch18` 可指定同结构的只读输入根目录。

## 统计和清洗口径

SECOND 训练/验证分别报告 U5 ×4（主单元）、U0、直接对合并对象。LEVIR 测试报告 U5 ×16、U0、直接对合并对象；WHU 测试报告直接对合并对象（主单元）及 U0。LEVIR/WHU 仅描述，无规则选择、无清洗导出。

| 标记 | 定义 |
|---|---|
| `tiny50` / `tiny100` | 面积严格小于 50 / 100 px |
| `thin` | 外接框短边 ≤3 px，或面积/外接框面积 <0.2 |
| `fragment` | 与任一其他单元的交集/自身面积 >0.5；按最大两两重叠计算 |
| `middle` | 真值占比 ≥0.1 且 <0.5；0.5 归正例，<0.1 归负例 |

碎片指标是几何代理，不把重叠直接解释为同一地物；不使用多个邻居的并集。质量类别可重叠，数量不能相加。报告各类数量、占整个池的比例、正/负/中间数量及类内比例。

oracle 沿用 batch18：在原检测器预测上并入真值占比 ≥0.5 的完整单元。每类贡献是“完整 oracle − 移除此类后的 oracle”的 micro 像素 F1 / IoU（百分点），并报告失去的独有 TP/FP 像素。移除某个正单元后若其他正单元仍覆盖该像素，就不会重复计算损失。负例、中间标签通常贡献零，但仍影响训练规模。边界、目标、逐图下降、有害编辑率等完整指标照算，放附表/JSON；不用于选择。

同图中移除正单元编号完全相同的类别/规则共用一次指标计算；负例和中间标签不进入 oracle，不重复计算相同预测的边界和目标指标。

候选规则预先采用任务给定阈值；训练报告完成后由 `define` 登记到 `cleaning/candidates.json`，验证入口要求该文件先存在：

| 规则 | 移除条件（逻辑或） |
|---|---|
| R1 | tiny50、middle |
| R2 | tiny100、thin、middle |
| R3 | tiny100、thin、fragment、middle |

仅用 297 张验证图的 U5 ×4 oracle 像素 F1 选择；精确并列再比较 IoU，仍并列取 R1、R2、R3 中靠前者。完整池只作参照，不参与这三个清洗候选的竞争。没有另加可接受损失阈值，候选即使全部有损失，也会如实报告并选损失最小者。U0 和直接对合并对象用同一选中规则报告，不独立择优。监督正例率分母为保留的正+负单元，另报所有保留单元分母的正例率。

清洗仅导出 SECOND **训练**单元编号，不改变掩码、验证输入和测试池。测试统计的逐类移除只是反事实贡献计算，不生成清洗后的测试数据。

## 审核页

- SECOND 200 个不同的 `(image, unit_id)`，另随机添加 20 个重复；LEVIR/WHU 各 100 个，无额外重复。对象指冻结池中的判断单元；U5 大对象可能拆成多个单元，抽样编号互异不意味着原图或父对象互异。
- 四层互斥：正例、普通负例、高分负例、中间标签。高分负例为所有负例中**单元内检测器平均概率最高的前 10%（向上取整）**，同分按图名、单元编号排序；普通负例排除这些对象。
- 目标各 25%。若某层不足，先用尽该层，再用 6g Hamilton 分配函数按其余各层剩余容量分配缺额，保持规定的独立对象总数。总体不足则明确失败。实际数量、比例、各层总体数量和高分边界保存在 `samples.json`，实际比例与定义也写进 HTML 页面说明。
- 随机种子默认 `20261022`，各数据集使用固定不同偏移。重复项换独立随机编号，保留相同图像与轮廓，随机打乱顺序。原始对应关系只保存在抽样清单中。
- 复用 6g Renderer 时注入新池的局部掩码及影像缓存，保留其红色轮廓渲染；近景使用原 6a crop，宽景使用原 6g context_box。页面不显示当前对象真值、分数或重复身份，也不叠加真值轮廓。
- 答案为“变了 / 没变 / 部分变化 / 看不清 / 跳过”，快捷键 1 / 2 / 4 / 3 / 0；沿用本地保存、导出和导入。每份离线目录预算 40 MB。
- 正式审核生成要求 `cleaning/selected.json` 已冻结；仅用于测试池描述，不反馈到规则选择。生成后状态为 `generated_not_reviewed`，不执行人工审核。
- 页面先写到 `exp8_batch20/audit/exp8_{second,levir,whu}/`。用户审阅后才复制到 `CD-Agent/audit/`，本代码不执行仓库复制。

## 环境与资源估计

全部使用现有 `conda_envs/exp5-env/bin/python`。依赖：Python 标准库、NumPy、Pillow、SciPy、scikit-image、torch、torchange，以及 batch18 既有本地模块。没有新增依赖和安装步骤。当前只做文件及源码检查，未执行模块导入。

| 阶段 | 资源建议 | 预估/依据 |
|---|---|---|
| SECOND 自动掩码 | 1 GPU、2 CPU、12G 内存、4 小时 | 2,671×约3秒≈2.23 GPU 小时；主机内存已有实测约7G，留余量；20张约60秒推理，另含加载 |
| 七池构建 | 1 CPU、4G、4分片，每片8小时 | 旧 pool 实测约1.4G；暂按5–30秒/图，合计3.7–22.3 CPU小时 |
| 七池回读验收 | 1 CPU、4G、4分片，每片4小时 | 仅回读真实掩码和 oracle 计数；耗时待冒烟测量 |
| SECOND 质量计算 | 1 CPU、4G、8分片，每片8小时 | 包括三种单元和多次完整指标；暂按20–120秒/图，训练合计14.8–89 CPU小时 |
| 验证/LEVIR/WHU 质量 | 1 CPU、4G、各4分片，每片8小时 | 暂按20–120秒/图；1024² LEVIR 可能更慢，按冒烟校正 |
| 汇总、定义、选择 | 1 CPU、4G、30分钟 | 按图读取JSON，汇总保留指标，不载入全池掩码 |
| 每份审核页 | 1 CPU、4G、2小时 | 单图读取对象，保留抽样元数据和负例排名；生成400或880张JPEG，尚未实测 |

除明确给出的历史内存和约3秒/图外，上表是预算，不能当作本批实测。质量计算比单次 pool 多次调用完整指标，需以冒烟输出的 `seconds` 调整时限。只有自动掩码阶段申请 GPU，不训练、不保存 checkpoint。

## 后续提交模板（本次未执行）

模板沿用 batch18 `submit18`，重命名为 `submit20`。提交前核对路径、分区、可用资源和重复任务；控制面异常时停止提交。已有 `probe/run.sbatch` 负责退出时的成功/失败 QQ 通知，包含作业名、状态、耗时、命令；命令的 `--out` 给出产物路径。stdout/stderr 位于本批 `logs/`。

```bash
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch20
EXP8=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8_batch20
scontrol ping
squeue -u guisongxia
sinfo -p gre
mkdir -p "$EXP8/logs"
submit20() {
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

### 20 张真实图冒烟

训练、验证各取固定排序前20张。训练自动掩码与池全写 `smoke20/`，不污染正式完成标志；仍使用完整 batch18 冻结参数。正式验证选规则拒绝 `--limit`。审核冒烟读取测试前20张，每份仅抽20个单元，SECOND另加2个重复，页面标明冒烟。

```bash
SMOKE="$EXP8/smoke20"
a=$(submit20 b20-smoke-auto 00:30:00 12G 1 exp5 '' "$STAGE/exp8_train.py" \
  automatic --dataset second_cv_train --out "$SMOKE" --limit 20)
p=$(submit20 b20-smoke-pool 00:40:00 4G 0 exp5 "$a" "$STAGE/exp8_train.py" \
  build --dataset second_cv_train --out "$SMOKE" --limit 20)
v=$(submit20 b20-smoke-verify 00:30:00 4G 0 exp5 "$p" "$STAGE/exp8_train.py" \
  verify --dataset second_cv_train --out "$SMOKE" --limit 20)
q=$(submit20 b20-smoke-quality 01:00:00 4G 0 exp5 "$v" "$STAGE/exp8_quality.py" \
  analyze --dataset second_cv_train --out "$SMOKE" --limit 20)
r=$(submit20 b20-smoke-report 00:15:00 4G 0 exp5 "$q" "$STAGE/exp8_quality.py" \
  aggregate --dataset second_cv_train --out "$SMOKE" --limit 20)
d=$(submit20 b20-smoke-define 00:10:00 4G 0 exp5 "$r" "$STAGE/exp8_quality.py" \
  define --dataset second_cv_train --out "$SMOKE" --limit 20)
qv=$(submit20 b20-smoke-val 01:00:00 4G 0 exp5 "$d" "$STAGE/exp8_quality.py" \
  analyze --dataset second_cv --out "$SMOKE" --limit 20)
submit20 b20-smoke-val-report 00:15:00 4G 0 exp5 "$qv" "$STAGE/exp8_quality.py" \
  aggregate --dataset second_cv --out "$SMOKE" --limit 20
for ds in levir whu; do
  q=$(submit20 "b20-smoke-quality-$ds" 02:00:00 4G 0 exp5 '' "$STAGE/exp8_quality.py" \
    analyze --dataset "$ds" --out "$SMOKE" --limit 20)
  submit20 "b20-smoke-report-$ds" 00:15:00 4G 0 exp5 "$q" "$STAGE/exp8_quality.py" \
    aggregate --dataset "$ds" --out "$SMOKE" --limit 20
done
for ds in second levir whu; do
  submit20 "b20-smoke-audit-$ds" 00:40:00 4G 0 exp5 '' "$STAGE/exp8_audit.py" \
    --dataset "$ds" --out "$SMOKE" --limit 20
done
```

检查 `pool/second_cv_train/verification_0of1.json` 为 passed、各质量报告存在、七池面积/标签/TP/FP/FN 回读一致；按 `timing/` 和逐图 `seconds` 校正正式资源。审核需在浏览器查看近景/上下文轮廓，并试用五个选项、导入导出；这项交互检查当前未执行，不计为已通过。

### 完整 SECOND 建池和训练质量

```bash
a=$(submit20 b20-train-auto 04:00:00 12G 1 exp5 '' "$STAGE/exp8_train.py" \
  automatic --dataset second_cv_train --out "$EXP8")
verified=''
for shard in 0 1 2 3; do
  p=$(submit20 "b20-train-pool-$shard" 08:00:00 4G 0 exp5 "$a" "$STAGE/exp8_train.py" \
    build --dataset second_cv_train --out "$EXP8" --shard "$shard/4")
  v=$(submit20 "b20-train-verify-$shard" 04:00:00 4G 0 exp5 "$p" "$STAGE/exp8_train.py" \
    verify --dataset second_cv_train --out "$EXP8" --shard "$shard/4")
  verified="${verified:+$verified:}$v"
done
quality_deps=''
for shard in 0 1 2 3 4 5 6 7; do
  q=$(submit20 "b20-train-quality-$shard" 08:00:00 4G 0 exp5 "$verified" "$STAGE/exp8_quality.py" \
    analyze --dataset second_cv_train --out "$EXP8" --shard "$shard/8")
  quality_deps="${quality_deps:+$quality_deps:}$q"
done
tr=$(submit20 b20-train-report 00:30:00 4G 0 exp5 "$quality_deps" "$STAGE/exp8_quality.py" \
  aggregate --dataset second_cv_train --out "$EXP8")
definition=$(submit20 b20-define 00:15:00 4G 0 exp5 "$tr" "$STAGE/exp8_quality.py" \
  define --dataset second_cv_train --out "$EXP8")
```

### 验证选择、描述性质量表、正式审核页

```bash
val_deps=''
for shard in 0 1 2 3; do
  q=$(submit20 "b20-val-quality-$shard" 08:00:00 4G 0 exp5 "$definition" "$STAGE/exp8_quality.py" \
    analyze --dataset second_cv --out "$EXP8" --shard "$shard/4")
  val_deps="${val_deps:+$val_deps:}$q"
done
vr=$(submit20 b20-val-report 00:30:00 4G 0 exp5 "$val_deps" "$STAGE/exp8_quality.py" \
  aggregate --dataset second_cv --out "$EXP8")
selection=$(submit20 b20-select 00:30:00 4G 0 exp5 "$vr" "$STAGE/exp8_quality.py" \
  select --dataset second_cv --out "$EXP8")
for ds in levir whu; do
  ds_deps=''
  for shard in 0 1 2 3; do
    q=$(submit20 "b20-quality-$ds-$shard" 08:00:00 4G 0 exp5 '' "$STAGE/exp8_quality.py" \
      analyze --dataset "$ds" --out "$EXP8" --shard "$shard/4")
    ds_deps="${ds_deps:+$ds_deps:}$q"
  done
  submit20 "b20-report-$ds" 00:30:00 4G 0 exp5 "$ds_deps" "$STAGE/exp8_quality.py" \
    aggregate --dataset "$ds" --out "$EXP8"
done
for ds in second levir whu; do
  submit20 "b20-audit-$ds" 02:00:00 4G 0 exp5 "$selection" "$STAGE/exp8_audit.py" \
    --dataset "$ds" --out "$EXP8"
done
```

## 输出、续跑与验收范围

- `automatic/second_cv_train/*.npz`：labels、gpu_seconds。已存在文件直接复用；缺失文件才推理。
- `pool/second_cv_train/<方案>/*.npz` 和 `images/*.json`：七种真实掩码及来源、成员、父对象、面积、标签和指标。逐图JSON在七个NPZ完成后原子写入；中断后同参数续跑。
- `pool/second_cv_train/verification_*of*.json`：真实掩码回读标签、残余真值计数、独立调用 batch18/7b oracle 核对 TP/FP/FN。正式四分片都必须通过。
- `quality/<划分>/images/*.json`：逐图质量、反事实移除指标及训练候选保留编号；`summary.json`、`quality.csv`、`report.md`：完整汇总。CSV包含每类标签数量和类内比例；Markdown含主表及边界/目标附表。
- `cleaning/candidates.json`：验证前登记的训练规则和训练数量；`selected.json`、`report.md`：验证选择、损失及各训练方案规模/正例率；`training_ids/*.jsonl`：每张训练图在原NPZ中的保留编号。空列表允许，监督不包含中间标签。
- `audit/exp8_<数据集>/`：`samples.json`、`label_audit.html`、`crops/*.jpg`、`page_report.json`。清单不包含人工答案；页面离线运行，未来人工导出的答案由审核者保管。
- 同配置可续跑；缺输入、混用冒烟/正式目录、缺图、标签或oracle计数不一致会明确失败。更改阈值、定义或抽样配置使用新输出根目录。不要让两个作业同时写同一分片；汇总必须等待全部分片成功。审核中断后会按既有清单恢复相同编号并重绘，不更换样本。

已执行验证：四个 Python 文件的语法编译检查、README Bash 块语法检查，以及复用函数与输入字段的源码核对。未执行：模块导入、20图冒烟、全量统计、GPU推理、真实页面生成及浏览器交互。用户明确限定本次只能做语法检查且不得运行 Slurm，因此这些结果没有填入报告。

仍需后续实测/研究确认：

1. 质量计算的真实时间、显存和页面分层容量，要在20图冒烟与全量运行后报告；目前没有新数字。
2. 任务未指定清洗损失容限。本实现严格以像素 F1 / IoU 排序并如实报告损失，不额外设门槛；若要增加门槛，应在查看验证结果前确定。
3. 第1步文档仍将直接对合并例图标为待确认。本批沿用任务明确指定的冻结池；正式审核页产生后仍待用户审阅再拷入仓库。
