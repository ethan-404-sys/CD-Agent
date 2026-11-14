# T3 semantic bridge: fixed development results

Completed tasks: 60/60. Model: qwen3.7-flash.

The two single-date exemplar requests contain no task category. DINO and bridge use the same cached proposals; verification uses the same eight-view question and shared candidate responses. All scores exclude the supplied exemplar. Oracle class ranking is an offline diagnostic only.

Class agreement is agreement with existing labels, not adjudicated visual understanding accuracy. Individual grounding and label ambiguities require the saved-input audits.

| Group | Tasks | Before agreement | After agreement | Pair agreement |
|---|---:|---:|---:|---:|
| 全部 | 60 | 53.333 | 50.000 | 21.667 |
| 目标类 | 27 | 51.852 | 66.667 | 25.926 |
| 地表类 | 33 | 54.545 | 36.364 | 18.182 |

| Group | Method | F1 | Precision | Recall | Candidate pixel coverage | Component recall |
|---|---|---:|---:|---:|---:|---:|
| 全部 | bridge@4 | 2.638 | 3.374 | 2.166 | 2.166 | 1.515 |
| 全部 | bridge@8 | 3.936 | 3.558 | 4.403 | 4.403 | 3.030 |
| 全部 | bridge@16 | 3.742 | 2.561 | 6.947 | 6.947 | 4.545 |
| 全部 | bridge@32 | 4.547 | 2.694 | 14.562 | 14.562 | 8.333 |
| 全部 | bridge_dense@0 | 3.318 | 2.679 | 4.357 | — | — |
| 全部 | bridge_verified@8 | 4.216 | 4.044 | 4.403 | 4.403 | 3.030 |
| 全部 | dino@4 | 3.690 | 2.314 | 9.109 | 9.109 | 4.545 |
| 全部 | dino@8 | 5.360 | 3.162 | 17.586 | 17.586 | 9.848 |
| 全部 | dino@16 | 6.968 | 3.836 | 37.928 | 37.928 | 16.667 |
| 全部 | dino@32 | 5.480 | 2.926 | 43.079 | 43.079 | 25.758 |
| 全部 | dino_verified@8 | 5.756 | 3.447 | 17.429 | 17.429 | 9.091 |
| 全部 | oracle_class_diagnostic@4 | 8.547 | 7.203 | 10.507 | 10.507 | 5.303 |
| 全部 | oracle_class_diagnostic@8 | 11.216 | 7.426 | 22.907 | 22.907 | 11.364 |
| 全部 | oracle_class_diagnostic@16 | 16.922 | 9.807 | 61.650 | 61.650 | 18.182 |
| 全部 | oracle_class_diagnostic@32 | 12.106 | 6.581 | 75.383 | 75.383 | 27.273 |
| 目标类 | bridge@4 | 5.860 | 16.084 | 3.583 | 3.583 | 2.299 |
| 目标类 | bridge@8 | 9.568 | 15.558 | 6.909 | 6.909 | 4.598 |
| 目标类 | bridge@16 | 9.533 | 8.983 | 10.154 | 10.154 | 6.897 |
| 目标类 | bridge@32 | 12.787 | 9.042 | 21.826 | 21.826 | 11.494 |
| 目标类 | bridge_dense@0 | 6.090 | 5.373 | 7.028 | — | — |
| 目标类 | bridge_verified@8 | 9.849 | 17.145 | 6.909 | 6.909 | 4.598 |
| 目标类 | dino@4 | 12.311 | 12.001 | 12.638 | 12.638 | 4.598 |
| 目标类 | dino@8 | 12.877 | 9.068 | 22.205 | 22.205 | 8.046 |
| 目标类 | dino@16 | 15.013 | 8.855 | 49.291 | 49.291 | 13.793 |
| 目标类 | dino@32 | 11.049 | 6.129 | 56.049 | 56.049 | 25.287 |
| 目标类 | dino_verified@8 | 15.341 | 11.719 | 22.201 | 22.201 | 8.046 |
| 目标类 | oracle_class_diagnostic@4 | 18.832 | 24.964 | 15.119 | 15.119 | 6.897 |
| 目标类 | oracle_class_diagnostic@8 | 24.389 | 20.140 | 30.912 | 30.912 | 13.793 |
| 目标类 | oracle_class_diagnostic@16 | 23.775 | 15.698 | 48.980 | 48.980 | 21.839 |
| 目标类 | oracle_class_diagnostic@32 | 19.600 | 11.458 | 67.752 | 67.752 | 32.184 |
| 地表类 | bridge@4 | 0.091 | 0.082 | 0.102 | 0.102 | 0.000 |
| 地表类 | bridge@8 | 0.444 | 0.315 | 0.753 | 0.753 | 0.000 |
| 地表类 | bridge@16 | 0.756 | 0.453 | 2.274 | 2.274 | 0.000 |
| 地表类 | bridge@32 | 0.740 | 0.408 | 3.980 | 3.980 | 2.222 |
| 地表类 | bridge_dense@0 | 0.301 | 0.222 | 0.465 | — | — |
| 地表类 | bridge_verified@8 | 0.488 | 0.361 | 0.753 | 0.753 | 0.000 |
| 地表类 | dino@4 | 0.868 | 0.487 | 3.967 | 3.967 | 4.444 |
| 地表类 | dino@8 | 1.957 | 1.075 | 10.858 | 10.858 | 13.333 |
| 地表类 | dino@16 | 2.488 | 1.321 | 21.373 | 21.373 | 22.222 |
| 地表类 | dino@32 | 2.028 | 1.058 | 24.183 | 24.183 | 26.667 |
| 地表类 | dino_verified@8 | 1.965 | 1.084 | 10.476 | 10.476 | 11.111 |
| 地表类 | oracle_class_diagnostic@4 | 2.048 | 1.403 | 3.790 | 3.790 | 2.222 |
| 地表类 | oracle_class_diagnostic@8 | 3.546 | 2.105 | 11.245 | 11.245 | 6.667 |
| 地表类 | oracle_class_diagnostic@16 | 13.465 | 7.350 | 80.109 | 80.109 | 11.111 |
| 地表类 | oracle_class_diagnostic@32 | 8.428 | 4.430 | 86.501 | 86.501 | 17.778 |

## Individual tasks and failure evidence

Any true/false pixel overlap is a diagnostic signal, not a semantic error label. Empty residual-GT tasks remain in the fixed set and are identified below. Complete raw responses, input images and usage are retained under each request archive.

| Task | Group | Remaining GT pixels | Full proposal coverage | Failure signals | Metrics |
|---|---|---:|---:|---|---|
| train_1065_2to5 | 目标类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_1065_2to5/metrics.json) |
| train_1129_4to5 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1129_4to5/metrics.json) |
| train_1136_5to1 | 目标类 | 3 | 100.000 | verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1136_5to1/metrics.json) |
| train_1166_3to5 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1166_3to5/metrics.json) |
| train_1177_2to3 | 地表类 | 1686 | 74.259 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1177_2to3/metrics.json) |
| train_119_6to2 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_119_6to2/metrics.json) |
| train_127_3to2 | 地表类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_127_3to2/metrics.json) |
| train_1322_2to4 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1322_2to4/metrics.json) |
| train_1344_2to5 | 目标类 | 16056 | 98.667 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1344_2to5/metrics.json) |
| train_1370_3to1 | 地表类 | 791 | 97.092 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1370_3to1/metrics.json) |
| train_1377_5to3 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1377_5to3/metrics.json) |
| train_145_1to2 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_145_1to2/metrics.json) |
| train_147_1to5 | 目标类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_147_1to5/metrics.json) |
| train_1496_2to1 | 地表类 | 4 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1496_2to1/metrics.json) |
| train_1527_5to2 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1527_5to2/metrics.json) |
| train_1535_4to1 | 地表类 | 14 | 100.000 | exemplar_gt_class_disagreement_requires_review, unparsed_verification, verification_removed_true_pixels, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1535_4to1/metrics.json) |
| train_1538_1to3 | 地表类 | 5243 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1538_1to3/metrics.json) |
| train_156_2to3 | 地表类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_156_2to3/metrics.json) |
| train_1659_1to2 | 地表类 | 7720 | 99.404 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1659_1to2/metrics.json) |
| train_1745_5to2 | 目标类 | 12451 | 99.494 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1745_5to2/metrics.json) |
| train_1754_5to1 | 目标类 | 6067 | 95.368 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1754_5to1/metrics.json) |
| train_1798_2to6 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1798_2to6/metrics.json) |
| train_1808_2to1 | 地表类 | 7641 | 99.620 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_1808_2to1/metrics.json) |
| train_1884_4to3 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_1884_4to3/metrics.json) |
| train_2043_2to3 | 地表类 | 386 | 97.668 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2043_2to3/metrics.json) |
| train_206_3to4 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_206_3to4/metrics.json) |
| train_2075_4to2 | 地表类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_2075_4to2/metrics.json) |
| train_2130_2to4 | 地表类 | 0 | — | unparsed_verification, verification_retained_false_pixels | [evidence](dev60/tasks/train_2130_2to4/metrics.json) |
| train_2192_1to5 | 目标类 | 25417 | 99.858 | verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2192_1to5/metrics.json) |
| train_2227_3to2 | 地表类 | 816 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2227_3to2/metrics.json) |
| train_2255_2to5 | 目标类 | 3859 | 99.041 | verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2255_2to5/metrics.json) |
| train_23_2to1 | 地表类 | 3235 | 89.552 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_23_2to1/metrics.json) |
| train_2383_2to5 | 目标类 | 14568 | 97.941 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2383_2to5/metrics.json) |
| train_2437_1to3 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_2437_1to3/metrics.json) |
| train_2438_5to1 | 目标类 | 13025 | 91.155 | exemplar_gt_class_disagreement_requires_review, unparsed_verification, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2438_5to1/metrics.json) |
| train_2461_5to3 | 目标类 | 887 | 99.775 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2461_5to3/metrics.json) |
| train_2571_4to5 | 目标类 | 6 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_2571_4to5/metrics.json) |
| train_2590_3to5 | 目标类 | 1014 | 82.643 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2590_3to5/metrics.json) |
| train_2677_2to1 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_2677_2to1/metrics.json) |
| train_2792_1to5 | 目标类 | 5613 | 99.359 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2792_1to5/metrics.json) |
| train_2822_1to2 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_2822_1to2/metrics.json) |
| train_2828_1to5 | 目标类 | 43900 | 98.629 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2828_1to5/metrics.json) |
| train_2848_1to4 | 地表类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_2848_1to4/metrics.json) |
| train_2890_5to1 | 目标类 | 492 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2890_5to1/metrics.json) |
| train_2912_5to2 | 目标类 | 429 | 94.872 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_2912_5to2/metrics.json) |
| train_2928_3to1 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_2928_3to1/metrics.json) |
| train_368_1to2 | 地表类 | 70577 | 99.565 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_368_1to2/metrics.json) |
| train_37_4to3 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_37_4to3/metrics.json) |
| train_378_1to4 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_378_1to4/metrics.json) |
| train_385_5to6 | 目标类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_385_5to6/metrics.json) |
| train_427_3to1 | 地表类 | 364 | 98.352 | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels, proposal_coverage_gap, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_427_3to1/metrics.json) |
| train_443_3to2 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_443_3to2/metrics.json) |
| train_555_4to2 | 地表类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_555_4to2/metrics.json) |
| train_615_3to4 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_615_3to4/metrics.json) |
| train_7_1to2 | 地表类 | 262 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_7_1to2/metrics.json) |
| train_786_3to5 | 目标类 | 0 | — | verification_retained_false_pixels | [evidence](dev60/tasks/train_786_3to5/metrics.json) |
| train_908_4to1 | 地表类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_908_4to1/metrics.json) |
| train_915_3to4 | 地表类 | 1 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels | [evidence](dev60/tasks/train_915_3to4/metrics.json) |
| train_968_5to3 | 目标类 | 62 | 100.000 | exemplar_gt_class_disagreement_requires_review, verification_removed_true_pixels, verification_retained_false_pixels, bridge_top8_ranking_gap | [evidence](dev60/tasks/train_968_5to3/metrics.json) |
| train_987_3to5 | 目标类 | 0 | — | exemplar_gt_class_disagreement_requires_review, verification_retained_false_pixels | [evidence](dev60/tasks/train_987_3to5/metrics.json) |

See [manual visual audit](manual_dev_review.md) and [smoke review](manual_smoke_review.md).

These are fixed-pipeline offline results. They do not establish Agent adoption or three-seed online gains.
