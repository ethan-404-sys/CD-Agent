# exp6j 判错样例（Mini-InternVL2-4B-DA-RS，近景 + 上下文）

每张图：上排 T1 / T2 近景，下排 T1 / T2 上下文；红色为单元新增像素，青色为检测结果 D。MLLM 以 P(yes) > 0.5 判有变化；D 的阈值在另一半验证集上按加权 F1 选。

## MLLM 判错、D 判对（已人工归类）

| 图 | 数据集 | 真值 | P(yes) | D | 类别 | 说明 |
|---|---|---:|---:|---:|---|---|
| [sysu_train_13319_3.jpg](sysu_train_13319_3.jpg) | sysu | 0 | 0.62 | 0.074 | 太小看不清 | 单元在近景角落，只有很小一块，看不出内容 |
| [sysu_train_12610_96.jpg](sysu_train_12610_96.jpg) | sysu | 0 | 0.59 | 0.101 | 季节或色调 | 屋顶在两期光照和颜色不同，结构没变 |
| [sysu_train_12606_2.jpg](sysu_train_12606_2.jpg) | sysu | 0 | 0.65 | 0.032 | 季节或色调 | 两期光照方向不同，阴影位置变化 |
| [sysu_train_13317_84.jpg](sysu_train_13317_84.jpg) | sysu | 0 | 0.68 | 0.028 | 太小看不清 | 单元贴在裁块边缘，只露出几个像素 |
| [sysu_train_13932_10.jpg](sysu_train_13932_10.jpg) | sysu | 0 | 0.68 | 0.056 | 季节或色调 | 林地在 T2 整体更暗，是色调差异 |
| [sysu_train_13385_31.jpg](sysu_train_13385_31.jpg) | sysu | 0 | 0.62 | 0.000 | 认错地物 | 高速路旁的路堤植被带，不是目标类变化 |
| [sysu_train_15124_1.jpg](sysu_train_15124_1.jpg) | sysu | 0 | 0.65 | 0.030 | 配准错位 | 紧贴旁边大片施工区的建筑边缘细条，两期错位 |
| [sysu_train_15400_6.jpg](sysu_train_15400_6.jpg) | sysu | 0 | 0.65 | 0.050 | 真值可疑 | 新建大楼旁的场地由草地变为阴影下的硬地，看起来像真实变化 |
| [sysu_train_13601_21.jpg](sysu_train_13601_21.jpg) | sysu | 0 | 0.59 | 0.000 | 太小看不清 | 林中几处零碎小块 |
| [sysu_train_14017_13.jpg](sysu_train_14017_13.jpg) | sysu | 0 | 0.59 | 0.001 | 季节或色调 | 道路与林带的明暗变化 |
| [second_train_1568_163.jpg](second_train_1568_163.jpg) | second | 0 | 0.59 | 0.011 | 真值可疑 | 裸土变成深色铺装，看起来像真实变化 |
| [second_train_1296_241.jpg](second_train_1296_241.jpg) | second | 0 | 0.59 | 0.002 | 认错地物 | 两排屋顶之间的细条，屋顶重新刷成蓝色，不是变化 |
| [second_train_1274_21.jpg](second_train_1274_21.jpg) | second | 0 | 0.62 | 0.000 | 季节或色调 | 居民楼屋顶的光照与阴影差异 |
| [second_train_156_107.jpg](second_train_156_107.jpg) | second | 0 | 0.71 | 0.000 | 太小看不清 | 农田里的一条细线 |
| [second_train_2048_168.jpg](second_train_2048_168.jpg) | second | 0 | 0.56 | 0.000 | 认错地物 | 厂房屋顶由白色变为蓝色，同一建筑 |
| [second_train_953_57.jpg](second_train_953_57.jpg) | second | 0 | 0.62 | 0.004 | 认错地物 | 屋顶上的车辆或阴影 |
| [second_train_1608_326.jpg](second_train_1608_326.jpg) | second | 0 | 0.65 | 0.000 | 认错地物 | 屋顶由棕色变为蓝色，同一建筑 |
| [second_train_2797_39.jpg](second_train_2797_39.jpg) | second | 0 | 0.65 | 0.014 | 季节或色调 | 建筑旁树木与阴影的明暗变化 |
| [second_train_2913_57.jpg](second_train_2913_57.jpg) | second | 0 | 0.62 | 0.002 | 季节或色调 | 路旁树带的季节变化 |
| [second_train_1683_47.jpg](second_train_1683_47.jpg) | second | 0 | 0.59 | 0.000 | 认错地物 | 温室顶棚颜色变化，同一设施 |

## MLLM 判对、D 判错（未归类）

| 图 | 数据集 | 真值 | P(yes) | D | 类别 | 说明 |
|---|---|---:|---:|---:|---|---|
| [sysu_train_13604_128.jpg](sysu_train_13604_128.jpg) | sysu | 1 | 0.65 | 0.070 | — | — |
| [sysu_train_12615_28.jpg](sysu_train_12615_28.jpg) | sysu | 1 | 0.59 | 0.081 | — | — |
| [sysu_train_12610_77.jpg](sysu_train_12610_77.jpg) | sysu | 1 | 0.56 | 0.088 | — | — |
| [sysu_train_13323_91.jpg](sysu_train_13323_91.jpg) | sysu | 1 | 0.56 | 0.011 | — | — |
| [sysu_train_14685_52.jpg](sysu_train_14685_52.jpg) | sysu | 1 | 0.56 | 0.011 | — | — |
| [sysu_train_13661_0.jpg](sysu_train_13661_0.jpg) | sysu | 1 | 0.62 | 0.043 | — | — |
| [sysu_train_13804_20.jpg](sysu_train_13804_20.jpg) | sysu | 1 | 0.62 | 0.092 | — | — |
| [sysu_train_14828_30.jpg](sysu_train_14828_30.jpg) | sysu | 1 | 0.56 | 0.009 | — | — |
| [sysu_train_14575_27.jpg](sysu_train_14575_27.jpg) | sysu | 1 | 0.53 | 0.026 | — | — |
| [sysu_train_15122_9.jpg](sysu_train_15122_9.jpg) | sysu | 1 | 0.62 | 0.018 | — | — |
| [second_train_1834_368.jpg](second_train_1834_368.jpg) | second | 1 | 0.56 | 0.001 | — | — |
| [second_train_1237_364.jpg](second_train_1237_364.jpg) | second | 1 | 0.59 | 0.001 | — | — |
| [second_train_1237_161.jpg](second_train_1237_161.jpg) | second | 1 | 0.62 | 0.040 | — | — |
| [second_train_1834_259.jpg](second_train_1834_259.jpg) | second | 1 | 0.59 | 0.010 | — | — |
| [second_train_2590_215.jpg](second_train_2590_215.jpg) | second | 1 | 0.62 | 0.001 | — | — |
| [second_train_1928_73.jpg](second_train_1928_73.jpg) | second | 1 | 0.62 | 0.000 | — | — |
| [second_train_2068_167.jpg](second_train_2068_167.jpg) | second | 1 | 0.68 | 0.025 | — | — |
| [second_train_2767_82.jpg](second_train_2767_82.jpg) | second | 1 | 0.65 | 0.045 | — | — |
| [second_train_2438_193.jpg](second_train_2438_193.jpg) | second | 1 | 0.62 | 0.028 | — | — |
| [second_train_618_401.jpg](second_train_618_401.jpg) | second | 1 | 0.65 | 0.004 | — | — |
