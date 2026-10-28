# MLLM 判断器审核前端

只覆盖“MLLM 做判断器”这一环：提示词版本、ICL 示例（零样本 / 少示例 / 多示例 / 多示例批量；示例形式 A / B / A+B）、示例池审核、模型原始输出与指标、示例数量对指标的影响。候选生成、分类和调度智能体不在范围内。exp6k 起的 ICL 实验都在这里审，不再另做审核页。

架构：服务器上一个 SQLite 文件（`verifier.db`）是唯一的数据源。准备脚本登记示例池、开发集和测试集，推理脚本通过 `store.RunRecorder` 写库；`app.py`（FastAPI）读库，只写审核结果和指标缓存；`static/index.html` 是单页前端。图片不打包，按库里登记的路径直接从服务器磁盘读。

## 启动

```bash
pip install fastapi uvicorn numpy pillow
cd audit/verifier_ui
VERIFIER_DB=/path/to/verifier.db uvicorn app:app --port 8765
# 本机：ssh -L 8765:localhost:8765 <服务器>，然后打开 http://localhost:8765
```

没有服务器数据时可以先看合成数据：`python demo_fixture.py /tmp/vdemo`，再用 `VERIFIER_DB=/tmp/vdemo/verifier.db` 启动。合成数据里的图像、标签和分数都是编出来的，故意放了两处划分违规，用来展示检查。

指标第一次打开时计算（每个运行每个数据集约 1–2 秒，含 2000 次自助法），之后缓存在库里的 `metric_cache` 表。样本标签或权重改了之后，删掉这张表的内容即可重算。

## 写库：样本集（exp6k 第 4 部分）

```python
import store
db = store.connect('verifier.db')
with db:
    store.add_sample_set(db, 'exp6k_demos_second', 'demo_pool', 'second', demos)   # 只能来自 train
    store.add_sample_set(db, 'exp6k_dev_second', 'dev', 'second', dev)            # 只能来自 val
    store.add_sample_set(db, 'exp6k_test_second', 'test', 'second', test)         # 只能来自 test
    store.add_prompt(db, 'second_sys_v1_zh', SYSTEM_ZH, note='出处：……；归纳自训练难负例：……')
```

每个样本是一个字典：

| 键 | 含义 |
| --- | --- |
| `dataset`、`split`、`cid`、`image` | 数据集、划分（train / val / test）、单元 id、原图名。划分检查和“同图”判断都按原图 |
| `label` | 1 有变化，0 无变化（伪变化也是 0） |
| `rationale` | 一句理由。正例写变化内容，负例写伪变化类型，例如“屋顶换色，属于无变化” |
| `pseudo_type` | 负例的伪变化类型（季节 / 物候、光照与阴影、色调 / 成像差异、屋顶换色或翻新、车辆等临时物体、配准错位……） |
| `change_kind` | SECOND 正例的“t1 类别 → t2 类别” |
| `semantic_text` | SECOND 两期语义的文字说明，随 B 形式给出。若同时给了 `semantic_t1` / `semantic_t2`（候选区域两期类别直方图 `[{name, fraction}, ...]`），登记和显示时都改用 `store.semantic_line` 的格式：第一行是候选区域，例如“T1 非植被地表 → T2 树木（占候选 74%）”；给了 `semantic_full`（整图两期直方图）时第二行是整图两期主要类别。“只在变化区提供地类”这类说明放在悬停提示里，不进文字。原文存进 `semantic_text_long`。评测脚本给模型的 B 形式文字也请用 `store.semantic_line`，和前端保持一致 |
| `area`、`source`、`det_mean`、`m1_score` | 新增像素面积、候选来源、检测器均值 D、M1-ign3 分数（D 和 M1-ign3 是参照线的分数） |
| `bbox` | 候选在整图中的 `[x0, y0, x1, y1]`，用来画整图位置缩略图 |
| `inputs` | 图像路径：A 形式 `t1`、`t2`（近景，红色轮廓）；B 形式 `full_t1`、`full_t2`、`mask`；可选 `thumb`（预渲染的位置缩略图，不给就用 `full_t2` + `bbox` 现画） |
| `stratum`、`weight` | 分层（`pos2` ② 正例、`pos_other` 其他正例、`hard_neg` 难负例、`rand_neg` 随机负例）和抽样权重（抽样概率的倒数） |

其余键存进 `meta`。重复登记同一个样本集会更新分层和权重，不会碰审核结果。

## 写库：评测运行

```python
from store import RunRecorder, icl_template
with RunRecorder('verifier.db', name='second_dev_plus_AB_k8_s0', model='qwen3-vl-plus',
                 prompt_name='second_sys_v1_zh', prompt_text=SYSTEM_ZH, demos=demos,
                 mode='few', form='AB', pool='exp6k_demos_second', eval_set='exp6k_dev_second',
                 readout='verbal_confidence', strategy='按类别均衡随机', seed=0,
                 template=icl_template(len(demos), SYSTEM_ZH, form='AB')) as rec:
    for q in queries:
        rec.record(q, answer='no', confidence=80, raw_text=text,
                   usage=dict(input_tokens=..., output_tokens=..., cost=...))
```

- `mode`：`zero` / `few` / `many` / `batched_many`；`form`：`A` / `B` / `AB`。
- `readout`：`token_prob`（有 P(yes)）、`verbal_confidence`（只有口头置信度，例如百炼接口拿不到词元概率时）、`answer_only`。AUC / AP 的分数依次用 P(yes)、口头置信度换算的 P(yes)、回答本身。
- `usage` 记一次 API 调用的输入 / 输出词元数和费用（默认币种 CNY），运行结束时汇总到 `run` 表。
- 批量提问：同一张原图的全部候选放一次调用，用 `rec.record_batch(queries, raw_text, per_query=[...], group_key=image, usage={...})`，并在 `RunRecorder` 里写 `batch_by='image'`。整段原始输出存一次，每个候选记批次和位置。
- `template` 是实际发给模型的消息序列，图像写成引用（`demo[3].full_t2`、`query[0].t1`），示例答案写成 `{"type": "text", "ref": "demo[3].answer"}`（前端显示答案和理由），SECOND 语义写成 `demo[3].semantic`。脚本的排布和 `icl_template` 不同时，传自己的模板即可。
- 提示词按全文哈希去重，可以用 `prompt_parent` 记它从哪个版本改来。

已有的 exp3 / exp6j 零样本结果可以用 `import_existing.py` 导入（这些运行没有关联样本集，在前端的“未指定样本集”里）。

## 提示词分组与迁移

`prompt_version` 新增可空字段 `grp`（分组键）、`title`（中文显示名）、`kind`（`template` / `rendered`，空值按模板显示）。`store.connect` 打开旧库时自动补列，已有记录的新字段保持空值；`add_prompt` 可选传入同名参数，原调用方式继续可用。`name`、`id` 和父版本关系保持不变。

`GET /api/prompts` 保留原字段，并返回这三个字段及 `rendered_ids`：模板对应的直接渲染实例 id 列表，渲染条目自身返回空列表。分组顺序为 SYSU、SECOND、SYSU + SECOND 共用、LEVIR、WHU、LEVIR + WHU 共用、未分组，对应键为 `sysu`、`second`、`sysu_second`、`levir`、`whu`、`levir_whu`、空值。

用数据库副本核对现有提示词的名称映射，再按需应用（在本目录执行）：

```bash
python migrate_prompt_meta.py --db /path/to/copy.db          # 只读打印对照表
python migrate_prompt_meta.py --db /path/to/copy.db --apply  # 备份后写入
```

写入前创建 `<db>.bak-prompt-meta`；需要写入时若备份已存在则报错，绝不覆盖。所有字段已一致时重复执行不写库、不再备份。未匹配名称、渲染实例缺少模板父版本或分组与父模板不符都会报错并停止。迁移只更新这三个字段。

## 审核与导出

在“样本集与审核”页逐个看示例：点“通过 / 剔除”，“撤销”回到未审，备注框按回车保存，结果直接写回库里。审核结果按样本存（`sample_review` 表），同一个样本在 64 池和 256 池里共享：任一池里审过，另一池同步显示，导出也按共享结果。旧库里按池存的审核结果在第一次连接时自动迁移过来（同一样本在多个池里审过时取最近一次）。评测前导出：

```bash
python export_demos.py --db verifier.db --set exp6k_demos_second --out demos_second.json             # 去掉剔除的
python export_demos.py --db verifier.db --set exp6k_demos_second --out demos_second.json --only-passed  # 只要通过的
```

## 指标

- **主指标**：② 正例 vs 全部补漏负例的 AUC / AP，按抽样权重还原。另列全部正例的 AUC / AP 和加权准确率。
- **分层**：只分四栏，② 正例、其他正例（①③ 合并）、难负例、随机负例，每栏给不加权的准确率和答“是”比例。
- **参照**：同一批样本上的 D（检测器均值）和 M1-ign3 分数，作为表格里的参照行和曲线上的点线。
- **CI**：2000 次按原图整群自助法（与 exp8s3 一致）。
- 准确率优先用生成的回答，没有时用 P(yes) > 0.5。
- 开发集 / 测试集用页面顶部的切换按钮切换，依据是运行的 `eval_set`。

## 划分检查

示例池只能来自 train，开发集来自 val，测试集来自 test，三者（以及运行用过的示例集）按原图互不重叠。违反时每页顶部标红，单个示例和运行详情里也会标出；示例池单图超过 2 个示例也会标出。

## 数据表

| 表 | 内容 |
| --- | --- |
| `prompt_version` | 最终提示词全文、哈希 id、父版本、备注（出处） |
| `sample` | 所有出现过的候选（示例或查询）及上面那张表里的字段 |
| `sample_set` / `sample_set_item` | 示例池、开发集、测试集；每个样本在集合里的分层和权重 |
| `sample_review` | 每个样本的审核结果（通过 / 剔除 / 备注），所有示例池共享 |
| `demo_set` / `demo_set_item` | 某次运行实际用的示例：模式、形式、k、来自哪个示例池、选法、种子、顺序和提示中写的答案 |
| `run` | 模型、提示词、示例集、评测样本集、读数方式、批量方式、调用次数、词元数、费用、消息模板、git 提交号、完整配置 |
| `call` | 批量调用的原始输出和该次调用的用量 |
| `output` | 每个查询的 P(yes)、logit 差、yes/no 概率质量、解析后的回答、置信度、原始文本 |

## 前端五个视图

1. **提示词**：左侧只列模板，按数据集分为可折叠小节，组内按创建时间排序；主行显示中文名，小字显示原名、id 和运行次数。右侧查看全文、父版本和可展开的渲染实例，实例可与模板逐词对比；按住 Shift 点第二个模板仍可对比。点击模板只更新详情和选中高亮，切换页面时在当前会话保存列表滚动位置和分组折叠状态。未迁移元数据的旧库全部显示在“未分组”。
2. **样本集与审核**：示例池、开发集、测试集的构成统计（正负比例、分层、伪变化类型、变化种类、面积档、来源、划分、单图最多几个），每个样本显示 A 形式近景、整图位置缩略图、可展开的 B 形式（整图、掩码、SECOND 语义）、理由和各字段；示例池可以直接审。
3. **ICL 示例集**：某次运行实际用的示例、顺序和构成。
4. **运行与输出**：按数据集分组，先列 D 和 M1-ign3 参照行，再列各运行的 ② AUC / AP、全部 AUC / AP、准确率、四栏分层、调用次数和费用。点进去看实际发给模型的输入（批量调用会显示同一张图的其他候选）、划分与泄漏检查，以及原始输出表，可按数据集、分层、候选来源、答错 / 误报 / 漏报筛选。
5. **示例数量曲线**：按数据集分面，横轴为示例数（对数刻度），颜色区分少示例 / 多示例 / 多示例批量，线型区分示例形式 A / B / A+B；零样本、D 和 M1-ign3 画成灰色参照线；可以切换 ② AUC、② AP、全部 AUC / AP 和准确率。
