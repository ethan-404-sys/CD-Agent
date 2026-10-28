# 开放词汇变化检测及相邻任务文献调研

调研日期：2026-10-28。资料来自两处：遥感 Agent 知识库中已入库的论文原文（`raw/01-论文/*/source.md`），以及本次通过 arXiv API 检索并读取 arXiv HTML 全文的论文。每条出处标注论文简称和章节、表格或图编号；论文简称与来源的对应关系见文末“参考来源”。标“推断”的内容是根据多篇证据得出的判断，原文没有直接写出。

指标记号：F1^c、IoU^c 指“变化类”的 F1 和 IoU（只对查询类别的变化像素计算）。除非特别说明，数字都是原论文自己报告的值。

## 总表

| 标题 | 年份 | 会议或期刊 | 任务定义 | 输入 | 输出 | 数据集 | 指标 | 关键数字 | 是否开源 |
|---|---|---|---|---|---|---|---|---|---|
| DynamicEarth: How Far are We from Open-Vocabulary Change Detection? | 2025（arXiv）/2026 | AAAI 2026 Oral | OVCD，免训练的 M-C-I 与 I-M-C 两个框架 | 两期影像 + 类别文本 | 该类别的二值变化掩码 | LEVIR-CD、WHU-CD、S2Looking、BANDON、SECOND | 逐类 IoU^c、F1^c | F1^c：LEVIR 69.7、WHU 75.8、S2Looking 38.5、BANDON 30.2；SECOND 建筑 55.2 [DynamicEarth 表1、表2] | 代码开源，含各数据集、各类别评测脚本；免训练，无自有权重 |
| Segment Any Change（AnyChange） | 2024 | NeurIPS 2024 | 零样本类别无关变化检测；加单击点查询后得到以目标为中心的变化 | 两期影像（可加点） | 类别无关变化掩码 | LEVIR-CD、S2Looking、xView2、SECOND | 像素 F1、P、R；mask AR@1000 | SECOND 零样本 F1 44.6（ViT-B）[AnyChange 表6]；单次点查询使三个建筑数据集 F1 提高 15.4–15.7 [AnyChange 表4] | 代码在 torchange 库中 |
| Segment Change Model（SCM） | 2023 | IGARSS 2024 | 建筑无监督变化检测（FastSAM + CLIP 语义注意力） | 两期影像 | 建筑变化掩码 | LEVIR-CD、WHU-CD | mIoU；F1^c | F1^c：LEVIR 31.7、WHU 31.3 [DynamicEarth 表1] | 代码开源（StephenApX/UCD-SCM） |
| OmniOVCD: Streamlining OVCD with SAM 3 | 2026 | arXiv 预印本 | 免训练 OVCD，只用 SAM 3，融合语义、实例、存在三个输出头后拆成实例比较 | 两期影像 + 类别文本 | 两期变化实例的并集 [OmniOVCD 式6] | LEVIR-CD、WHU-CD、S2Looking、SECOND | IoU、F1 | F1：LEVIR 80.4、WHU 79.9、S2Looking 39.4；SECOND 六类平均 41.8 [OmniOVCD 表1、表2] | 代码开源；使用 SAM 3 官方权重 |
| AdaptOVCD | 2026 | arXiv 预印本 | 免训练 OVCD，SAM-HQ + DINOv3 + DGTRS-CLIP，数据、特征、决策三层自适应 | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、WHU-CD、DSIFN、SECOND | P、R、IoU^c、F1^c | F1^c：LEVIR 68.00、WHU 76.53、DSIFN 59.47、SECOND 建筑 63.81 [AdaptOVCD 表3、表4] | 代码开源 |
| OpenDPR / OpenDPR-W | 2026 | CVPR 2026 | OVCD；扩散模型离线生成类别原型，在视觉空间检索识别；W 版加弱监督定位模块 S2C | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、WHU-CD、Hi-UCD mini、SECOND | IoU、F1 | OpenDPR F1：LEVIR 61.9、WHU 70.4；OpenDPR-W：65.0、81.2；SECOND 逐类推理 mF1 40.5 [OpenDPR 表1、表2、表4] | 推理代码开源；OpenDPR-W 只提供 S2C 推理结果文件 |
| CoRegOVCD | 2026 | arXiv 预印本 | 免训练稠密推断，把类别变化写成校准后验之差 | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、WHU-CD-256、DSIFN、SECOND 验证划分 | P、R、IoU^C、F1^C（按 OR 规则） | F1^C：LEVIR 83.01、WHU 82.14、DSIFN 64.45；SECOND 平均 47.50 [CoRegOVCD 表1、表2] | 未找到代码 |
| MemOVCD | 2026 | arXiv 预印本 | 免训练，把双时相写成两帧跟踪，双向记忆传播 + 全局局部校正 | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、DSIFN、S2Looking、BANDON、SECOND | IoU^c、F1^c | F1^c：LEVIR 84.1、DSIFN 54.9、S2Looking 41.3、BANDON 37.7 [MemOVCD 表1] | 仓库只有 README，写明录用后发布 |
| ReA-OVCD | 2026 | arXiv 预印本 | 免训练像素级 OVCD，语义与空间两阶段可靠性评估 | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、WHU-CD、DSIFN、SECOND（1694 对） | P、R、IoU^C、F1^C | F1^C：LEVIR 84.52、WHU 90.31、DSIFN 67.79；SECOND 平均 47.19 [ReA-OVCD 表II、表III] | 代码开源（基于 SAM 3） |
| Zero-OVCD | 2026 | arXiv 预印本 | 两阶段：冻结 SAM3、DINOv3、SegEarth-OV3 生成伪标签并补漏；再用伪标签在目标域无标注影像上训练 ChangerEx | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、WHU-CD、S2Looking、SECOND（593 对） | P、R、F1、IoU | 第一阶段 F1：86.25、85.82、50.48；第二阶段 88.65、88.85、57.96；SECOND 平均 F1 47.91→50.92 [Zero-OVCD 摘要、表I、表II] | 仓库只有 README 和两个第二阶段配置文件 |
| UniVCD | 2025 | arXiv 预印本 | 无监督 OVCD：冻结 SAM2 与 CLIP，无标注训练轻量特征对应模块 | 两期影像 + 类别文本 | 类别变化掩码 | LEVIR-CD、WHU-CD、SECOND | F1^c、IoU^c、mIoU | F1^c：LEVIR 72.3、WHU 79.5；SECOND 建筑 60.4 [UniVCD 表I、表II] | 未发布，README 写明录用后发布 |
| Seg2Change | 2026 | arXiv 预印本 | 训练类别无关变化头 CACH，把开放词汇语义分割模型改造成 OVCD | 两期影像 + 类别文本 | 类别变化掩码 | WHU-CD、LEVIR-CD、DSIFN、CLCD、SECOND、SC-SCD；训练集 CA-CDD | F1^c、IoU^c、Kappa | F1^c：WHU 86.18、LEVIR 78.72、DSIFN 58.56；SECOND mF1^c 42.89 [Seg2Change 表1、表2] | 代码与 CACH 权重开源 |
| CogVis | 2026 | arXiv 预印本 | 感知、记忆、核验三步；冻结 SAM3 与 CLIP，训练三个轻量适配器 | 两期影像 + 类别文本 | 类别变化掩码 | SECOND、SCSCD、CLCD、DSIFN、LEVIR-CD、WHU-CD、xBD | mIoU、IoU | SECOND mIoU 32.48；IoU：LEVIR 72.55、WHU 75.86 [CogVis 表1] | 代码开源；README 写明权重不放在仓库中，未找到下载链接 |
| Semantic-CD | 2025 | IGARSS 2025（据 AdaptOVCD 参考文献[10]） | 借用 CLIP 开放词汇语义的语义变化检测，在 SECOND 上训练和测试 | 两期影像 | 二值变化 + 语义标签 | SECOND | OA、F1、mIoU、SeK | SeK 23.85、mIoU 75.10 [Semantic-CD 表I] | 未找到代码 |
| Referring Change Detection（RCDNet、RCDGen） | 2025 | WACV 2026 | 指代变化检测：用类别提示取出该类别的“变化后”区域 | 两期影像 + 类别提示 | 该类别二值掩码 | SECOND、CNAM-CD；跨域测试 WHU-CD、LEVIR-CD | 合并后用 mIoU、SeK、OA、F_scd；BCD 用 IoU | SECOND SeK 24.57（加合成数据 25.25）；在 SECOND 上训练、跨域测试建筑 IoU：WHU 59.05/63.14、LEVIR 53.98/60.21 [RCD 表1] | 代码与权重开源 |
| QSCP | 2026 | arXiv 预印本 | 查询引导的语义变化解析，支持同义词、句子和 from→to 转换查询 | 两期影像 + 查询文本 | 查询掩码 + 两期语义图 | SECOND；WHU-CDC 迁移 | BIoU、语义 mIoU | 转换查询 70.99（RCDNet 48.56）；同义词查询 70.71（RCDNet 53.34）[QSCP 表I] | 代码开源，不含权重 |
| LG-CD | 2025 | arXiv 预印本 | 文本引导的监督二值变化检测（SAM2 + 文本融合注意力） | 两期影像 + 文本提示 | 二值变化掩码 | LEVIR-CD、WHU-CD、SYSU-CD | P、R、IoU、F1、OA | F1：LEVIR 90.35、WHU 91.83、SYSU 80.48 [LG-CD 表II] | 未找到代码 |
| DynamicVL（DVL-Bench、DVLChat） | 2025 | NeurIPS 2025 D&B | 多时相城市理解基准，含指代变化检测 RCD | 5–10 期影像 + 指令 | 答案、描述或掩码 | DVL-Bench（NAIP，42 个美国城市） | 准确率、0–5 分评分、IoU | RCD IoU：ChangeMamba 32.41、DVLChat 29.06、PSALM 26.93、LISA 13.85 [DynamicVL 图10] | 代码与数据开源 |
| LaVIDE | 2024 | IEEE TPAMI 2026 | 用语言编码过时地图，与当前影像比较 | 当前影像 + 过时地图 | 二值变化图 | DynamicEarthNet、HRSCD、BANDON、SECOND | F1、IoU | IoU 相对提升 1.6%–18.4% [LaVIDE 表1] | 代码开源 |
| UniChange | 2025 | CVPR 2026 | MLLM 统一 BCD 与 SCD，文本指令生成掩码 | 两期影像 + 文本指令 | 掩码集合 | WHU-CD、S2Looking、LEVIR-CD+、SECOND（联合训练） | P、R、F1、IoU；mIoU、F_scd、SeK | F1：WHU 94.96、S2Looking 69.32、LEVIR-CD+ 88.19；SECOND IoU 57.62 [UniChange 表1、摘要] | 代码与权重开源（2026-10-20 发布权重） |
| Delta-LLaVA（Decoding the Delta） | 2026 | arXiv 预印本 | MLLM 统一变化分割与变化问答，支持双时相、三时相和点框提示 | 多期影像 + 文本 + 可选点框 | 文本 + 掩码 | Delta-QA（SECOND、Landsat-SCD、WUSU 合成）、DVL-Bench | mIoU、SeK、F_scd；准确率、METEOR、CIDEr | Delta-SECOND mIoU 69.72；DVL-Bench BCA-QA 58.28 [Delta-LLaVA §5.2] | 未公开 |
| ViLaCD-R1 | 2025 | arXiv 预印本 | VLM 经 SFT 与 RL 做块级双时相推理，输出粗掩码，再由掩码引导解码器细化 | 两期影像 | 二值变化图 | LEVIR-CD、LEVIR-CD+、SYSU-CD | P、R、F1、IoU、OA | SYSU F1 83.71、IoU 74.11；LEVIR IoU 84.33 [ViLaCD-R1 表1、表3] | 未找到代码 |
| QUAKE-CD | 2026 | arXiv 预印本 | VLM 用四叉树 token 序列直接生成二值变化掩码，附推理链，RL 训练 | 两期影像 | 推理文本 + 掩码 | QUAKE-CoT（由 SYSU-CD、LEVIR-CD、LEVIR-CD+ 构建） | 累计 F1、逐图平均 F1 | 累计 F1 78.31；ChangeFormer 81.44；LISA-7B 27.14 [QUAKE-CD 表1] | 未找到代码 |
| Change-Agent | 2024 | IEEE TGRS 2024 | 多任务 MCI 模型（掩码 + 描述）+ LLM 智能体 | 两期影像 + 指令 | 背景、道路、建筑掩码 + 描述 + 分析 | LEVIR-MCI | MIoU；BLEU、CIDEr-D | 相对单任务方法 MIoU +0.75、BLEU-4 +1.56 [Change-Agent 表2] | 代码与权重开源 |
| ChangeGPT | 2025 | Automation in Construction 177 (2025) | LLM 智能体调用视觉基础模型工具回答变化问题 | 影像 + 问题 | 文本答案 | 140 道问题（LEVIR-CD 样本） | 工具选择 P、R；Match | GPT-4-turbo 后端 Match 90.71% [ChangeGPT 摘要]；不评像素指标 [ChangeGPT §4.1] | 未找到代码 |
| GaiaAgent | 2026 | ISPRS JPRS 2026 | 视觉语言变化检测模型 + LLM 多智能体，2D 与 3D 变化 | 双时相影像 + DSM + 指令 | 2D 语义变化 + 高度变化 + 分析 | UrbanChange、3DCD、SMARS | F1、mIoU、RMSE、cRMSE | UrbanChange F1 57.17（ChangeMamba 53.27）[GaiaAgent 表4] | 未找到代码 |
| TEOChat | 2024 | ICLR 2025 | 时序遥感视觉语言助手，含变化检测（以框表示）与变化问答 | 影像序列 + 指令 | 文本（含框） | TEOChatlas；零样本 ABCD、CDVQA | 框转掩码后的像素 F1；准确率 | S2Looking 变化检测比专用模型低 31.3 F1 [TEOChat 表1] | 代码与权重开源 |
| DeltaVLM | 2025 | arXiv 预印本 | 多轮指令式变化分析（描述、分类、计数、定位、问答） | 两期影像 + 指令 | 文本 | ChangeChat-105k | BLEU、CIDEr、准确率 | 本次未提取数值 | 代码与权重开源 |
| UniRS | 2024 | arXiv 预印本 | 统一单图、双时相、视频的 VLM，含变化描述 | 影像对 + 指令 | 文本 | LEVIR-CC 等 | CIDEr-D | LEVIR-CC CIDEr-D 139.12 [UniRS 表VI] | 代码开源 |
| TriSV-CD | 2026 | ESWA 2027 | 二值变化检测修正的接受或回滚仲裁，并检验 VLM 判定 | 两期影像 + 基线预测 + 修正提议 | 最终掩码 | SYSU-CD、LEVIR-CD、NJDS | ΔF1、有害编辑率 | VLM 提示变体 V2、V3 一致率 62.78%；VLM 类别门 ΔF1 +0.0047，低于单阈值门 +0.0051 [TriSV-CD 表1、表2] | 代码应请求提供 |
| KnowChange | 2026 | arXiv 预印本 | VLM 指导的变化数据合成 | 变化前影像 + 语义掩码 + 变化类型 | 变化后影像与掩码 | 下游：LEVIR-CD、WHU-CD、DSIFN-CD、SEC-BCD、SECOND、HRSCD | IoU、F1 | 合成到真实迁移平均 IoU +6.64 [KnowChange 表2] | 代码开源 |
| ChangeMamba | 2024 | IEEE TGRS 2024 | 封闭集 BCD、SCD、损毁评估 | 两期影像 | 二值或语义变化图 | SYSU-CD、LEVIR-CD+、WHU-CD、SECOND、xBD | F1、IoU；SeK | MambaBCD-Base F1：SYSU 83.11、WHU 94.19、LEVIR-CD+ 88.39；MambaSCD-Base SeK 22.92 [ChangeMamba 表III–V、表VII] | 代码与权重开源 |

## 1. 相关任务的界定

**结论。** 这些任务的区别在三处：是否接收文本、输出是掩码还是文本、“变化”怎样定义。OVCD 的评测定义是“该像素发生了变化，并且查询类别在任一期出现”，包括新增、消失和同类内变化，不区分方向。指代变化检测（RCD）只取变化后属于查询类别的区域，即只算新增方向。语义变化检测输出 from–to 两期类别。变化描述和变化问答输出文本，变化的定义由标注文本或问答模板决定。“零样本”在不同论文中含义不同，引用时必须写清是哪一种。

| 任务 | 输入 | 输出 | “变化”的定义 | 代表工作 |
|---|---|---|---|---|
| 开放词汇变化检测（OVCD） | 两期影像 + 任意类别文本 | 该类别的二值变化掩码 | 变化像素中，查询类别在 t1 或 t2 出现即为正样本 | DynamicEarth、OmniOVCD、Zero-OVCD、CoRegOVCD |
| 零样本变化检测 | 两期影像（AnyChange 可加点） | 类别无关的二值变化掩码 | 两期语义不同的位置 | AnyChange、SCM |
| 指代变化检测 | 两期影像 + 类别提示（QSCP 扩展到句子和转换） | 该类别二值掩码（QSCP 另输出两期语义图） | RCD：变化后属于查询类别（新增方向）；QSCP：可指定 from→to | RCD（WACV 2026）、QSCP、DVL-Bench 的 RCD |
| 文本引导变化检测 | 两期影像 + 文本提示 | 二值变化掩码 | 沿用训练数据集的定义；文本只调节注意力 | LG-CD、UniChange、LaVIDE（地图文本） |
| 语义变化检测 | 两期影像 | 两期语义图 + 变化掩码 | from–to 类别转换；SECOND 也标注同类内变化 | SECOND、ChangeMamba、Semantic-CD |
| 变化描述 | 两期影像 | 句子 | 标注者描述的变化；含“无变化”负样本 | LEVIR-CC、LEVIR-MCI、UniRS |
| 变化问答 | 两期或多期影像 + 问题 | 答案文本 | 由问答模板和语义掩码导出 | TEOChat、Delta-QA、DVL-Bench、ChangeChat-105k |

证据：

- OVCD 的任务定义：输入两期影像和任意文本或语义标签，定位并识别该类别的变化，类别不预先固定 [DynamicEarth §3]。作者把多类语义变化检测简化成逐类的单类变化检测 [DynamicEarth §2]。
- OVCD 在 SECOND 上的真值构造：DynamicEarth 评测代码 `eval/evaluate_second.py` 写作 `change_label = (label1 == class_id) | (label2 == class_id)` [DynamicEarth 代码]。SECOND 的两期语义图只在变化区域标注类别，未变化像素被置空 [SECOND §IV]。两者合起来，正样本就是“变化且 t1 或 t2 属于该类”。CoRegOVCD 把这一点明确写成 OR 规则：“a pixel is positive for class c if either timestamp belongs to c” [CoRegOVCD 表1、表2 表题]。
- OVCD 方法的输出也按“任一期”取并集：OmniOVCD 的最终变化掩码是 t1、t2 两期变化实例的并集 [OmniOVCD 式6]。
- SECOND 明确保留同类内变化：作者用“土地覆盖图对 + 未变化掩码”表示变化类型，因此能区分同一类别内的变化像素和未变化像素 [SECOND §IV]。按 OR 规则，建筑拆除重建这类 building→building 的变化在 OVCD 评测中也算正样本（推断，由上面两条证据组合得出）。
- AnyChange 把两期语义类别不同的位置定义为变化，输出类别无关掩码，只回答“哪里变了” [AnyChange §3.1；DynamicEarth §1]。
- 指代变化检测：RCD 用自然语言提示指定变化类别 [RCD 摘要]；实验中“只关注变化后的图，即增长型变化” [RCD §4.4]。QSCP 也指出已有的类别提示 RCD 用查询类别指定变化的“目的地”，只返回二值掩码 [QSCP 摘要]。DVL-Bench 的 RCD 含 1,391 条指代分割指令，变化类别改编自 SECOND，共 20 种变化事件 [DynamicVL §3]。
- 文本引导变化检测：LG-CD 用文本提示让网络关注目标变化区域，仍在每个数据集上监督训练 [LG-CD 摘要、表II]。LaVIDE 的输入是过时地图和当前影像，输出二值变化图 [LaVIDE §III-A]。UniChange 输入双时相影像和文本指令，输出遵循指令的掩码集合 [UniChange §3.1]。
- 语义变化检测：SECOND 六类加未变化共 30 种常见变化类型 [SECOND §IV]，指标为 mIoU 和 SeK [SECOND §V]。
- 变化描述：LEVIR-CC 共 10,077 对，每对 5 句描述 [UniRS §III-C]；LEVIR-MCI 每对另加一张掩码 [Change-Agent §3]。
- 变化问答：Delta-QA 以时相语义分割掩码为真值，经 GPT-4o 生成识别、计数统计、趋势、空间分析四类问答 [Delta-LLaVA §3.1]；DeltaVLM 的 ChangeChat-105k 含描述、分类、计数、定位、开放问答、多轮对话六种交互 [DeltaVLM 摘要]；TEOChat 做零样本 CDVQA [TEOChat 表3]。
- “零样本”的三种用法：(a) 不做任何变化检测训练，如 AnyChange、DynamicEarth [AnyChange §1；DynamicEarth §4]；(b) 不用目标域像素标注，但在目标域影像上训练，如 Zero-OVCD 第二阶段沿用原训练、验证划分并以伪标签训练 [Zero-OVCD §III-B]；(c) 在其他变化数据上训练类别无关模块，再在新数据上测试，如 Seg2Change 的 CA-CDD [Seg2Change 表17]、RCD 的跨域实验 [RCD §4.6]。

## 2. 基准与评测协议

**结论。** 建筑类基准是 LEVIR-CD、WHU-CD、S2Looking、BANDON、DSIFN，查询文本固定为 building，真值直接用数据集的二值掩码。多类基准几乎都用 SECOND，按 OR 规则逐类构造单类真值，再报告六类宏平均。指标是变化类的 IoU^c 和 F1^c，加 Precision、Recall。没有任何一篇 OVCD 论文划分“见过”和“没见过”的类别。各论文的测试划分和复现数字不一致，跨论文直接比较数字有风险。

证据：

- 数据集：DynamicEarth 用 LEVIR-CD（测试 128 对 1024²）、WHU-CD（744 对 256²）、S2Looking（1,000 对）、BANDON（只用 207 对 in-domain）、SECOND [DynamicEarth §5.1]。后续工作加入 DSIFN [AdaptOVCD 表3；CoRegOVCD 表1；MemOVCD 表1]、Hi-UCD mini [OpenDPR 表3]、CLCD 和 SC-SCD [Seg2Change 表1、表2]、xBD [CogVis 表1]。
- 类别查询：建筑数据集只用 building 作前景提示，用 background 作背景提示 [MemOVCD 实验设置]。SECOND 按 OR 规则逐类构造（见第 1 节），Zero-OVCD 称之为 one-vs-rest，并说明沿用 DynamicEarth [Zero-OVCD §IV-A]。
- 提示词：VLM 对文本敏感，简单“类别名 + background”只对 building 这类常见类别有效，土地覆盖类别要加同义词，并把其他类别放进背景提示 [DynamicEarth §5.3]。之后各论文都公开了逐数据集提示表 [AdaptOVCD 表2；ReA-OVCD 表I；Seg2Change 表6；CogVis 表S1、表S2]，提示不统一。
- 指标：变化类 Precision、Recall、F1、IoU，TP、FP、FN 按像素统计 [Zero-OVCD 式23]；SECOND 报告六类平均 [Zero-OVCD 表II；CoRegOVCD 表2]。Seg2Change 另报 Kappa [Seg2Change 表1]。RCD 把逐类二值图合并后用语义变化指标 mIoU、SeK、F_scd [RCD §4.3–4.4]，与 OVCD 的逐类指标不可比。
- 统计方式会改变结论：在同一测试集上，ChangeCLIP 的累计 F1 是 81.30，逐图平均 F1 只有 51.04 [QUAKE-CD 表1]。
- SECOND 划分不一致：DynamicEarth 用 593 对 [DynamicEarth §5.1]，其评测脚本读取 `data/SECOND2/val` [DynamicEarth 代码]；Zero-OVCD 用 2,079/1,990/593 划分 [Zero-OVCD §IV-A]；CoRegOVCD 用 SECOND 验证划分 [CoRegOVCD §4.1]；OmniOVCD、AdaptOVCD、ReA-OVCD、Seg2Change 用 1,694 对测试集 [OmniOVCD §4.2；AdaptOVCD 与 ReA-OVCD 的数据集一节；Seg2Change 表17]。
- WHU-CD 划分不一致：744 对 256² [DynamicEarth §5.1；Zero-OVCD §IV-A]，660 对 512² [Seg2Change 表17]，256² 切块按 8:1:1 [OmniOVCD §4.2]，1024² 切块按 8:1:1 [UniChange §4.2]，本仓库为 690 对 512²（`README.md`）。
- 复现数字差异：DynamicEarth 的 APE-DINO 在 LEVIR-CD 上 F1 原文为 69.7 [DynamicEarth 表1]，Zero-OVCD 复现 69.71 [Zero-OVCD 表I]，AdaptOVCD 复现 65.78 [AdaptOVCD 表3]，ReA-OVCD 复现 61.70 [ReA-OVCD 表II]。同一配置的差异最多 8 个百分点。
- 见过与没见过的类别：
  - 免训练方法不在任何类别上训练，全部查询都相当于“没见过”。
  - Seg2Change 的训练集 CA-CDD 包含 SECOND 训练集 2,968 对 [Seg2Change 表17]，因此它在 SECOND 上的变化头属于同数据集结果。
  - CogVis 去掉 CA-CDD 训练的适配器后，SECOND mIoU 从 32.48 降到 31.42；再去掉记忆模块降到 23.39 [CogVis 表3]。
  - RCD 的跨域实验只测 building，这个类别在 SECOND 训练中出现过 [RCD §4.6]；作者试过完全零样本的未见类别，结果“不稳定”，因此不下结论 [RCD §4.7]。
  - QSCP 评测同义词、句子和转换查询，并测试对未知类别的拒绝 [QSCP 表I、表III]。
  - DynamicEarth 指出 Semantic-CD 只在同一数据集上训练和评测，不足以支持真正的开放词汇能力 [DynamicEarth §4]。

## 3. 方法分类

**结论。** 免训练组合方法目前分成三条路线：先掩码后比较再识别（M-C-I）、先识别再比较（I-M-C）、像素级语义差异加可靠性筛选。2026 年的方法普遍换用 SAM 3，数字明显提高。需要训练的开放词汇方法有两种做法：训练类别无关的变化模块，让类别交给开放词汇分割模型；或者用伪标签在目标域训练。基于 MLLM 的方法有三类：直接输出掩码、调用工具的智能体、做验证或仲裁。直接输出掩码的 MLLM 仍落后于专用检测器；调用工具的智能体都不评像素掩码质量。

### 3.1 免训练组合方法

| 路线 | 方法 | 组成 | 证据 |
|---|---|---|---|
| M-C-I | DynamicEarth M-C-I | SAM/SAM2 自动掩码 → DINO/DINOv2 余弦变化分数 → SegEarth-OV 识别 | [DynamicEarth §4.1] |
| M-C-I | AnyChange（+ 点查询） | SAM 掩码 + SAM 隐空间双向匹配 | [AnyChange §3.3–3.4] |
| M-C-I | OpenDPR | SAM + DINOv2 提议，扩散原型在视觉空间检索识别 | [OpenDPR 摘要] |
| M-C-I | AdaptOVCD | SAM-HQ + DINOv3 + DGTRS-CLIP，加辐射校正、自适应阈值、置信度过滤 | [AdaptOVCD 摘要] |
| I-M-C | DynamicEarth I-M-C | Grounding DINO / APE / Molmo 识别 → SAM 细化 → IoU 几何比较 + 特征双重确认 | [DynamicEarth §4.2] |
| I-M-C | OmniOVCD | SAM 3 语义、实例、存在三个头融合后拆成实例，跨期比较 | [OmniOVCD 摘要、式6] |
| I-M-C | MemOVCD | 两帧跟踪式双向记忆传播，直方图过渡帧 | [MemOVCD 摘要] |
| 像素级 | CoRegOVCD | 竞争性后验校准 + 语义后验差 + 几何门控 | [CoRegOVCD 摘要] |
| 像素级 | ReA-OVCD | 像素语义差异候选 → 语义可靠性 → 空间可靠性 | [ReA-OVCD 摘要] |
| 组合 + 补漏 | Zero-OVCD 第一阶段 | SAM3 自动与文本掩码互补 → 多尺度相似度 + 前背景间隔过滤 → 两期类别响应差找回漏检 | [Zero-OVCD §III-A] |

- 换用更强的基础模型收益最大：Zero-OVCD 比较四种基础模型组合，SAM3–DINOv3–SOV3 在三个数据集上都最好 [Zero-OVCD 表VIII]。DynamicEarth 的 I-M-C 高度依赖识别器，APE 远好于 MM-Grounding-DINO [DynamicEarth §5.3]。
- 简单数据集上 I-M-C 好，复杂数据集上 M-C-I 好 [DynamicEarth §5.3]。

### 3.2 需要训练的开放词汇方法

| 方法 | 训练什么 | 用什么数据 | 证据 |
|---|---|---|---|
| UniVCD | SAM2 与 CLIP 之间的轻量对应模块 | 无标注，不需要变化影像对 | [UniVCD 摘要] |
| Seg2Change | 类别无关变化头 CACH | CA-CDD（SECOND 训练集、JL1-CD、CNAM-CD） | [Seg2Change 摘要、表17] |
| CogVis | 三个轻量适配器 + 记忆库 | CA-CDD | [CogVis 摘要、表3] |
| OpenDPR-W | 弱监督定位模块 S2C | 弱标注 | [OpenDPR 摘要、表2] |
| Zero-OVCD 第二阶段 | ChangerEx，每个查询类别单独训练 | 目标域影像 + 第一阶段伪标签 | [Zero-OVCD §III-B、§V] |
| RCDNet | 文本条件的变化网络 | SECOND、CNAM-CD，加 RCDGen 合成数据 | [RCD §3、§4] |
| QSCP | 查询条件解码器 | SECOND | [QSCP 摘要] |
| Semantic-CD | CLIP 编码器 + 双解码器 | SECOND | [Semantic-CD 摘要] |
| LG-CD | SAM2 适配器 + 文本融合 | 各数据集监督 | [LG-CD 摘要] |

### 3.3 基于 MLLM 的方法

**直接输出掩码。**

- UniChange 在词表中加入 [T1]、[T2]、[CHANGE] 三个 token，经 SAM 式解码器得到掩码 [UniChange §3.2]。
- Delta-LLaVA 用 [SEG] token 加 Change-SEG 解码器 [Delta-LLaVA §4]。
- DVLChat 采用 LISA 式 `<SEG>` token 加 SAM 解码器 [DynamicVL §5]。
- QUAKE-CD 把掩码写成四叉树 token 序列，由 VLM 自回归生成 [QUAKE-CD 摘要]。
- ViLaCD-R1 由 VLM 输出粗掩码，再交给解码器细化 [ViLaCD-R1 摘要]。

**调用工具的智能体。**

- Change-Agent 由 LLM 调度 MCI 模型和后处理 [Change-Agent §4]。
- ChangeGPT 分层调用视觉基础模型工具，但明确说明框架不优化像素级预测，因此不用 IoU、F1 评测 [ChangeGPT §4.1]。
- GaiaAgent 以多智能体完成指令解读、任务分解、检测、定量分析和人工修订 [GaiaAgent §4.2]。
- 知识库中的 RemoteAgent 把分割、检测、变化检测这类稠密预测一律交给专家工具，MLLM 只理解意图、选择工具 [wiki/agentic-segmentation]。

**验证与仲裁。** TriSV-CD 让 VLM 判定修正是否可接受。这条路线出现了负面结果，见第 6 节。

**直接输出掩码的 MLLM 落后于专用检测器。**

- DVL-Bench 指代变化检测上，ChangeMamba IoU 32.41，DVLChat 29.06 [DynamicVL 图10]。
- QUAKE-CoT 上，ChangeFormer 累计 F1 81.44，QUAKE-CD 78.31，LISA-7B 27.14 [QUAKE-CD 表1]。
- 推理分割基线即使经过监督微调，在 Delta-SECOND 上 F_scd 仍接近零 [Delta-LLaVA §5.2.1]。

## 4. 具体数字

**结论。** 免训练 OVCD 在 LEVIR-CD 和 WHU-CD 上的 F1^c 已达 84–90，与封闭集全监督（约 91–94）只差几个百分点。在侧视角的 S2Looking 上最好的免训练结果是 F1 50.48，全监督是 66.3，差距仍大。SECOND 六类平均 F1^c，免训练方法最好约 47–48，Zero-OVCD 第二阶段为 50.92；查询类别之间差别很大。SYSU-CD 没有类别标签，检索范围内没有任何 OVCD 方法在 SYSU-CD 上报告结果；SYSU-CD 上只有监督方法（含文本引导和 VLM 方法）的数字。

### 4.1 建筑类数据集，F1^c / IoU^c（%）

“划分”一列只在与 DynamicEarth 不同时注明。带 * 的是他人复现的数字。

| 方法 | 类型 | LEVIR-CD | WHU-CD | S2Looking | 其他 | 出处 |
|---|---|---|---|---|---|---|
| AnyChange-H | 免训练、类别无关 | 23.0 / – | – | 6.4 / – | – | [DynamicEarth 表1] |
| AnyChange* | 同上 | 24.88 / 14.21 | 19.20 / 10.62 | 6.83 / 3.54 | – | [Zero-OVCD 表I] |
| SCM | 免训练 | 31.7 / 18.8 | 31.3 / 18.6 | – | – | [DynamicEarth 表1] |
| DynamicEarth SAM-DINOv2-SOV（M-C-I） | 免训练 | 53.6 / 36.6 | 57.7 / 40.6 | 38.5 / 23.9 | BANDON 30.2 / 17.6 | [DynamicEarth 表1] |
| DynamicEarth APE-DINO（I-M-C） | 免训练 | 69.7 / 53.5 | 72.5 / 56.8 | 18.4 / 10.1 | BANDON 14.5 / 7.8 | [DynamicEarth 表1] |
| DynamicEarth APE-DINOv2（I-M-C） | 免训练 | 66.7 / 50.0 | 75.8 / 61.1 | 10.1 / 5.3 | BANDON 21.1 / 11.8 | [DynamicEarth 表1] |
| OmniOVCD | 免训练 | 80.4 / 67.2 | 79.9 / 66.5（256² 切块 8:1:1） | 39.4 / 24.5 | – | [OmniOVCD 表1] |
| AdaptOVCD | 免训练 | 68.00 / 51.52 | 76.53 / 61.99 | – | DSIFN 59.47 / 42.32 | [AdaptOVCD 表3] |
| OpenDPR | 免训练 | 61.9 / 44.8 | 70.4 / 54.3 | – | – | [OpenDPR 表1] |
| CoRegOVCD | 免训练 | 83.01 / 70.95 | 82.14 / 69.70（WHU-CD-256） | – | DSIFN 64.45 / 47.55 | [CoRegOVCD 表1] |
| MemOVCD | 免训练 | 84.1 / 72.5 | – | 41.3 / 26.0 | DSIFN 54.9 / 37.8；BANDON 37.7 / 23.2 | [MemOVCD 表1] |
| ReA-OVCD | 免训练 | 84.52 / 73.19 | 90.31 / 82.33 | – | DSIFN 67.79 / 51.27 | [ReA-OVCD 表II] |
| Zero-OVCD 第一阶段 | 免训练 | 86.25 / 75.82 | 85.82 / 75.17 | 50.48 / 33.76 | – | [Zero-OVCD 表I] |
| Zero-OVCD 第二阶段 | 目标域无标注影像 + 伪标签训练 | 88.65 / 79.61 | 88.85 / 79.93 | 57.96 / 40.81 | – | [Zero-OVCD 表I] |
| OpenDPR-W | 弱监督 | 65.0 / 48.1 | 81.2 / 68.3 | – | – | [OpenDPR 表2] |
| UniVCD（orig., SAM3 postproc） | 无标注训练 | 72.3 / 56.7 | 79.5 / 66.0 | – | – | [UniVCD 表I] |
| Seg2Change | 训练类别无关变化头 | 78.72 / 64.91（512² 切块） | 86.18 / 75.72（660 对 512²） | – | DSIFN 58.56 / 41.40；CLCD 47.89 / 31.48 | [Seg2Change 表1] |
| CogVis | 训练适配器 | – / 72.55 | – / 75.86 | – | DSIFN – / 48.50 | [CogVis 表1] |
| RCDNet（在 SECOND 上训练，跨域） | 指代、监督 | – / 53.98 | – / 59.05 | – | – | [RCD 表1] |
| RCDNet + 合成数据（跨域） | 指代、监督 | – / 60.21 | – / 63.14 | – | – | [RCD 表1] |

### 4.2 SECOND 逐类，F1^c / IoU^c（%）

| 方法 | 划分 | 建筑 | 树 | 水 | 低矮植被 | 非植被地表 | 操场 | 平均 | 出处 |
|---|---|---|---|---|---|---|---|---|---|
| DynamicEarth SAM-DINOv2-SOV | 593 | 55.2 / 38.1 | 33.8 / 20.3 | 25.1 / 14.3 | 38.9 / 24.1 | 41.6 / 26.2 | 33.3 / 20.0 | 38.0 / 23.8 | [DynamicEarth 表2；平均取自 OpenDPR 表4] |
| DynamicEarth APE-DINOv2 | 593 | 43.9 / 28.1 | 24.8 / 14.1 | 21.7 / 12.2 | 2.7 / 1.4 | ≈0 | 27.6 / 16.0 | – | [DynamicEarth 表2] |
| OmniOVCD | 1694 | 62.3 / 45.2 | 28.6 / 16.7 | 35.0 / 21.2 | 39.3 / 24.5 | 43.4 / 27.7 | 42.4 / 27.0 | 41.8 / 27.1 | [OmniOVCD 表2] |
| AdaptOVCD | 1694 | 63.81 / 46.85 | 22.49 / 12.67 | 37.84 / 23.34 | 36.27 / 22.15 | 50.74 / 33.99 | 45.30 / 29.28 | 42.74 / 28.05 | [AdaptOVCD 表4；平均值取自 CoRegOVCD 表2] |
| OpenDPR（逐类推理） | 本次未查到 | 60.4 / 43.2 | 33.0 / 19.7 | 21.4 / 12.0 | 38.6 / 23.9 | 47.5 / 31.2 | 42.0 / 26.5 | 40.5 / 26.1 | [OpenDPR 表4] |
| CoRegOVCD | 验证划分 | 65.69 / 48.91 | 34.61 / 20.93 | 46.01 / 29.88 | 43.40 / 27.71 | 48.79 / 32.27 | 46.52 / 30.31 | 47.50 / 31.67 | [CoRegOVCD 表2] |
| ReA-OVCD | 1694 | 74.31 / 59.12 | 32.48 / 19.40 | 41.39 / 26.09 | 43.69 / 27.95 | 51.63 / 34.80 | 39.67 / 24.74 | 47.19 / 32.02 | [ReA-OVCD 表III] |
| Zero-OVCD 第一阶段 | 593 | 69.53 / 53.30 | 35.98 / 21.94 | 40.75 / 25.59 | 48.17 / 31.73 | 53.27 / 36.30 | 39.78 / 24.83 | 47.91 / 32.28 | [Zero-OVCD 表II] |
| Zero-OVCD 第二阶段 | 593 | 72.11 / 56.39 | 42.51 / 26.99 | 46.20 / 30.04 | 50.27 / 33.58 | 55.68 / 38.58 | 38.72 / 24.01 | 50.92 / 34.93 | [Zero-OVCD 表II] |
| UniVCD（SAM3 postproc） | 本次未查到 | 60.4 / 43.2 | 31.9 / 18.9 | 15.2 / 8.2 | 39.8 / 24.9 | 43.7 / 28.0 | ≈0 | – | [UniVCD 表II] |
| Seg2Change | 1694 | 76.36 / 61.76 | 27.73 / 16.10 | 37.23 / 22.88 | 31.49 / 18.69 | 54.29 / 37.26 | 30.23 / 17.81 | 42.89 / 29.08 | [逐类取自 CogVis 表S5；平均取自 Seg2Change 表2] |
| CogVis | 本次未查到 | 77.35 / 63.07 | 39.43 / 24.55 | 45.99 / 29.86 | 47.20 / 30.89 | 56.90 / 39.76 | 12.61 / 6.73 | 46.58 / 32.48 | [CogVis 表S5] |

### 4.3 SYSU-CD

SYSU-CD 是类别无关的变化数据集 [ChangeMamba 表I]，无法按类别构造查询。检索范围内的 OVCD 论文都没有在 SYSU-CD 上报告结果。这个数据集上能找到的数字都来自监督方法，F1 / IoU（%）：

| 方法 | 类型 | F1 / IoU | 出处 |
|---|---|---|---|
| MambaBCD-Base | 封闭集监督 | 83.11 / 71.10 | [ChangeMamba 表III] |
| LG-CD | 文本引导、监督 | 80.48 / 70.59 | [LG-CD 表II] |
| ViLaCD-R1 | VLM + 解码器、监督 | 83.71 / 74.11 | [ViLaCD-R1 表3] |
| ChangeCLIP | 封闭集监督 | 83.32 / 71.41 | [ViLaCD-R1 表3] |
| SChanger-small（本仓库复现） | 封闭集监督 | F1 84.58 | `results/env.md` |

### 4.4 封闭集全监督参照，F1 / IoU（%）

| 数据集 | 方法 | F1 / IoU | 出处 |
|---|---|---|---|
| LEVIR-CD | Changer（同数据集训练） | 91.8 / 84.9 | [DynamicEarth 表3] |
| LEVIR-CD | AdaptOVCD 表中的全监督上界 | 91.20 / 83.80 | [AdaptOVCD 表3] |
| LEVIR-CD | SChanger-base（本仓库复现） | 92.88 / – | `results/env.md` |
| WHU-CD | Changer | 93.5 / 87.9 | [DynamicEarth 表3] |
| WHU-CD | MambaBCD-Base | 94.19 / 89.02 | [ChangeMamba 表V] |
| WHU-CD | UniChange（四个数据集联合训练的 MLLM） | 94.96 / 90.41 | [UniChange 表1] |
| S2Looking | Changer | 66.3 / 49.6 | [DynamicEarth 表3] |
| S2Looking | UniChange | 69.32 / 53.04 | [UniChange 表1] |
| BANDON | Changer | 68.5 / 52.1 | [DynamicEarth 表3] |
| DSIFN | AdaptOVCD 表中的全监督上界 | 69.26 / 53.80 | [AdaptOVCD 表3] |
| SECOND 建筑（OR 规则） | AdaptOVCD 表中的全监督上界 | 72.40 / 56.70 | [AdaptOVCD 表3] |
| SECOND（SCD 口径） | MambaSCD-Base | SeK 22.92，mIoU 73.68 | [ChangeMamba 表VII] |
| SECOND（二值变化） | MambaSCD-Base（本仓库复现） | F1 74.17 | `results/env.md` |

监督模型跨数据集使用时下降严重：Changer 在 LEVIR-CD 上训练后，在 S2Looking 上 F1 只有 2.9，在 BANDON 上只有 5.8；DynamicEarth 的最好 OVCD 结果在四个数据集上都高于最好的跨数据集监督结果 [DynamicEarth 表3]。检索范围内没有找到 SECOND 其余五类按 OR 规则评测的全监督参照。

## 5. 重点核实：DynamicEarth

**结论。** 这篇论文存在，标题为 *DynamicEarth: How Far are We from Open-Vocabulary Change Detection?*，作者 Kaiyu Li、Xiangyong Cao、Yupeng Deng、Chao Pang、Zepeng Xin、Deyu Meng、Zhi Wang（西安交通大学、中国科学院、武汉大学）。arXiv:2501.12931 于 2025-01-22 提交；正式发表于 AAAI 2026，为 Oral，DOI 10.1609/aaai.v40i8.37554（知识库笔记于 2026-09-07 依据 GitHub README 和 AAAI proceedings 页核验）。它是第一篇提出 OVCD 任务的论文，提出两个免训练框架，并发布了代码库 DynamicEarth。

- **任务定义。** 对同一地点两期影像，按任意文本或语义标签指定的类别定位并识别变化；类别不预先固定 [DynamicEarth §3]。作者认为完整的 OVCD 框架需要三个组件：识别器、比较器、实例级掩码提议 [DynamicEarth §1]。
- **M-C-I 框架。** SAM/SAM2 自动模式生成两期全部候选掩码，合并后用 NMS 去重；比较器用 DINO/DINOv2 掩码区域平均特征的负余弦相似度作为变化分数，超过阈值 β 判为变化；识别器用 SegEarth-OV 的整图 CLIP 特征做掩码平均池化，再与类别文本比较 [DynamicEarth §4.1、式1]。
- **I-M-C 框架。** 借鉴分类后比较，用 Grounding DINO、APE 或 Molmo 按文本识别目标，再用 SAM 转成细掩码；比较器先用同类掩码的 IoU 判断，再叠加 M-C-I 的特征匹配，两种方法都确认才保留 [DynamicEarth §4.2]。
- **数据集。** LEVIR-CD、WHU-CD、S2Looking、BANDON（in-domain 207 对）、SECOND（六类） [DynamicEarth §5.1]。
- **评测与设置。** 逐类 IoU^c、F1^c [DynamicEarth §5.2]；SECOND 真值按 OR 规则构造 [DynamicEarth 代码]。单张 4090 可运行；阈值 β 随方法和类别调到最优 [DynamicEarth §5.2]。
- **主要结果。**
  - 建筑数据集上，I-M-C（APE-DINO）在 LEVIR-CD 上 F1^c 69.7，I-M-C（APE-DINOv2）在 WHU-CD 上 75.8；M-C-I（SAM-DINOv2-SegEarth-OV）在 S2Looking、BANDON 上分别为 38.5、30.2 [DynamicEarth 表1]。
  - SECOND 六类上 M-C-I 整体领先，建筑 F1^c 55.2 最高；I-M-C 在低矮植被和非植被地表上接近 0 [DynamicEarth 表2]。
  - 相对最好的跨数据集监督结果（Changer），F1^c 提升 7.0–22.8 [DynamicEarth 表3]。
- **作者承认的局限。**
  - 免训练方法仍不及纯监督方法 [DynamicEarth §6]。
  - 提示词敏感 [DynamicEarth §5.3]。
  - 多类推理需逐类进行 [DynamicEarth §5.3]。
  - M-C-I 对紧凑目标无法实例化；I-M-C 召回受识别器限制；两者都检测不出目标内部的部件级变化 [DynamicEarth 图3、§5.3]。
- **版本说明。** 知识库中的原文是 arXiv v1。本节数字取自 v1，AAAI 正式版的数字本次没有逐项核对。

## 6. 空白与机会

**结论。**

1. **伪变化。** 现有方法处理光照、季节和配准错位的手段（实例级比较、辐射校正、直方图过渡帧、边界可靠性筛选）单项收益都小。空间错位造成的边界响应是像素级方法的主要误差来源。“同一地物换色”和“同类内变化”在评测定义上互相冲突：SECOND 的 OR 规则把同类内变化算作正样本，基于语义差异的方法在结构上检测不到这类变化。
2. **漏检。** I-M-C 路线在识别阶段丢掉的目标后面无法找回，这是整个目标没检出的主要来源。“背景型”类别（树、低矮植被、非植被地表、操场）漏检严重。检索范围内只有 Zero-OVCD 专门设计了补漏模块。没有论文报告目标级的整目标漏检率。
3. **标注口径。** 各论文的类别边界、变化方向、测试划分、提示词和复现数字都不一致。OVCD（OR 规则）与 RCD（新增方向）的数字不可比。
4. **MLLM 迭代优化变化掩码。** 在知识库和 arXiv 检索范围内没有找到。最接近的工作是一次性输出掩码的 MLLM 和单图多轮分割智能体；VLM 用于变化验证时出现了提示不稳定的负面结果。

**伪变化的证据。**

- 季节、光照、视角：DynamicEarth 和 AnyChange 认为实例级比较优于像素级比较，可以缓解这类伪变化 [DynamicEarth §1、§4]。AnyChange 对两期分别做随机颜色抖动后，mask AR 只变化 −1.9 到 +0.1 [AnyChange 表3]。
- 辐射校正收益小：AdaptOVCD 单独加入辐射校正模块 ARA，F1^C 在 LEVIR-CD 上从 65.97 到 66.29，在 WHU-CD 上从 71.36 到 72.13 [AdaptOVCD 表5]。
- 过渡帧收益小：MemOVCD 用直方图过渡帧平滑突变外观，过渡帧数从 0 增至 3 时 LEVIR-CD IoU^c 从 71.05 到 72.54 [MemOVCD 表4]。
- 配准错位：
  - ReA-OVCD 以两期语义标签的异或（XOR）为基线。加入区域级边界可靠性筛选后，F1^C 在 LEVIR-CD 上从 69.52 到 82.90，在 WHU-CD 上从 65.10 到 90.30 [ReA-OVCD 表VII]。作者把这类候选称为“只由边界引起的响应” [ReA-OVCD 摘要]。
  - S2Looking 多为侧视、角度不同的影像 [SAM-CD §IV-A]，所有免训练方法在上面的分数都最低（表 4.1）。
  - BANDON 为离轴影像，屋顶相对地图有明显偏移 [LaVIDE §IV-B]。
  - SECOND-CC 明确写到 SECOND 自带空间错位，但没有给出像素误差统计（知识库输出 `output/codex/SECOND配准与残余错位文献调研.md` §1）。
- 同一地物换色与同类内变化：
  - SECOND 允许同类内变化 [SECOND §IV]，OR 规则把它算作正样本 [DynamicEarth 代码]。
  - ReA-OVCD 的基线是语义标签 XOR [ReA-OVCD 表VII]；Zero-OVCD 对自动来源候选要求两期校准类别不同才保留 [Zero-OVCD §III-A2、式11]；CoRegOVCD 以查询类别后验之差作为变化证据 [CoRegOVCD 摘要]。这三类判据在建筑重建（building→building）上给不出变化信号，而 DINO 特征余弦比较会把屋顶换色也判为变化（推断）。
  - LEVIR-CD 等数据集对屋顶翻新是否算变化的细则，知识库中未找到（`output/codex/变化候选验证中的标签噪声与评估问题-知识库检索.md` §7.2）。
- VLM 判断伪变化不稳定：
  - 在 SYSU-CD 争议样本上，决策型提示 V1 抑制约 50% 的编辑，V2 改写措辞后只保留约 37%，V3 几乎全部返回“不确定”。V2、V3 在 532 个相同图块上一致率 62.78%，V2、V3、V4 在 182 个共同坐标上一致率为 0% [TriSV-CD §3.2、表1]。
  - 按 VLM 类别设置的阈值门不优于单阈值门；随机打乱类别标签 1,000 次，92% 的打乱版本达到或超过原门 [TriSV-CD §3.3、表2]。

**漏检的证据。**

- 整目标漏检：I-M-C 精度高，但部分目标在识别阶段就被丢弃，召回低；M-C-I 对一些紧凑目标无法实例化 [DynamicEarth 图3、§5.3]。SECOND 上 I-M-C 的低矮植被、非植被地表 F1^c 约为 0，原因是识别器难以识别在自然图像中常被当作背景的类别 [DynamicEarth 表2、§5.3]。
- 背景型类别：树的 F1^c 普遍在 22–43 [表 4.2]。操场在 UniVCD 上约为 0 [UniVCD 表II]，在 CogVis 上为 12.61 [CogVis 表S5]。
- 小目标：AnyChange 在原始分辨率下漏掉极小变化，把区域放大两倍后只能检出一部分 [AnyChange §F]。Zero-OVCD 指出对比方法常漏掉小的变化建筑 [Zero-OVCD 图7、§IV-D]。
- 补漏设计：Zero-OVCD 的 MCCM 用两期类别响应差找回前面阶段漏掉的变化区域，丢弃小于 500 像素的补充连通域。加入 MCCM 后三个建筑数据集平均 F1 +8.29、IoU +11.07 [Zero-OVCD §III-A3、表III]。这一增益包含过滤与补漏两部分，没有单独消融 [Zero-OVCD 表III]。
- 目标级评测缺失：检索范围内的 OVCD 论文只报告像素级指标。本仓库实验 1 已经定义整目标漏检（IoU < 0.1）和无信号漏检（`README.md` 设置要点）。

**标注口径的证据。**

- 同一地物在不同数据集中正负相反：一个数据集的变化正样本（如建筑）在另一个数据集中被标为负背景，这种冲突使传统模型无法联合训练 [UniChange §1、图1]。
- 类别边界不同：一个 SCD 数据集把“不透水面”和“裸地”分开，SECOND 合并为“非植被地表” [RCD §1]。SECOND 中非植被地表占全部变化面积 43%，操场只占 0.38% [RCD §1]。
- 变化方向不同：LEVIR-CD 同时标注建筑增长和减少；S2Looking 分别标注新建和拆除（`output/codex/变化候选验证中的标签噪声与评估问题-知识库检索.md` §7.2）；RCD 只评测新增方向 [RCD §4.4]；OVCD 用 OR 规则（第 1 节）。
- 划分、提示、复现不一致：见第 2 节。
- 数据集混合训练：RCD 用文本提示混合 SECOND 与 CNAM-CD 训练，与分开训练相比 SECOND SeK 24.48 对 24.57 [RCD 表2]。UniChange 的联合训练消融中，加入的数据集越多，四个数据集的结果越好 [UniChange 表6]。

**MLLM 迭代优化变化掩码。** 检索覆盖知识库全部 82 篇论文，以及 arXiv 上的“open-vocabulary change detection”“referring change detection”“change detection + MLLM + mask”“change detection + agent + tool”“change detection + VLM + refine + iterative”等检索式（2026-10-28），没有找到用 MLLM 多轮迭代修改变化掩码的工作。最接近的工作有四类：

- 一次性输出掩码的 MLLM：UniChange、Delta-LLaVA、DVLChat、QUAKE-CD；ViLaCD-R1 是“VLM 出粗掩码、解码器细化”的单次流程 [ViLaCD-R1 摘要]。
- 迭代修正但不用 MLLM：Zero-OVCD 用多阶段规则修正伪标签 [Zero-OVCD §III-A]，并在训练时用 checkpoint 投票更新伪标签 [Zero-OVCD §III-B]。
- 单图多轮分割智能体（自然图像或医学图像）：SegAgent、SAM-Veteran、MedSAM-Agent 以“观察当前掩码 → 点击或框 → 再观察”的方式多轮修改掩码。它们的共同发现是：停止时机需要专门训练，只奖励最终结果会产生冗余动作，首步定位是主要瓶颈 [wiki/agentic-segmentation]。
- 变化图的接受或回滚：TriSV-CD 只对一次修正做二选一，VLM 不参与在线决策 [TriSV-CD §4.1–4.3]。

## 7. 代码与权重

**结论。** 适合直接作为 baseline 的有 DynamicEarth、ReA-OVCD、OmniOVCD、AdaptOVCD、OpenDPR（免训练，只需基础模型官方权重），以及 Seg2Change（提供训练好的 CACH 权重）。Zero-OVCD、CoRegOVCD、MemOVCD、UniVCD 的数字较高，但第一阶段或全部代码没有公开，只能引用论文数字。RCDNet、UniChange、DeltaVLM、TEOChat、Change-Agent 提供权重，但都依赖与评测数据集相同或相近的监督训练。

以下为 GitHub 于 2026-10-28 的检查结果：

| 方法 | 仓库 | 代码 | 权重 | 作为 baseline 的说明 |
|---|---|---|---|---|
| DynamicEarth | likyoo/DynamicEarth | 完整，按数据集和类别给出评测脚本 | 使用 SAM、DINO、APE、SegEarth-OV 官方权重 | OVCD 协议的来源，SECOND 的 OR 规则在 `eval/evaluate_second.py` 中 |
| OmniOVCD | Erxucomeon/OmniOVCD | 完整 | SAM 3 官方权重 | 只用 SAM 3。本仓库的“SAM3+OmniOVCD”是旧原型（overlap-presence 匹配，阈值 0.25），LEVIR/WHU F1 73.13/56.37（`results/env.md`），与论文设置不同 |
| ReA-OVCD | Funny0101/ReA-OVCD | 完整 | SAM 3 官方权重 | 有代码的免训练方法中报告数字最高 |
| AdaptOVCD | Dmygithub/AdaptOVCD | 完整 | 使用 SAM-HQ、DINOv3、DGTRS-CLIP 官方权重 | M-C-I 路线最新版本 |
| OpenDPR | guoqi2002/OpenDPR | 推理与评测代码 | SAM、DINOv2 需自行下载；OpenDPR-W 只给 S2C 推理结果 | CVPR 2026 |
| Seg2Change | yogurts-sy/Seg2Change | 训练与测试 | 提供 CACH `best.pth` 和训练日志 | 训练集含 SECOND 训练划分 |
| CogVis | KotlinWang/CogVis | 推理与训练 | README 写明权重不放在仓库中，未找到下载链接 | 需要自己训练适配器 |
| AnyChange | Z-Zheng/pytorch-change-models（torchange） | 单文件实现 | SAM 官方权重 | 类别无关，需配合识别器 |
| SCM | StephenApX/UCD-SCM | 完整 | FastSAM、CLIP 官方权重 | 仅建筑 |
| Zero-OVCD | 1321663019/Zero-OVCD | 只有 README 和两个第二阶段配置文件 | 无 | 第一阶段代码未公开 |
| MemOVCD | kzigzag/MemOVCD | 无，README 写明录用后发布 | 无 | 只能引用数字 |
| UniVCD | Die-Xie/UniVCD | 无，README 写明录用后发布 | 无 | 只能引用数字 |
| CoRegOVCD | 未找到 | – | – | 只能引用数字 |
| RCDNet / RCDGen | yilmazkorkmaz1/referring_change_detection | 完整 | RCDGen 在 HuggingFace；RCDNet 在 Google Drive（只用真实数据训练） | 指代任务；变化定义只算新增方向 |
| QSCP | qianyuancs/QSCP | 研究代码 | README 写明不提供预训练模型 | – |
| UniChange | NKU-HLT/UniChange | 训练与推理 | 2026-10-20 发布 final_weight（百度网盘） | 在四个评测数据集上联合训练 |
| DeltaVLM | hanlinwu/DeltaVLM | 完整 | HuggingFace `hlwu/DeltaVLM` | 只输出文本 |
| TEOChat | ermongroup/TEOChat | 完整 | HuggingFace `jirvin16/TEOChat` | 变化检测以框输出 |
| Change-Agent | Chen-Yang-Liu/Change-Agent | 完整 | 提供 MCI_model.pth | 只覆盖道路和建筑 |
| DynamicVL | weihao1115/dynamicvl | 完整 | 数据集在 HuggingFace | DVLChat 权重下载说明本次未找到 |
| ChangeMamba | ChenHongruixuan/MambaCD | 完整 | 官方权重（本仓库已用 MambaSCD-Base） | 封闭集参照 |

## 对我的任务定义的建议

**任务设置。**

- 输入：两期影像 (I1, I2) 和类别文本 c。输出：c 的二值变化掩码。
- 真值：按 DynamicEarth 评测代码的 OR 规则，变化像素中 t1 或 t2 属于 c 即为正样本。这与 DynamicEarth、Zero-OVCD、CoRegOVCD 的 SECOND 评测相同，结果可以直接与这些论文比较。
- 方向分项：在 SECOND 上额外按方向拆成三项分别报告召回。
  - 出现：t2 = c，t1 ≠ c。
  - 消失：t1 = c，t2 ≠ c。
  - 同类内变化：t1 = t2 = c。
  
  这样可以直接量出基于语义差异的方法在同类内变化上的盲区（第 6 节），也能换算到 RCD 的“新增方向”口径。
- 文本：每个类别使用一份固定、公开的同义词表，背景提示固定为 background 加其余类别名。不允许按数据集调整提示。
- 阈值：所有阈值在一个不参与评测的数据集上确定，再固定用于全部评测集。
- 模块训练：初始变化、补漏、MLLM 优化三个模块按免训练设计。如果某个模块需要训练，训练数据不得包含任何评测数据集。此时按训练标签是否覆盖该类别，把 SECOND 六类分成“见过”和“没见过”两组分别报告。

**数据集与划分。**

- LEVIR-CD 官方测试集 128 对。
- WHU-CD 采用 DynamicEarth 评测脚本的 744 对 256² 测试划分，与 DynamicEarth、Zero-OVCD 相同。
- S2Looking 官方测试集 1,000 对，用来检验侧视和配准错位下的伪变化。
- SECOND 测试集 1,694 对，与 OmniOVCD、AdaptOVCD、ReA-OVCD、Seg2Change 及本仓库已有数据相同；六类逐类评测。
- SYSU-CD 没有类别标签，不进入按类别查询的主表。
- 所有 baseline 在上述同一划分上用官方代码重新运行。论文报告的数字只作为附表引用，并注明各自的划分。

**评测协议。**

- 主指标：变化类 F1^c。同时报告 IoU^c、Precision、Recall，TP、FP、FN 在整个测试集上累计。SECOND 报告六类宏平均和逐类数值。
- 目标级指标：整目标漏检率，即真值连通域与预测的 IoU < 0.1 的比例，与本仓库实验 1 的定义相同。另报告整目标误检率。这两项对应流程中的补漏和 MLLM 去误检两步。现有 OVCD 论文都没有报告目标级指标。
- 辅助指标：逐图平均 F1。QUAKE-CD 的结果说明累计 F1 与逐图平均 F1 可能相差很大 [QUAKE-CD 表1]。
- 分项：SECOND 按出现、消失、同类内变化三个方向分项报告。
- 效率：每对影像的推理时间，以及 MLLM 调用次数。

**三个必须比较的 baseline。**

1. **DynamicEarth。** 同时运行 M-C-I（SAM-DINOv2-SegEarth-OV）和 I-M-C（APE-DINO）两个配置。它是 OVCD 任务和评测协议的来源，代码按数据集、按类别给出评测脚本，所有后续论文都和它比较。
2. **ReA-OVCD。** 有完整代码的免训练方法中，它报告的数字最高：LEVIR-CD F1^C 84.52、WHU-CD 90.31、SECOND 平均 47.19。它基于 SAM 3 做像素级比较，并带有边界可靠性筛选，正好用来衡量本方法在伪变化上的改进。
3. **Seg2Change。** 有公开训练权重的需训练开放词汇方法中，它在建筑类上最强（SECOND 建筑 F1^c 76.36）。它代表“训练类别无关变化头”这条路线。比较时须注明它的训练集含 SECOND 训练划分。

另外两类结果放进同一张表，作为参照行：

- **全监督上界**：本仓库的 SChanger 和 MambaSCD。
- **引用数字**：Zero-OVCD、CoRegOVCD、MemOVCD 没有完整代码，只引用论文数字并注明划分。Zero-OVCD 的第一阶段包含专门的补漏模块，与本方法的补漏步骤直接对应，表中应单独列出。

## 参考来源

“知识库”指 `Obsidian Vault/遥感Agent知识库/raw/01-论文/` 下对应文件夹的 `source.md`；“arXiv 全文”指本次通过 arXiv API 检索、读取 arXiv HTML 全文的论文，其会议或期刊信息只依据 arXiv 页面的 Comments 和 Journal-ref 字段。

| 简称 | 来源 | 位置 |
|---|---|---|
| DynamicEarth | 知识库 | `dynamicearth-距开放词汇变化检测还有多远/`；arXiv:2501.12931；评测代码 github.com/likyoo/DynamicEarth |
| Zero-OVCD | 知识库 | `zero-ovcd-零样本开放词汇变化检测/`；arXiv:2608.11663 |
| AnyChange | 知识库 | `anychange-免训练目标变化检测/`；arXiv:2402.01188 |
| UniChange | 知识库 | `unichange-统一多模态变化检测/`；arXiv:2511.02607 |
| Delta-LLaVA | 知识库 | `delta-llava-统一遥感变化检测与理解/`；arXiv:2604.14044 |
| Change-Agent | 知识库 | `change-agent-交互式综合遥感变化解译与分析/`；arXiv:2403.19646 |
| ChangeGPT | 知识库 | `changgpt-城市环境变化分析LLM智能体/`；arXiv:2601.02757 |
| GaiaAgent | 知识库 | `gaiaagent-多任务土地覆盖变化检测/`；DOI 10.1016/j.isprsjprs.2026.05.025 |
| DynamicVL | 知识库 | `dynamicvl-动态城市理解多模态大模型基准/` |
| TEOChat | 知识库 | `teochat-时序地球观测视觉语言助手/` |
| UniRS | 知识库 | `unirs-统一多时相遥感视觉语言模型/`；arXiv:2412.20742 |
| LaVIDE | 知识库 | `lavide-语言提示地图影像对齐变化检测/`；arXiv:2411.19758 |
| SAM-CD | 知识库 | `sam-cd-视觉基础模型适配变化检测/`；arXiv:2309.01429 |
| TriSV-CD | 知识库 | `trisv-cd-变化检测选择性验证与回滚仲裁/`；DOI 10.1016/j.eswa.2026.134443 |
| KnowChange | 知识库 | `knowchange-知识引导遥感变化数据合成/`；arXiv:2608.24263 |
| ChangeMamba | 知识库 | `changemamba-时空状态空间模型遥感变化检测/` |
| wiki/agentic-segmentation | 知识库 | `wiki/agentic-segmentation.md`（SegAgent、SAM-Veteran、MedSAM-Agent、RemoteAgent 等的综合条目） |
| SECOND | 本地 PDF | `Desktop/papers/01_遥感/数据集与数据构建/SECOND/SECOND.pdf`，§IV–V |
| SCM | arXiv 全文 | arXiv:2312.16410 |
| Semantic-CD | arXiv 全文 | arXiv:2501.06808 |
| OmniOVCD | arXiv 全文 | arXiv:2601.13895 |
| AdaptOVCD | arXiv 全文 | arXiv:2602.06529 |
| OpenDPR | arXiv 全文 | arXiv:2603.27645 |
| CoRegOVCD | arXiv 全文 | arXiv:2604.02160 |
| MemOVCD | arXiv 全文 | arXiv:2604.26774 |
| ReA-OVCD | arXiv 全文 | arXiv:2606.20032 |
| UniVCD | arXiv 全文 | arXiv:2512.13089 |
| Seg2Change | arXiv 全文 | arXiv:2604.11231 |
| CogVis | arXiv 全文 | arXiv:2608.06150 |
| RCD（RCDNet、RCDGen） | arXiv 全文 | arXiv:2512.11719 |
| QSCP | arXiv 全文 | arXiv:2609.33088 |
| LG-CD | arXiv 全文 | arXiv:2509.21894 |
| ViLaCD-R1 | arXiv 全文 | arXiv:2512.23244 |
| QUAKE-CD | arXiv 全文 | arXiv:2609.09876 |
| DeltaVLM | arXiv 全文 | arXiv:2507.22346 |
