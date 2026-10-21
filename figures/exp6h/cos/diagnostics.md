# 6h-1 两期余弦诊断

正例为真变化。原始 cos 与固定方向 1−cos 均报告；不按数据挑方向。原始 DINOv3 对照是已有固定高斯投影后的 256 维 f1/f2，未做训练集标准化。G1-0 为未加 L_t 的投影头对照。RemoteCLIP 是不同候选群体的历史参照。

| 模型 | 数据集 | n | AUC(cos) | AUC(1−cos) | 来源/群体 |
|---|---|---|---|---|---|
| raw_dinov3_256 | second_cv | 286287 | 0.2154 | 0.7846 | labeled_recall_pieces |
| h1_g1_0_s0 | second_cv | 286287 | 0.1860 | 0.8140 | labeled_recall_pieces |
| h1_g1_0_s1 | second_cv | 286287 | 0.1817 | 0.8183 | labeled_recall_pieces |
| h1_g1_0_s2 | second_cv | 286287 | 0.1889 | 0.8111 | labeled_recall_pieces |
| h1_g1_cos_l0.1_s0 | second_cv | 286287 | 0.1856 | 0.8144 | labeled_recall_pieces |
| h1_g1_cos_l0.1_s1 | second_cv | 286287 | 0.1768 | 0.8232 | labeled_recall_pieces |
| h1_g1_cos_l0.1_s2 | second_cv | 286287 | 0.1781 | 0.8219 | labeled_recall_pieces |
| h1_g1_cos_l0.3_s0 | second_cv | 286287 | 0.1807 | 0.8193 | labeled_recall_pieces |
| h1_g1_cos_l0.3_s1 | second_cv | 286287 | 0.1782 | 0.8218 | labeled_recall_pieces |
| h1_g1_cos_l0.3_s2 | second_cv | 286287 | 0.1846 | 0.8154 | labeled_recall_pieces |
| h1_g1_cos_l1_s0 | second_cv | 286287 | 0.1835 | 0.8165 | labeled_recall_pieces |
| h1_g1_cos_l1_s1 | second_cv | 286287 | 0.1822 | 0.8178 | labeled_recall_pieces |
| h1_g1_cos_l1_s2 | second_cv | 286287 | 0.1803 | 0.8197 | labeled_recall_pieces |
| h1_g1_sm_l0.1_s0 | second_cv | 286287 | 0.2191 | 0.7809 | labeled_recall_pieces |
| h1_g1_sm_l0.1_s1 | second_cv | 286287 | 0.2093 | 0.7907 | labeled_recall_pieces |
| h1_g1_sm_l0.1_s2 | second_cv | 286287 | 0.1951 | 0.8049 | labeled_recall_pieces |
| h1_g1_sm_l0.3_s0 | second_cv | 286287 | 0.2094 | 0.7906 | labeled_recall_pieces |
| h1_g1_sm_l0.3_s1 | second_cv | 286287 | 0.2046 | 0.7954 | labeled_recall_pieces |
| h1_g1_sm_l0.3_s2 | second_cv | 286287 | 0.2059 | 0.7941 | labeled_recall_pieces |
| h1_g1_sm_l1_s0 | second_cv | 286287 | 0.1888 | 0.8112 | labeled_recall_pieces |
| h1_g1_sm_l1_s1 | second_cv | 286287 | 0.2048 | 0.7952 | labeled_recall_pieces |
| h1_g1_sm_l1_s2 | second_cv | 286287 | 0.2126 | 0.7874 | labeled_recall_pieces |
| raw_dinov3_256 | second | 1824655 | 0.2207 | 0.7793 | labeled_recall_pieces |
| h1_g1_0_s0 | second | 1824655 | 0.1854 | 0.8146 | labeled_recall_pieces |
| h1_g1_0_s1 | second | 1824655 | 0.1828 | 0.8172 | labeled_recall_pieces |
| h1_g1_0_s2 | second | 1824655 | 0.1856 | 0.8144 | labeled_recall_pieces |
| h1_g1_cos_l0.1_s0 | second | 1824655 | 0.1855 | 0.8145 | labeled_recall_pieces |
| h1_g1_cos_l0.1_s1 | second | 1824655 | 0.1792 | 0.8208 | labeled_recall_pieces |
| h1_g1_cos_l0.1_s2 | second | 1824655 | 0.1825 | 0.8175 | labeled_recall_pieces |
| h1_g1_cos_l0.3_s0 | second | 1824655 | 0.1830 | 0.8170 | labeled_recall_pieces |
| h1_g1_cos_l0.3_s1 | second | 1824655 | 0.1818 | 0.8182 | labeled_recall_pieces |
| h1_g1_cos_l0.3_s2 | second | 1824655 | 0.1887 | 0.8113 | labeled_recall_pieces |
| h1_g1_cos_l1_s0 | second | 1824655 | 0.1830 | 0.8170 | labeled_recall_pieces |
| h1_g1_cos_l1_s1 | second | 1824655 | 0.1851 | 0.8149 | labeled_recall_pieces |
| h1_g1_cos_l1_s2 | second | 1824655 | 0.1865 | 0.8135 | labeled_recall_pieces |
| h1_g1_sm_l0.1_s0 | second | 1824655 | 0.2179 | 0.7821 | labeled_recall_pieces |
| h1_g1_sm_l0.1_s1 | second | 1824655 | 0.2062 | 0.7938 | labeled_recall_pieces |
| h1_g1_sm_l0.1_s2 | second | 1824655 | 0.2028 | 0.7972 | labeled_recall_pieces |
| h1_g1_sm_l0.3_s0 | second | 1824655 | 0.2111 | 0.7889 | labeled_recall_pieces |
| h1_g1_sm_l0.3_s1 | second | 1824655 | 0.2101 | 0.7899 | labeled_recall_pieces |
| h1_g1_sm_l0.3_s2 | second | 1824655 | 0.2128 | 0.7872 | labeled_recall_pieces |
| h1_g1_sm_l1_s0 | second | 1824655 | 0.1992 | 0.8008 | labeled_recall_pieces |
| h1_g1_sm_l1_s1 | second | 1824655 | 0.2124 | 0.7876 | labeled_recall_pieces |
| h1_g1_sm_l1_s2 | second | 1824655 | 0.2119 | 0.7881 | labeled_recall_pieces |
| exp5 RemoteCLIP exp4b/clip_dissim_ctx | second | 300 | 0.3846 | 0.6154 | outputs/CD_1016_probe/exp4b/second/analysis_5b.json |
| exp5 RemoteCLIP exp4b/clip_dissim_tight | second | 300 | 0.3648 | 0.6352 | outputs/CD_1016_probe/exp4b/second/analysis_5b.json |
| exp5 RemoteCLIP exp5b/clip_dissim_ctx | second | 300 | 0.3273 | 0.6727 | outputs/CD_1016_probe/exp5b/second/analysis_5b.json |
| exp5 RemoteCLIP exp5b/clip_dissim_tight | second | 300 | 0.4022 | 0.5978 | outputs/CD_1016_probe/exp5b/second/analysis_5b.json |

RemoteCLIP 无同一 297 图验证集的已确认结果；由 Claude 查证后手填，不能移用测试值。
