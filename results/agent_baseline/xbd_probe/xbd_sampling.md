# xBD 抽样

seed=1016; nonempty post-disaster building labels; exactly half severe images (>=1 major/destroyed), half clean images (only no/minor; no un-classified); within each stratum greedily balance disaster type then event, seeded random ties and seeded shuffled names; positive stratum first; no model outputs read

数据路径相对数据根。hold→dev60，test→report100；probe10 是 dev60 子集。
只读灾后 JSON；没有读取模型输出。负图严格仅含 no/minor。
un-classified 单列，不是负类；忽略其像素。本轮指标按建筑计算。

## dev60

图数 60；严重图 30。

| 事件 | 灾种 | 张数 |
| --- | --- | ---: |
| guatemala-volcano | volcano | 4 |
| hurricane-florence | flooding | 4 |
| hurricane-harvey | flooding | 4 |
| hurricane-matthew | wind | 6 |
| hurricane-michael | wind | 5 |
| mexico-earthquake | earthquake | 11 |
| midwest-flooding | flooding | 4 |
| palu-tsunami | tsunami | 10 |
| santa-rosa-wildfire | fire | 6 |
| socal-fire | fire | 6 |

| 等级 | 建筑数 |
| --- | ---: |
| no-damage | 8169 |
| minor-damage | 108 |
| major-damage | 125 |
| destroyed | 172 |
| un-classified | 36 |
| 合计 | 8610 |

## report100

图数 100；严重图 50。

| 事件 | 灾种 | 张数 |
| --- | --- | ---: |
| guatemala-volcano | volcano | 3 |
| hurricane-florence | flooding | 7 |
| hurricane-harvey | flooding | 7 |
| hurricane-matthew | wind | 8 |
| hurricane-michael | wind | 12 |
| mexico-earthquake | earthquake | 20 |
| midwest-flooding | flooding | 7 |
| palu-tsunami | tsunami | 16 |
| santa-rosa-wildfire | fire | 10 |
| socal-fire | fire | 10 |

| 等级 | 建筑数 |
| --- | ---: |
| no-damage | 11862 |
| minor-damage | 529 |
| major-damage | 477 |
| destroyed | 375 |
| un-classified | 36 |
| 合计 | 13279 |

## probe10

图数 10；严重图 5。

| 事件 | 灾种 | 张数 |
| --- | --- | ---: |
| guatemala-volcano | volcano | 2 |
| hurricane-florence | flooding | 1 |
| hurricane-matthew | wind | 1 |
| hurricane-michael | wind | 1 |
| mexico-earthquake | earthquake | 1 |
| midwest-flooding | flooding | 1 |
| palu-tsunami | tsunami | 1 |
| santa-rosa-wildfire | fire | 1 |
| socal-fire | fire | 1 |

| 等级 | 建筑数 |
| --- | ---: |
| no-damage | 506 |
| minor-damage | 8 |
| major-damage | 27 |
| destroyed | 69 |
| un-classified | 5 |
| 合计 | 615 |

