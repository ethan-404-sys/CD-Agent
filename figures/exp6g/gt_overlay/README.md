# 6g 审核样本的整图真值叠加

每张 2×3：第一行 t1、t2、二值变化真值（`label_unch0chi255`）；第二行 slabel1 叠 t1、slabel2 叠 t2、候选切块掩膜叠 t2。红色轮廓是候选切块，黄色虚线框是近景框（crop）。整张 512×512，不裁剪；类别颜色取数据集自带 `cls_rgb.json`，"未变化"不着色。标题给出 gt_frac、面积、来源、V1 分数和分层。代码：`code/probe/exp6g_gt_overlay.py`。

网页序号按 `audit/label_audit.html` 中 `ids` 数组的顺序（页面不打乱，第 n 个即 `ids[n-1]`）：

| 文件 | 网页序号 | 图像 / 切块 | 分层 |
|---|---:|---|---|
| `web206_test_204_p95.png` | 206 | test_204 / 95 | S3 |
| `web215_test_791_p715.png` | 215 | test_791 / 715 | S3 |
| `web123_test_1509_p12.png` | 123 | test_1509 / 12 | S2 |
| `named_test_5_p90.png` | — | test_5 / 90 | S1 |
| `named_test_900_p362.png` | — | test_900 / 362 | S1 |
| `named_test_759_p619.png` | — | test_759 / 619 | S1 |

请求中给出的图名（test_5、test_900、test_759）与网页序号 206、215、123 对应的样本不一致，两组都画了。
