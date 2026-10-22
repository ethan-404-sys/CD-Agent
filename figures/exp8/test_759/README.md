# test_759 新旧池对照

| 池 | 单元数 |
|---|---:|
| 旧池（全部六来源）切块 | 1302 |
| 旧池（全部六来源）候选 | 221 |
| 本批原始 B+G0.05+H1 候选 | 176 |
| objects_none | 176 |
| objects_direct_pairs | 153 |
| objects_transitive | 132 |
| objects_nms_area | 135 |
| U0 | 803 |
| U4 | 435 |
| U5_x4 | 362 |

new_pool.jpg 是验证集冻结 U5；new_objects.jpg 是默认直接对合并对象。
复用旧图绘制函数：t2、随机色半透明填充、黑轮廓、白色真值轮廓、红色 piece 619 框。
旧池的六来源与本批三来源不同；U0 行提供相同来源的切块对照。
