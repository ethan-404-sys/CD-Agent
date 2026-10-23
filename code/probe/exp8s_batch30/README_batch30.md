# batch30：实验8补充第1、2步、公共底座和 r1

本批按 `TASK.md` 和已全文阅读的 `wyh/CD-Agent/doc/exp8_supp_candidate_budget.md` 编写。用户后续确认：LEVIR 用官方 val 的 C2 切点，WHU 迁移该切点；LEVIR val 的 SAM3 缓存缺失时，两者均使用 C1+全部 C2。全部本次写入位于 batch30。未修改 probe、batch16–29、原始数据或模型；当前目录不是可用的 Git 仓库。

本次只执行源码、接口、命令语法及 `python -m py_compile` 检查。未提交 Slurm、运行实验、计算新指标、联网或安装依赖。r1 单元测试已交付，尚未运行；后续命令将其放在 CPU 作业内执行。不能把静态通过解释为真实冒烟或一致性复算通过。真实运行的 JSON、CSV、图由下列入口生成。

## 文件与入口

| 文件 | 用途 |
|---|---|
| `exp8s_common.py` | batch18/19 读取适配、划分、D1 封装、目标和对象存储 |
| `exp8s_sources.py` | 缓存后过滤，B/G/H1/C 重建和 C2 验证分数 |
| `exp8s_prepare.py` | 全量缓存清单、历史核对、C2 校准和逐图数据底座 |
| `exp8s_metrics.py` | 候选/目标统计、并集 oracle 和 micro 汇总 |
| `exp8s_report.py` | 第1步曲线、第2步阶梯/来源贡献、JSON/CSV/图及中文报告 |
| `exp8s_rank.py` | `r1_key(obj)`、`rank_r1(objects, k=None)`，供第3、4步调用 |
| `exp8s_test_rank.py` | r1 的5个测试方法：来源数、面积平局、来源去重、稳定排序、不使用真值、top-K和输入不变 |
| `exp8s_run.py` | `preflight/reference/calibrate/prepare/evaluate/all` 命令入口及 `--legacy-exp8` 开关 |
| `exp8s_static_check.py` | 标准库静态检查，不导入实验模块 |
| `static_checks.txt` | 本次实际静态检查输出 |

部署文件名保持 `exp8s_*.py`，可置于 `code/probe/`；读取上游的 staging 路径为固定的 `wyh/exp6_staging/batch18/19`，不依赖本文件的相邻目录。运行环境沿用 `conda_envs/exp5-env/bin/python` 及已安装的 NumPy、SciPy、Pillow、scikit-image、Matplotlib、PyTorch（上游划分模块导入）和原 probe 模块，不安装包。运行入口禁写依赖字节码；Matplotlib 缓存放在 `--out/.matplotlib/`。H1压缩数组按图读取一次，各档复用，避免逐候选反复解码整个NPZ。

## 划分与实际输入路径

以下路径相对于 `/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/`。预测、标签、图名及 SAM3 路径直接复用 batch18 `exp8_runtime.py`，SYSU 用 batch19 的进程内适配。没有另外实现预测或标签加载函数。

| 数据集键 | 图像范围 | 检测器 / 预测目录 | SAM3 与 U5 |
|---|---|---|---|
| `second_cv` | `exp6b_common.verifier_split()[1]`，297张 | 交叉拟合 `preds/second_cv/mambascd_base` | `exp4/second_cv/sam3`；`exp8_batch18/pool/second_cv/U5_x4` |
| `second` | test 1694张 | `preds/second/mambascd_base` | `exp4/second/sam3`；`exp8_batch18/pool/second/U5_x4` |
| `sysu_val` | batch19 `frozen/sysu_val.json` 的 `validation_images`，800张，核对 pool_val | `exp8_batch19/preds/sysu_val/schanger_small` | 使用 batch19 `sam3_path()`，包括 `exp8_batch19/sam3/sysu_val`；U5 为 `exp8_batch19/pool/sysu_val/U5_x4` |
| `sysu` | test 4000张 | `preds/sysu/schanger_small` | `exp4/sysu/sam3`；`exp8_batch19/pool/sysu/U5_x4` |
| `levir` | test原图128张 | `preds/levir/schanger_base` | `exp4/levir/sam3`，路径不存在时沿用 batch18 的回退路径 |
| `whu` | test 690张 | `preds/whu/schanger_base` | `exp4/whu/sam3`，同上 |

LEVIR 校准只用 batch18 `levir_val` 的64张官方 val；预测为 `exp8_batch18/preds/levir_val/schanger_base`，SAM3 使用其 `sam3_path()`。只提取分数切点，不增加 LEVIR 验证曲线或选择工作点。WHU 不使用测试分数估分位数。

H1 路径统一为 `exp5/<dataset>/anychange/<stem>.npz`。SYSU 验证目前缺少 H1；任何划分有部分或全部 H1 文件缺失时，整个划分的 H1 标为缺失，所有涉及它的曲线与组合跳过，缺失文件逐个写入清单和报告 JSON。其余必需缓存缺失会汇总路径并报错，不会补算。SAM3/AnyChange 必需键缺失会明确失败。

已只读确认 SECOND/SYSU 的上述四处 U5 目录、两份冻结文件和两份测试 summary 存在。batch20 的 SECOND `second_cv_train` 是训练池，本任务297张验证图仍读 batch18；无需重建 U5。预检核对 SECOND/SYSU 冻结倍率均为4。U5 采用相同新增像素≥20过滤，计数单位保留为原冻结单元，不再去重；另报过滤前单元数和丢弃数，因此不得把本批的过滤后计数直接当作旧报告原始计数。

## 来源、默认档与去重

| 来源 | 单一旋钮及档位 | 默认与分数 |
|---|---|---|
| G | `prob > t`，8连通，t=0.05/0.1/0.2/0.3/0.4 | 0.05；原区域检测器均值 |
| B | 原候选最小面积0/50/100/200/500 | 0为不加过滤；原区域 `conf_t1*conf_t2` 均值 |
| H1 | 每图 top-K：全部/1/3/5/10/20 | 全部；缓存 `h1_scores`，top-K按分数降序，平局缓存原序 |
| C | 原候选最小面积0/50/100/200/500，匹配IoU固定0.5 | 0为不加面积过滤；C1用1−最佳匹配IoU，C2用区域特征距离均值 |

B 选面积旋钮，避免对两个时期语义置信度增加新的校准假设；C 也选面积，固定原匹配逻辑，所有档位只做缓存后过滤。SECOND/SYSU 的 B 为两期类别均>0且不同；LEVIR/WHU 为建筑类别5的有无异或，与 batch18 一致。

C2 的保留规则为分数≥验证部分的80%分位数，含切点平局，与 `exp4a_analyze.load()` 的方向和 NumPy 默认分位数定义一致。求切点前按 exp4 的原始候选条件（面积≥10、预测覆盖≤0.5）收集验证 C2 分数；新实验的候选域则统一改为新增像素≥20，不继续施加旧预测覆盖条件。SECOND 297张及 SYSU 800张分别校准后用于各自测试。LEVIR 用64张官方 val；WHU迁移LEVIR。若任一 LEVIR val SAM3 文件缺失，两者均不按C2分数过滤，缺失清单与替代规则写入 `calibration.json` 和各报告。若验证部分没有合格C2分数，也保守保留全部C2并记录，绝不从测试求切点。冒烟只用前N张验证图求临时切点，不能形成正式冻结值。

上游 `exp4_candidates.py` 没有独立 C 函数。为避免重写算法，本批直接导入其 `process` 和 `upsample`，用标准库 AST 从 `process` 抽取 `inst=...` 到 D 来源循环之前的原始语句，原样执行 C1/C2 逻辑，替换的仅是收集候选的 `add` 回调。不会运行其 A/D/E/F、读取语义预测头或提取新特征；静态检查确认抽出的来源只有C1/C2。上游结构变化时提取明确失败。这是对“直接导入函数”要求的本地适配，需在真实冒烟检查源缓存重建结果。

生成原候选后先应用来源旋钮，再取 `~(prob>0.5)` 部分；新增面积<20丢弃，不把被检测器分开的残余区域重新拆成多个候选。逐来源逐档记录过滤前数量、<20丢弃量和保留量。H1 的 top-K 在面积过滤前选，低分候选不补位。B/C 的面积旋钮作用于原区域，20像素规则作用于残余区域。

D1 直接调用 batch17 `groups_for()` 的 `direct_pairs` 及 `union_object()`：IoU≥0.5 或交集/小者面积≥0.8；原候选边按IoU及编号排序，每个候选最多合并一次，不传递，也不重新比较合并结果。先来源内部去重，C1/C2在此阶段合称C；再对各来源结果做一次跨源去重。输入和来源顺序固定为B、G、H1、C。

旧函数跳过同名来源且其另一个 NMS 分支只识别B/G0.05/H1。本批用临时字符串标识适配输入：内部比较以候选编号区分，跨源比较以真实来源区分；旧NMS查表看到已支持的键，其结果不使用。原函数、默认参数和直接对匹配算法均未修改；真实来源、成员和分数仍由原对象保存。历史核对同时比较适配调用和原调用的逐对象掩码，以验证适配不会改变已有B/G/H1跨源D1结果。来源内部的合并是本实验新增的明确步骤。

r1 用去重对象的独立来源数降序、`area`（新增像素面积）升序；G各档合为G，C1/C2合为C，来源重复不加分。完全平局稳定保留输入顺序，不读取真值、检测器分数或候选分数；`k=None` 全部，`k=0` 空列表。

## 指标与历史核对

- 漏检对象：复用 `objects.gt_label` 的语义转移划分，再对各GT对象的 `GT & ~pred0.5` 取8连通域，保留≥20像素（`exp4_candidates.MIN_TARGET`）。SECOND/second_cv 按两期类别转移拆分；其他数据集按二值GT。对象召回按单候选新增区域与单漏检对象的IoU计算。
- exp4 无信号对象：原始GT对象面积≥20，检测器覆盖严格<10%。保留其原始完整区域，与exp4定义相同；无信号 R_obj@0.5、R_pix另列。该像素分母包含对象内原本已检出的少量像素，候选新增区域无法覆盖它们，报告不得把这项当成全部FN召回。
- 主R_pix以该划分全部FN像素为分母，包含不足20像素的小碎片；对象指标的最小面积不改变这个像素分母。
- R_obj@0.3/0.5、AR_obj为0.3/0.4/0.5/0.6/0.7五档的对象召回平均值。分母为整个划分对象总数，不平均各图召回。
- 每图数量均值和P90；面积横轴为候选新增面积求和/图像总像素，重叠重复计数，可超过1；另报并集面积比例。小于100像素候选占比、Pos_fraction及Pos%使用所有保留候选作为分母。
- F是当前划分的检测器micro F1（按TASK的明确口径），先固定；精度 `a/(a+b)>F/2` 的候选才接受，严格不含等号。exp8参照用新增区域精度≥0.5。两者分别取接受候选新增像素并集加入原预测，再由全划分TP/FP/FN计算F1及IoU增益，单位为百分点。规则不随逐个候选加入而更新。
- Frag：IoU≥0.5命中的漏检对象平均与多少候选有≥该对象10%面积的交集。无命中对象或无候选等零分母指标写null，图中略去未定义点。

`reference --legacy-exp8` 切回原exp8条件：原面积≥10、pred覆盖≤0.5、整候选判断真值占比≥0.5，SECOND用B+G0.05+H1、SYSU用B+G0.05。直接调用 batch18/19 `raw_objects`，不走本批新增像素和内部去重流程。基线采用原保存的检测器mask。

参考数据：

| 测试集 | 原始 summary 的 objects_direct_pairs ΔF1 | 已刊显示值 | 原始文件 |
|---|---:|---:|---|
| SECOND | 4.692789313826095 | +4.69 | `exp8_batch18/pool/second/summary.json` |
| SYSU | 2.163875898446399 | +2.16 | `exp8_batch19/pool/sysu/summary.json` |

已刊值来自 `wyh/CD-Agent/results/exp8_object_pool.md`。逐图对比旧 `pool/<ds>/images/*.json` 的基线和oracle TP/FP/FN以及单元数，最终整数总计、未舍入浮点ΔF1及两位显示值均须完全一致。没有误差容许、四舍五入后放宽或跳过失败。全量通过才置 `historical_verified=true`；失败写 `reference.json` 并中止。历史SECOND必需H1若缺失，历史验收本身不能完成，会阻止正式数字；一般曲线的H1缺失仍按上述划分跳过规则执行。冒烟比较其真实子集的逐图旧计数，始终标partial，不声称复现全量值。

## 第1、2步与选择边界

第1步输出各来源全部预定档位。每划分四张图：两个纵轴 R_obj@0.5、ΔF1_oracle，分别配每图候选数和新增面积比例横轴；四来源共轴，缺H1时只有三条。SECOND/SYSU另画已冻结U5×4的参考水平线和其实际预算位置。

第2步所有来源都有默认档，无需验证集挑档。对默认档枚举可用来源的所有非空子集，分别统计完全不去重和“内部+跨源D1”的全部指标。验证部分先取默认单源ΔF1最大的来源，再按每新增候选带来的ΔF1增量贪心加入；完全平局按B/G/H1/C。新增候选数≤0但收益>0优先，否则分母至少取1/图像数，避免零分母，规则预先写定。

SECOND、SYSU测试继承验证顺序；测试出现验证缺失的来源时按B/G/H1/C固定顺序追加（典型为SYSU测试H1），不依据测试收益选顺序。LEVIR/WHU只有测试描述，固定B/G/H1/C次序，不做工作点或来源顺序选择。测试全档描述性曲线按TASK明确要求生成；这覆盖了设计文档较早的“只评冻结2–3点”限制，本批不做第5步预算选择。

两条阶梯轨迹按相同的来源加入顺序连接，各阶段由原始点向D1点画箭头，包含末端全集。来源表按内部D1单来源候选报告候选数、IoU≥0.5独有命中对象数、独有覆盖FN像素数。独有对象由该来源命中且其他单来源都未命中；独有像素由该来源并集覆盖且不被其他来源覆盖。移除来源后的ΔF1下降通过重新运行剩余来源的D1和oracle计算，可为负。

对称重复率用合并后的一个对象在参与来源间均分计数，避免任意把某一来源定为“被删方”。跨源重复率=1−分配后的对象数/内部去重后来源对象数；另报内部、总重复率。两来源成对合并各承担半个重复量。总表提供计数和分母；这不是GT命中重复率。

每个划分先落盘第1步的`step1.json/step1.csv`并记录日志，再据验证结果形成第2步来源顺序；逐图单源/组合充分统计共用一次读取。第3–6步仅交付可用数据底座和r1，不计算D2、排序AP、工作点、真实评分器或联合改预测结论。中文报告明确哪些判断问题仍需后续步骤，不填造结果。JSON/CSV同时提供`Pos_fraction`和百分数`Pos_percent`。

## 数据底座与输出格式

`--out/interim/exp8s/<dataset>/<image_stem>/` 按图存储，不把全数据集掩码放内存。每档去重前候选独立保存；局部掩码为原有 `save_units/load_units` 格式，不使用pickle。

| 文件 | 内容 |
|---|---|
| `pixels.npz` | shape、float32 probability、全图GT和0.5 base的packbits，供第3–6步离线读入 |
| `missed.{npz,json}`、`no_signal.{npz,json}` | 两类目标的bbox局部packbits、面积、GT父对象编号和语义转移 |
| `<source>_<level>.{npz,json}` | NPZ：image_shape、bboxes、offsets、packed；JSON：与掩码同序的source/sources、level、score、original_id、members、area、a、b、detector_mean及两类目标交集列表 `[目标序号,像素数]` |
| `U5.{npz,json}` | 过滤后的原冻结单元，original_id关联原U5序号 |
| `image.json` | 图名、协议、文件清单、每档丢弃数、基线TP/FP/FN、耗时；最后写入作为完成标记 |
| 上一级 `manifest.json` | 当前划分完整图名、C2切点来源、H1状态及缺失清单、基线micro整数总计 |

候选score属于原来源区域；detector_mean属于新增像素。members是当前来源档位内的局部编号；跨来源应结合sources解释，不能把不同来源的同号成员当成同一候选。目标交集可稀疏存储，没出现的目标交集为0。后续去重取并集时须重新计算几何/真值交集，不能直接相加成员的a/b。`exp8s_common.load_objects()`负责解包，`dedup()`/`combined()`负责D1；r1可直接作用于其输出。

`preflight.json`、`calibration.json`、`reference.json`、`protocol.json`保存清单和来源；`orders/{second_cv,sysu_val}.json`保存验证次序。`results/<ds>/summary.json`包含全部指标与组合；`step1.csv`、`step2.csv`、`source_table.csv`、`discarded.csv`、`u5.csv`及`report.md`便于查阅。图在`figures/exp8s/<ds>/`，标题包含数据集与val/test、横纵轴明确标注。总报告为`results/exp8s_candidate_budget.md`；`completed.json`只在全部报告与图成功后写入。本批不会改共享的`results/summary.md`；以后发布结果时可从总报告摘录追加。

prepare按完整逐图记录续跑，遇不一致配置或缺失已完成文件明确失败；换limit或协议必须使用新目录。历史核对和evaluate可重跑，未实现进程中途的逐图评估续算。单次同目录只允许一个流水线，避免并发覆盖。

## 后续 CPU 冒烟与正式提交命令（本次未执行）

沿用batch28的submit函数，改名submit30。资源先按1 CPU、4G，无GPU；分区和排除列表沿用原模板，运行前只读检查现状。`probe/run.sbatch`成功或失败均发送既有QQ完成通知，包含状态、耗时和带输出路径的命令。本批没有发送通知。

```bash
cd /guisongxia01/pangchao/wangyihan/Chang-Agent
STAGE=/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch30
SMOKE_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8s_batch30_smoke
FINAL_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8s_batch30
scontrol ping
squeue -u guisongxia
sinfo -p gre
submit30() {
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

先运行r1测试及每划分前20图的真实缓存冒烟。预检仍检查全划分，避免冒烟子集掩盖H1缺失；LEVIR校准缺失只启用已授权替代。冒烟不保存模型或checkpoint。

```bash
t=$(submit30 b30-r1 00:05:00 1G '' "$SMOKE_OUT" "$STAGE/exp8s_test_rank.py")
s=$(submit30 b30-smoke 04:00:00 4G "$t" "$SMOKE_OUT" \
  "$STAGE/exp8s_run.py" all --limit 20)
# 作业完成后检查实际状态和峰值；本次未执行。
sacct -u guisongxia -j "$t,$s" --format=JobID,State,Elapsed,MaxRSS,ReqMem
```

冒烟验收：5个r1测试方法通过；两测试子集逐图旧整数计数和D1适配掩码相等；6个划分各20张的底座、指标表、四张单源图及四张阶梯图完整；H1缺失明确标注；C2迁移/替代来源可追溯；每图variants与文件一致；无新模型/自动分割计算。`reference.json`必须`partial=true,historical_verified=false`。冒烟不通过时依赖链停止，不提交正式计算。

正式全量分阶段提交，便于检查耗时和资源；所有数字必须依赖全量历史核对。确认冒烟之后再执行以下命令。

```bash
p=$(submit30 b30-preflight 01:00:00 4G '' "$FINAL_OUT" \
  "$STAGE/exp8s_run.py" preflight)
r=$(submit30 b30-reference 12:00:00 4G "$p" "$FINAL_OUT" \
  "$STAGE/exp8s_run.py" reference --legacy-exp8)
c=$(submit30 b30-calibrate 04:00:00 4G "$r" "$FINAL_OUT" \
  "$STAGE/exp8s_run.py" calibrate)
b=$(submit30 b30-prepare 24:00:00 4G "$c" "$FINAL_OUT" \
  "$STAGE/exp8s_run.py" prepare)
e=$(submit30 b30-evaluate 48:00:00 4G "$b" "$FINAL_OUT" \
  "$STAGE/exp8s_run.py" evaluate)
sacct -u guisongxia -j "$p,$r,$c,$b,$e" --format=JobID,State,Elapsed,MaxRSS,ReqMem
```

正式验收还要求：297/1694/800/4000/128/690张计数完整；历史两项ΔF1逐位一致且`historical_verified=true`；正式C2切点只来自完整验证部分或已记录的授权替代；测试无工作点选择；全部必需字段及U5对照存在；最后有`completed.json`。没有GPU或外部模型预算需求。

### 追加：补齐 H1 后仅重跑 SYSU 验证与测试

此补丁增加 `--datasets`（逗号分隔）与 `--reference-from <reference.json>`。未传新参数时仍按原六划分、原遍历顺序和原协议字段运行，`VERSION`不变；原命令和已运行的`exp8s_batch30`输出继续使用。阶段函数兼容旧进程中不带新属性的`args`，未调整候选、去重、指标或历史核对算法。

`preflight/calibrate/prepare/evaluate`只处理所选划分及其必要验证依赖。`sysu_val`与`sysu`必须同时出现，仅传其中一个报错；顺序可交换，程序按固定顺序处理，evaluate先验证再测试。SECOND测试同样需要显式包含`second_cv`；LEVIR/WHU保留原有`levir_val`切点依赖。未知名、重复名和空项报错。子集与reference来源路径写入新目录的`protocol.json`，所有阶段须传相同参数，避免混用输出。

`reference`及`--legacy-exp8`仍完整核对SECOND/SYSU，不按`--datasets`缩减。未增加`--skip-reference`：新目录可以用`--reference-from`只读复用原全量通过记录，无需本地`reference.json`；仍检查版本、passed、partial、historical_verified及两测试集图名范围。该复用适用于`--limit 0`，全量记录不能放行冒烟范围。原记录不复制、不改写，绝对来源路径保存在新协议中。`all --reference-from ...`也会复用该记录而省略重算reference；单独调用`reference`仍执行原核对。

本次已只读确认`exp8s_batch30/reference.json`的`version=1`、`passed=true`、`partial=false`、`historical_verified=true`，SECOND1694张、SYSU4000张均`full_match=true`。这些是既有结果，补丁未重新计算。原H1缺失时跳过该划分的行为保留；新流程必须等batch31的800张H1缓存完成，再预检并确认`h1_available=true`，随后处理验证、测试，不能提前把缺失状态写入新底座。

以下追加命令沿用上文`submit30`函数和`STAGE`，本次未执行。先在shell中把`BATCH31_H1_JOBS`设为实际负责完成800张缓存的全部作业ID（多个ID用冒号分隔）；afterok依赖确保补跑成功后再预检。

```bash
SYSU_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8s_batch30_sysu
REFERENCE_FROM=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8s_batch30/reference.json
: "${BATCH31_H1_JOBS:?请先设置batch31的H1作业ID，多个用冒号分隔}"
p=$(submit30 b30-sysu-preflight 01:00:00 4G "$BATCH31_H1_JOBS" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" preflight --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
```

等预检成功完成后，先用下面的只读JSON检查确认两划分均有完整H1，再提交后续依赖链。检查失败时不执行后续提交，应先完成缓存并重跑预检；这里不修改程序原有的H1缺失处理方式。

```bash
/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python - "$SYSU_OUT/preflight.json" <<'PY_CHECK'
import json
import sys
from pathlib import Path
rows = json.loads(Path(sys.argv[1]).read_text())
assert set(rows) == {'sysu_val', 'sysu'}
for ds, count in (('sysu_val', 800), ('sysu', 4000)):
    row = rows[ds]
    assert len(row['images']) == count, ds
    assert row['h1_available'] and not row['h1_missing'], ds
    assert not row['missing'] and not row['invalid'], ds
print('SYSU validation/test H1 caches complete')
PY_CHECK
```

确认上面的检查成功后，在同一shell继续：

```bash
c=$(submit30 b30-sysu-calibrate 04:00:00 4G "$p" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" calibrate --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
b=$(submit30 b30-sysu-prepare 24:00:00 4G "$c" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" prepare --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
e=$(submit30 b30-sysu-evaluate 48:00:00 4G "$b" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" evaluate --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
sacct -u guisongxia -j "$p,$c,$b,$e" --format=JobID,State,Elapsed,MaxRSS,ReqMem
```

新目录只生成`sysu_val`与`sysu`底座、曲线、来源表和报告；C2切点只用800张验证，四来源次序也先在验证确定再用于测试。`completed.json`中的datasets应为`["sysu_val", "sysu"]`。原目录不覆盖，原来的无H1验证结果保持原样。本次静态检查记录仍为`static_checks.txt`；`patch_static_checks.txt`另记录修改前后AST对比，确认reference函数、默认协议字面量和非本补丁目标函数保持不变。没有运行子集实验或提交Slurm。

### 追加：SYSU prepare 八分片并行版本

`prepare`新增`--shard i --shards n`，编号从0开始，每个划分按其完整图名顺序的零基序号`index % n == i`取图；设置limit时先截取前N张再分片。默认`--shard 0 --shards 1`与原流程相同。只有prepare接受非默认分片参数和`--finalize`；要求`n>=1`及`0<=i<n`，finalize使用`--shard 0`（默认）。旧进程没有这些参数属性时仍按0/1处理。

分片只写自己负责的图像目录及其中的临时文件，不写manifest。入口也只读核对已由串行preflight/calibrate建立的`protocol.json`，避免分片竞争原来固定名称的协议临时文件；协议缺失时明确报错。分片参数不写入协议或image.json，全部分片保存相同的完整划分配置，逐图结果格式和原单作业完全相同。分片开始后，preflight、calibrate及其输出保持固定。

八个分片均成功后，单独运行一次`prepare --finalize --shards 8`。该步骤遍历所选划分的全部图像，检查每张image.json完成标记、配置、图名及其列出的文件是否存在，汇总基线TP/FP/FN，然后逐划分写入原格式manifest。缺标记或缺文件即失败，finalize不会补算图像。manifest计数覆盖该划分全部图像，不能用某个分片的小计代替。finalize是唯一manifest写入者，evaluate依赖其成功完成。

同一次并行prepare须使用同一组datasets、limit、shards和reference来源；同一分片编号只启动一个作业。重跑失败分片会复用原有完整image.json、重做没有完成标记的图；等全部分片结束后再finalize。需要更改分片数时，先结束原来的所有prepare作业。串行版与以下并行版选择一套执行。先前“同目录一个流水线”的限制在这里允许同一流水线内不同prepare分片并行，其余阶段仍依次执行。

**evaluate仍为单作业，未增加分片统计或合并逻辑，`exp8s_report.py`保持原样。** reference、来源生成、指标计算和原默认协议也不变，正在运行的原正式evaluate继续使用相同接口。

以下命令沿用`submit30`（每作业1 CPU）、`STAGE`和原reference路径。输出仍为新SYSU目录。每片预算1CPU/4G、6小时；八片同时运行合计8CPU/32G，耗时为预算，尚未实测。finalize预算1小时/4G，evaluate沿用48小时/4G。本次未提交这些作业。

```bash
SYSU_OUT=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8s_batch30_sysu
REFERENCE_FROM=/guisongxia01/pangchao/wangyihan/Chang-Agent/outputs/CD_1016_probe/exp8s_batch30/reference.json
: "${BATCH31_H1_JOBS:?请先设置batch31的H1作业ID，多个用冒号分隔}"
p8=$(submit30 b30-sysu-preflight 01:00:00 4G "$BATCH31_H1_JOBS" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" preflight --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
```

等预检成功后，执行上一节的只读JSON完整性检查，确认sysu_val的800张和sysu的4000张H1均完整。检查通过后，继续提交calibrate、八个prepare分片、finalize及单作业evaluate：

```bash
set -euo pipefail
c8=$(submit30 b30-sysu-calibrate 04:00:00 4G "$p8" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" calibrate --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
prepare_deps=''
for shard in 0 1 2 3 4 5 6 7; do
  b8=$(submit30 "b30-sysu-prepare-$shard" 06:00:00 4G "$c8" "$SYSU_OUT" \
    "$STAGE/exp8s_run.py" prepare --datasets sysu_val,sysu \
    --reference-from "$REFERENCE_FROM" --shard "$shard" --shards 8)
  prepare_deps="${prepare_deps:+$prepare_deps:}$b8"
done
f8=$(submit30 b30-sysu-finalize 01:00:00 4G "$prepare_deps" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" prepare --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM" --finalize --shards 8)
e8=$(submit30 b30-sysu-evaluate 48:00:00 4G "$f8" "$SYSU_OUT" \
  "$STAGE/exp8s_run.py" evaluate --datasets sysu_val,sysu \
  --reference-from "$REFERENCE_FROM")
sacct -u guisongxia -j "$p8,$c8,${prepare_deps//:/,},$f8,$e8" \
  --format=JobID,State,Elapsed,MaxRSS,ReqMem
```

正式全量时每片处理100张sysu_val、500张sysu；八片不重叠且覆盖800/4000张。验收检查：分片阶段没有生成不完整manifest；finalize能发现缺失完成标记并失败；两份完整manifest的baseline分别是全部800/4000张计数之和；evaluate最终仍只输出sysu_val/sysu。分片并行及finalize真实运行未执行，本次只做静态检查和不导入实验模块的CLI诊断。`shard_static_checks.txt`记录此次修改前后AST核对与CLI检查结果；默认的图像恢复/生成保存语句、manifest内容和非prepare函数保持不变。

## 资源估计与待确认项

batch22、24、28的README引用过batch20/21约1.3G峰值量级，故本批先申请1CPU、4G。**当前读取的文件未找到可复核的MaxRSS原始账单，本批也未运行；这里不是本批实测内存声明。** 按图保留紧凑局部掩码；C收集阶段也立即裁成局部掩码。预计4G可作为冒烟起点，正式内存必须用上述`sacct`实测MaxRSS量级校正，例如按实测约1.3G申请4G保留解释器和临时数组空间，不能直接沿用未知来源的大内存请求。磁盘包含6个划分的逐图概率、约21档局部掩码及JSON，需从冒烟实际占用按图数外推。

预留时间是预算：预检1小时、历史核对12小时、切点4小时、准备24小时、全部指标/作图48小时。D1和目标交集是逐图候选配对计算，H1密集图可能主导时间；完整组合的评估约15种×两种去重状态，需按冒烟耗时重新估计。未运行作业，无法提供本批MaxRSS、CPU耗时或新指标。

待确认项（按保守、可追溯规则继续实现，不等待额外确认）：

1. 上游逐图缓存完整性、C AST适配与四来源D1真实运行是否满足验收；这些必须由真实缓存冒烟验证。
2. 来源内部的D1输入标识适配、Frag分母、对称重复率、U5应用统一20像素过滤的口径均已明确记录；后续比较应保持同一定义。
3. C默认档对齐exp4a的匹配与前20%分数规则，同时按本批要求迁移验证切点和改用新增区域；它不应复现exp4a使用测试集分位数的旧完整结果。
4. SYSU验证H1缺失使其优先级不能从验证选择；测试只按预定顺序追加。WHU迁移LEVIR切点及缺失时保留全部C2均写入正式报告。
5. SECOND交叉拟合到官方检测器的概率偏移、SYSU相关小图与权重选优来源限制继续保留。测试曲线是描述性统计，不能用于选择第5步工作点。
6. 全量历史一致性、分母、空候选图、实际峰值及磁盘占用均待允许的CPU运行确认；本次未用模拟样本替代这些验收。

本目录静态复查：

```bash
cd /guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch30
PYTHONDONTWRITEBYTECODE=1 /guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python \
  exp8s_static_check.py > static_checks.txt
```
