# Smoke实际输入与响应人工复核（2026-11-14）

这是**不完整、有选择偏差的机制审查**。从已存在的答案选择4个候选，覆盖SECOND新建、SECOND拆除、LEVIR三个组；其中LEVIR例因三臂不一致而选中。没有等待缺失答案，没有查询作业，没有进行批量统计。直接查看送入API的JPEG和对应原始响应；未读取GT，未将TP>FP像素收益代理当作语义真值。以下不能用于估计准确率或选择整体最优核验臂。

路径均相对于本报告目录；每个`inputs`目录包含`0.jpg=T1 close`、`1.jpg=T2 close`、`2.jpg=T1 wide`、`3.jpg=T2 wide`和`request.json`。四图、SoM、结构化分别简称four、som、structured。

## 1. LEVIR：结构化问法把轮廓外新住宅归给湖岸候选

**UID：`levir_any:train_106:3`。** S5，20734像素，原图bbox `[0,389,186,711]`。三臂答案为 **no / no / yes**。

- 实际查看：[four T1](smoke/answers/four/inputs/levir_any__train_106__3/0.jpg)、[four T2](smoke/answers/four/inputs/levir_any__train_106__3/1.jpg)、[som T1](smoke/answers/som/inputs/levir_any__train_106__3/0.jpg)、[som T2](smoke/answers/som/inputs/levir_any__train_106__3/1.jpg)，以及[structured T1 close](smoke/answers/structured/inputs/levir_any__train_106__3/0.jpg)、[T2 close](smoke/answers/structured/inputs/levir_any__train_106__3/1.jpg)、[T1 wide](smoke/answers/structured/inputs/levir_any__train_106__3/2.jpg)、[T2 wide](smoke/answers/structured/inputs/levir_any__train_106__3/3.jpg)。
- 红轮廓沿画面左侧湖岸/堤边形成弯曲带状区域。黄色1号的引线指向此带，未观察到标记错移到住宅。两期湖岸存在颜色/外观差别，但不能据此把它认作建筑新建。
- T2画面上方新增住宅明显，主体在该红轮廓之外。four回答非建筑岸线变化；som回答非建筑岸线且无变化。二者对岸线是否发生非建筑变化的描述不完全相同，但建筑变化决策一致。
- [structured响应](smoke/answers/structured/levir_any__train_106__3.json)将`area inside the red contour`描述成T1空地、T2住宅和道路，并据此yes。人工对实际图的判断：该理由与轮廓位置不吻合，支持**将周边变化误归给候选区域**这一具体失败机制；不能把它解释为只是输出措辞差异。
- 四张structured图在视觉上是同一全图的两期重复，所谓close未提供更近的定位。原因与既有裁图公式一致：候选bbox长边322，close的4倍扩边已超过1024原图边长，wide也被截到整图。于是标记区域在图内很偏、周边新住宅十分显著。这是候选几何与原裁图规则的相互作用，不是本轮SoM独自改变了扩边。
- 原始[usage](smoke/answers/structured/usage/levir_any__train_106__3.json)显示输出138 token、`finish_reason=stop`，结构化JSON完整。本例是定位/推理失败证据，**不是截断**。

## 2. SECOND新建：较完整区域三臂一致

**UID：`second_appeared:train_1065:1`。** S1_02，5293像素，bbox `[281,331,377,434]`。三臂均yes。

实际查看[som T1](smoke/answers/som/inputs/second_appeared__train_1065__1/0.jpg)和[som T2](smoke/answers/som/inputs/second_appeared__train_1065__1/1.jpg)：T1轮廓内主要为树/植被，T2出现规则的浅色屋顶区域。黄色标记引线落在该目标内部，没有明显遮掉整个对象；轮廓约束和理由在视觉上吻合。

[four](smoke/answers/four/second_appeared__train_1065__1.json)、[som](smoke/answers/som/second_appeared__train_1065__1.json)、[structured](smoke/answers/structured/second_appeared__train_1065__1.json)都描述植被处出现建筑。该例支持核验链能处理明显新建，但不能证明SoM或结构化增益。structured输出167 token、原始usage为stop，无截断。

## 3. SECOND拆除：目标消失，但周边新建筑容易造成表述混淆

**UID：`second_disappeared:train_1065:1`。** S1_02，346像素，bbox `[239,424,262,448]`。三臂均yes。

直接查看[som T1 close](smoke/answers/som/inputs/second_disappeared__train_1065__1/0.jpg)、[T2 close](smoke/answers/som/inputs/second_disappeared__train_1065__1/1.jpg)、[T1 wide](smoke/answers/som/inputs/second_disappeared__train_1065__1/2.jpg)、[T2 wide](smoke/answers/som/inputs/second_disappeared__train_1065__1/3.jpg)：T1轮廓内有小型浅色规则构筑物，T2对应位置主要呈植被/暗色地表，周边出现大型建筑。标记引线指向目标区域，两期位置一致。图像较模糊，不将建筑用途/精确边界作确定人工标签。

[four](smoke/answers/four/second_disappeared__train_1065__1.json)说小建筑消失；[som](smoke/answers/som/second_disappeared__train_1065__1.json)提到该位置被新大建筑及空地占据；[structured](smoke/answers/structured/second_disappeared__train_1065__1.json)更明确区分附近大建筑与原小建筑足迹。决策相同，理由的定位精度有区别，不能仅按yes/no统计掩盖。structured输出135 token、stop，无截断。

## 4. SECOND新建：52像素碎片会被按整栋建筑解释

**UID：`second_appeared:train_1065:4`。** 排名首次来源S5，元数据同时记录S2/S1_02/S4完全相同像素候选；仅52像素，bbox `[192,316,204,323]`。三臂均yes。

实际查看[som T1](smoke/answers/som/inputs/second_appeared__train_1065__4/0.jpg)和[som T2](smoke/answers/som/inputs/second_appeared__train_1065__4/1.jpg)：小红轮廓在T1地面上、T2大片新屋顶内部。SoM引线位置与这个小区域一致，黄色标签相对候选较大，覆盖/邻接部分局部证据，但未观察到整体错指。

[structured响应](smoke/answers/structured/second_appeared__train_1065__4.json)用“完整大型工业建筑”解释小碎片，体现**候选像素片段与对象级核验问法的粒度错位**。视觉上该片段确处于新屋顶内，因此不能直接把yes判为语义错误；同样，它也不能证明找回了一栋完整建筑。后续应分别看补入像素效果、组件/对象完整性和重复候选，而不是以此yes声称完整对象被补回。structured输出184 token、stop，无截断。

## 审查结论与后续证据需求

这4例没有观察到SoM引线明显落错目标，也没有结构化截断；这只是所选例，不是全smoke断言。结构化在LEVIR例中增加字段后仍会把轮廓外的显著变化错误归到候选。52像素例提示碎片问答的yes不能等价于完整目标召回。暂不据此改代码、增大输出预算或宣布某臂最好。

建议保留三臂完整配对结果，再针对全图退化裁图、边界/岸线长条、极小屋顶碎片分别人工复核。若后续设计更紧局部图，应作为单独、明确的新对照，保持当前四图/SoM/结构化历史输入及结果不变。
