# xBD probe10 验证器报告

状态：complete。已保存请求 610/610；跳过 un-classified 5 栋。
请求尝试 610 次；失败 0 次；已派发但未记录响应 0 次（崩溃或发送前 STOP，计费状态未知）。
响应费用 0.065494 元；ledger 0.065494 元。按现有单价与实际 usage 记账，最终以账单为准。
已知灾后建筑多边形的分类试探；不是检测结果。un-classified 及其像素不算正负。
无法解析的正例计入二分类漏检；负例无法解析数另列。未请求项另列，未完成指标仅供检查。

| 真值 / 预测 | no-damage | minor-damage | major-damage | destroyed | 无法解析 | 未请求 |
| --- |  ---: | ---: | ---: | ---: | ---: | ---: |
| no-damage | 232 | 46 | 96 | 132 | 0 | 0 |
| minor-damage | 0 | 0 | 1 | 7 | 0 | 0 |
| major-damage | 7 | 3 | 4 | 13 | 0 | 0 |
| destroyed | 1 | 0 | 0 | 68 | 0 | 0 |

| 等级 | Precision | Recall |
| --- | ---: | ---: |
| no-damage | 0.9667 | 0.4585 |
| minor-damage | 0.0000 | 0.0000 |
| major-damage | 0.0396 | 0.1481 |
| destroyed | 0.3091 | 0.9855 |

二分类 P/R/F1：0.2648 / 0.8854 / 0.4077。

| 事件 | 请求/应请求 | P | R | F1 | 无法解析正/负 |
| --- | ---: | ---: | ---: | ---: | ---: |
| guatemala-volcano | 39/39 | 0.3750 | 1.0000 | 0.5455 | 0/0 |
| hurricane-florence | 8/8 | 1.0000 | 0.2500 | 0.4000 | 0/0 |
| hurricane-matthew | 20/20 | 0.4500 | 1.0000 | 0.6207 | 0/0 |
| hurricane-michael | 21/21 | 0.0000 | — | 0.0000 | 0/0 |
| mexico-earthquake | 199/199 | 0.0000 | — | 0.0000 | 0/0 |
| midwest-flooding | 58/58 | 0.0000 | — | 0.0000 | 0/0 |
| palu-tsunami | 128/128 | 0.4583 | 0.8684 | 0.6000 | 0/0 |
| santa-rosa-wildfire | 42/42 | 0.9459 | 1.0000 | 0.9722 | 0/0 |
| socal-fire | 95/95 | 0.0000 | — | 0.0000 | 0/0 |

JDS 来源：https://arxiv.org/abs/1911.09296 (xBD, Joint Damage Scale); https://xview2.org/。
