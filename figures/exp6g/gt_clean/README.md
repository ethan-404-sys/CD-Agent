# SECOND 原始真值展示图（test_759、test_518）

只画数据集自带内容：影像、二值变化真值、两期语义标签。没有候选、切块或模型输出。代码：`code/probe/exp6g_gt_clean.py`（读图与配色同 `exp6g_gt_overlay.py`：`cls_gray.json`、`cls_rgb.json`，叠加 alpha 0.55，"未变化"不着色），dpi 150。

## 面板

| 位置 | 面板 | 单独文件（`<图名>_panels/`） |
|---|---|---|
| 第一行左 | t1 原图（前期） | `t1.png` |
| 第一行中 | t2 原图（后期） | `t2.png` |
| 第一行右 | 二值变化真值 `label_unch0chi255`（白 = 变化） | `change.png` |
| 第二行左 | slabel1（前期语义）叠在 t1 上 | `slabel1_on_t1.png` |
| 第二行中 | slabel2（后期语义）叠在 t2 上 | `slabel2_on_t2.png` |
| 第二行右 | 局部放大：左 slabel1 叠 t1，右 slabel2 叠 t2，最近邻放大到与其他面板等高 | `zoom.png` |

第一行三个面板上的白色细虚线框是放大窗口的位置，也是图中唯一的框。图例为六个语义类别（low vegetation、N.V.G surface、tree、water、building、playground）。

## 放大窗口（最终坐标，未调整）

| 图 | x | y |
|---|---|---|
| test_759 | 80–280 | 40–180 |
| test_518 | 175–355 | 300–460 |

## 标注情况

- **test_759**：院子左侧被标为变化的林地，前期（slabel1）标成 N.V.G surface（非植被地表），后期（slabel2）标成 low vegetation（窗口内两期各 7,599 像素，掩膜完全相同）。院子中间成行的同类林木在两期都没有标注，二值真值中为未变化。
- **test_518**：屋顶由红褐变浅蓝的厂房在两期都标为 building（窗口内两期各 10,647 像素，掩膜完全相同），二值真值把它整体标为变化。轮廓没有随后期屋顶调整：后期左下檐边的浅蓝条带落在标注外。窗口左下的草地前期标 low vegetation、后期标 N.V.G surface（2,333 像素）。

说明：SECOND 在变化区域内两期共用同一个掩膜，只是类别不同；"同类别（building → building）也算变化"是数据集的标注约定。
