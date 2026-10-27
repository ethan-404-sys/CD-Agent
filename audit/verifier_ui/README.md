# MLLM 判断器审核前端

只覆盖“MLLM 做判断器”这一环：提示词版本、ICL 示例（零样本 / 少示例 / 多示例 / 多示例批量）、模型原始输出与准确率、示例数量对准确率的影响。候选生成、分类和调度智能体不在范围内。

架构：服务器上一个 SQLite 文件（`verifier.db`）是唯一的数据源；推理脚本通过 `store.RunRecorder` 写库，`app.py`（FastAPI）只读，`static/index.html` 是单页前端。图片不打包，按库里登记的路径直接从服务器磁盘读。

## 启动

```bash
pip install fastapi uvicorn numpy pillow
cd audit/verifier_ui
VERIFIER_DB=/path/to/verifier.db uvicorn app:app --port 8765
# 本机：ssh -L 8765:localhost:8765 <服务器>，然后打开 http://localhost:8765
```

没有服务器数据时可以先看合成数据：`python demo_fixture.py /tmp/vdemo`，再用 `VERIFIER_DB=/tmp/vdemo/verifier.db` 启动。合成数据里的图像和分数都是编出来的。

## 导入已有的零样本结果

```bash
python import_existing.py --db verifier.db exp3 --tag <mllm_<tag>.jsonl 的 tag> --model qwen3.5-4b
python import_existing.py --db verifier.db exp6j --run-dir <exp6j 的 run 目录>
```

重复导入会覆盖同名运行。exp6j 只有前向读数没有生成，准确率按 P(yes) > 0.5 计算。

## 新实验怎么写库

```python
from store import RunRecorder, icl_template
with RunRecorder('verifier.db', name='icl_second_k8_s0', model='qwen3.5-4b',
                 prompt_name='general_v3', prompt_text=PROMPT, demos=demos, mode='few',
                 strategy='按类别均衡随机', seed=0, template=icl_template(len(demos), PROMPT)) as rec:
    for q in queries:
        rec.record(q, p_yes=p, answer=ans, confidence=conf, raw_text=text)
```

- `demos`、`queries` 都是样本字典：`dataset`、`split`、`cid`、`image`、`source`、`label`、`inputs`（`{"t1": 路径, "t2": 路径, ...}`），其余字段进 `meta`。
- `template` 是实际发给模型的消息序列，图像写成引用（`demo[3].t2`、`query[0].t1`），示例答案写成 `{"type": "text", "ref": "demo[3].answer"}`。脚本的排布和 `icl_template` 不同时，传自己的模板，前端按模板原样还原。
- 多示例批量：每次调用用 `rec.record_batch(queries, raw_text, per_query=[...])`，整段原始输出存一次，每个查询记批次和位置。
- 提示词按全文哈希去重：文本一样就是同一个版本，改一个字就是新版本；可以用 `prompt_parent` 记它从哪个版本改来。

## 数据表

| 表 | 内容 |
| --- | --- |
| `prompt_version` | 最终提示词全文、哈希 id、父版本、备注 |
| `sample` | 所有出现过的裁块（查询或示例），带数据集、划分、原图、标签、图像路径 |
| `demo_set` / `demo_set_item` | 示例集：模式、k、选取策略、种子；每个示例的顺序和提示中写的答案 |
| `run` | 模型、提示词、示例集、读数方式、视图 / 拼图方式、批大小、消息模板、git 提交号、完整配置 |
| `call` | 批量调用的原始输出（一次调用回答多个查询时才用） |
| `output` | 每个查询的 P(yes)、logit 差、yes/no 概率质量、解析后的回答、置信度、原始文本 |

指标不入库，读的时候算：准确率优先用生成的回答，没有时用 P(yes) > 0.5；AUC 用 P(yes)；CI 为 2000 次自助法（与 `exp3_analyze.py` 一致）。

## 前端四个视图

1. **提示词**：版本列表和全文；按住 Shift 点第二个版本做逐词对比。
2. **ICL 示例**：每个示例集的模式、k、正例数、来源数据集与划分，逐个展示示例裁块和提示里写的答案；来自 test 或答案与标签不符会标红。
3. **运行与输出**：所有运行按查询数据集列出准确率、AUC、答“是”比例和示例来源（同域 / 跨域 / 混合）；点进去看实际发给模型的输入（批量调用会显示同批的其他查询）、泄漏检查（示例来自 test、查询与示例同一张原图），以及原始输出表，可按数据集、候选来源、答错 / 误报 / 漏报筛选，答错且越自信的越靠前。
4. **示例数量曲线**：按查询数据集分面，横轴为示例数（对数刻度），颜色区分少示例 / 多示例 / 多示例批量，实线为同域示例、虚线为跨域，灰色虚线为零样本参照，竖线为 95% CI；下方附数据表。
