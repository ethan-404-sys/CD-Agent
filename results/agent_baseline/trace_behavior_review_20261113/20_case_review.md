# 20 条完整轨迹人工审查

审查者：Codex逐条检查；无独立第二审阅者，一致性未做双人测量。目的抽样，类别可重叠，不据20例估计总体频率。

入选信号审查分布：{"confirmed": 9, "unconfirmed": 3, "false_positive": 5, "partial": 3}

HTML页为自包含交互回放，可下载后直接打开；每例Markdown页可在GitHub直接查看完整步骤与掩码图。

## case_01 · redundancy
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_s3/T1.1xC0/val/val_31`
- [完整回放](case_01.html#review) / [GitHub可直接阅读的全步骤](case_01.md)；29条步骤记录/28次调用；最终F1=0.00
- 判定：真实错误循环；重复拦截本身按既定策略执行；归属：Agent决策 / Harness状态或接口
- 关键步骤：[21, 22, 23, 24, 25, 28]
完整0–28步：分割匹配、局部查看后得到mask#9，但一直set_current=false。21步ask无当前答案失败，22步把mask输入仅接受实例的to_mask失败，23步重复被过滤，24步ask仍失败，25步借submit设为current才继续。最终保留544 FP、GT为空；误检另外涉及视觉判断，不能全归因于这四轮状态失败。
改进与风险：优先提供对已有mask的显式答案切换，保留Agent选择权；不自动采用最新候选，也不因空答案惩罚。

## case_02 · redundancy
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t13_any_s1/T1.3/val/train_1608`
- [完整回放](case_02.html#review) / [GitHub可直接阅读的全步骤](case_02.md)；28条步骤记录/27次调用；最终F1=23.66
- 判定：真实ask/to_mask双调用失败循环；归属：Agent决策 / Harness状态或接口
- 关键步骤：[18, 19, 20, 21, 22, 23, 27]
完整0–27步：前期提议和特征未转化为合理选择，后用蓝屋顶与裸土候选。18–21步在无current与mask误传to_mask间重复；22步union(mask#10,mask#10)只为设置current，生成像素相同新句柄；随后核验和提交。mask#9 F1=50.22，合并屋顶后最终23.66；GT及图像显示把既有屋顶翻色当跨类变化，状态修复不能独自解决语义问题。
改进与风险：用显式答案切换替代恒等union；历史T1.3只作诊断，不改该任务或在其上做新实验。

## case_03 · redundancy
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/whu_g3_any_s1/T1.1xC0/val/train_1056`
- [完整回放](case_03.html#review) / [GitHub可直接阅读的全步骤](case_03.md)；23条步骤记录/22次调用；最终F1=0.00
- 判定：重复全图查看存在，但不能认定是无效循环；归属：未确认 / Toolbox能力 / Agent决策
- 关键步骤：[1, 12, 18, 19, 21, 22]
完整0–22步：第1、12、18步相同全图view穿插分割、局部观察、核验，信息关注点不同。最终提交空掩码，漏2112 TP；GT主要位于图块边缘，候选与验证器将车辆/临时对象排除。确有漏检，但无法证明重复view导致漏检，也未发现状态损坏。
改进与风险：保留重新观察能力；改用是否获得新证据评估冗余，不对同参数view一刀切。边界候选问题本轮不改显示尺寸。

## case_04 · redundancy
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/second_g5box_appeared_s1/T1.1xC4/val/train_1119`
- [完整回放](case_04.html#review) / [GitHub可直接阅读的全步骤](case_04.md)；26条步骤记录/25次调用；最终F1=0.00
- 判定：同框重新分割无新结果，并伴随候选退化；归属：Agent决策 / Harness状态或接口 / Verifier判断
- 关键步骤：[7, 10, 15, 16, 18, 23, 25]
完整0–25步：mask#5已含2259 TP、398 FP，F1=65.67；10和18步完全相同框生成mask#6与mask#9，均0 TP、8099 FP。中间ask无current及to_mask(mask)失败，之后始终围绕错误建筑改动；最终0 TP、1943 FP。图像与GT显示关注了错误区域，提交区域裁切按G5生效。
改进与风险：答案切换可减少操作性绕路，但不能保证选对候选；不自动按离线最佳F1回滚。

## case_05 · redundancy
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t52_k1_s2/T5.2/val/val_44`
- [完整回放](case_05.html#review) / [GitHub可直接阅读的全步骤](case_05.md)；12条步骤记录/7次调用；最终F1=90.81
- 判定：越界请求跨拦截后重复；后续已自行修正；归属：Agent决策
- 关键步骤：[1, 2, 3, 4, 5, 6, 8, 10]
完整0–10步含一条同编号报告：前五轮反复请求x=1080超出1024；6步改成1024后恢复。8步局部添加增加2810像素，F1由92.39降至90.81，核验全yes；重复错误与后续分割FP是不同原因。
改进与风险：记录无进展重试证据，不继续Gate6或坐标显示研究；局部反馈应保留未点区域，不能以整图F1替代指令遵循。

## case_06 · excess_calls
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_s1/T5.1/val/val_21`
- [完整回放](case_06.html#review) / [GitHub可直接阅读的全步骤](case_06.md)；31条步骤记录/29次调用；最终F1=87.42
- 判定：“过度调用”主标签不成立，长轨迹带来明显净收益；归属：Agent决策（有效修正）；局部ID管理问题
- 关键步骤：[6, 9, 16, 22, 25, 27, 28, 29]
完整0–29步含27步报告：175组件被分批核验和清理，TP始终72460，FP由初始大量伪变化降到15501，F1从62.01升87.42。16、22、25步确有已删ID被再次引用；27步主动发现核验意见冲突并查看，28步集中清除FP。29次调用高于组中位数13，不等于浪费。
改进与风险：此类作为保护集候选机制，不加调用数硬上限；优先改善ID与状态可见性，避免为省调用损害精度。

## case_07 · excess_calls
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/second_g5box_any_s1/T1.1xC2/val/train_1114`
- [完整回放](case_07.html#review) / [GitHub可直接阅读的全步骤](case_07.md)；24条步骤记录/26次调用；最终F1=0.00
- 判定：连续view不是已证实浪费；语义误判与漏检更关键；归属：Verifier判断 / Agent决策
- 关键步骤：[16, 18, 19, 20, 21, 22, 23]
完整0–23步：18步否定大候选，19–21步分别看局部、全区和另一局部，确在尝试核查。之后提交空掩码漏2073 TP。图像中目标屋顶被解释为停车场；候选原已低召回，F1从10.86变0。不能把三个view当根因或声称Agent未看图就盲信。
改进与风险：改进核验冲突处理需独立对照；当前保留人工证据，不追加危险删除一律拦截。

## case_08 · excess_calls
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t54_any_s1/T5.4/val/train_100`
- [完整回放](case_08.html#review) / [GitHub可直接阅读的全步骤](case_08.md)；22条步骤记录/21次调用；最终F1=94.84
- 判定：不同区域的连续查看属于正常核验；归属：Agent决策（基本有效）
- 关键步骤：[8, 10, 11, 12, 13, 14, 15, 21]
完整0–21步：T5.4使用旧地图与T2，10–13步查看整体及三个不同边界组件。初始候选F1=95.14，核验后删去276 TP和123 FP，最终94.84，约−0.30分；不能用轻微离线退化证明查看过度。工具正确使用map#0，视觉输入首幅是旧图而非T1影像。
改进与风险：作为简单保护案例；不改地图任务与显示机制，不把合理观察删掉。

## case_09 · excess_calls
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t53trial_fb_s1/T5.3/val/val_1`
- [完整回放](case_09.html#review) / [GitHub可直接阅读的全步骤](case_09.md)；7条步骤记录/6次调用；最终F1=70.69
- 判定：固定提交核验后出现可省观察，但没有答案退化；归属：Harness状态或接口 / Agent决策
- 关键步骤：[1, 2, 3, 4, 5, 6]
完整0–6步：用户明确“未指出错误，保持mask#0不变”。1步已正确提交，固定核验门返回报告后又inspect及三次view，6步原样再提交，F1保持70.69。标签捕获额外调用，但其源头包括固定提交门，并非失控循环。
改进与风险：未来可研究明确保持原样任务的提交交互；本轮先避免改变现有核验门，防止跳过必要审查。

## case_10 · excess_calls
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/g4dev_hintA_any_s1/T1.2xC0/val/train_1136`
- [完整回放](case_10.html#review) / [GitHub可直接阅读的全步骤](case_10.md)；26条步骤记录/25次调用；最终F1=11.84
- 判定：查看并非主要损失，类别合并后的差分丢失目标变化；归属：Agent决策 / Toolbox能力
- 关键步骤：[7, 8, 9, 10, 11, 12, 13, 14, 20, 25]
完整0–25步：分别语义分割building/low vegetation后，各期先union再做两向subtract，跨目标类别转移会被并集抵消；mask#8 F1仅12.00。12–14步连续查看不同范围是合理检查。后删纯TP170像素，最终11.84。语义掩码质量也较差，不能只怪规划；核验大量yes不能补回缺失像素。
改进与风险：旧T1.2任务口径与候选生成问题单列；本批不启动类别组合实验，不用阈值或看图限制掩盖主要损失。

## case_11 · tool_failure
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t13_any_s3/T1.3/val/train_1049`
- [完整回放](case_11.html#review) / [GitHub可直接阅读的全步骤](case_11.md)；32条步骤记录/30次调用；最终F1=22.36
- 判定：真实后端请求失败，另有候选利用不足；归属：Harness状态或接口（后端请求故障，代码Bug未确认） / Agent决策
- 关键步骤：[9, 10, 11, 20, 24, 26, 30]
完整0–30步：9步无current错误被10步正确修复；11、20步ask在backends→planner.complete抛PlannerError，27步同类调用恢复，故不能归为Agent工具参数错误。24步候选mask#15离线F1=89.93，但未被采纳，最终建筑/水窄候选F1=22.36并预算终止。缺少底层HTTP证据，不擅自判为网络或鉴权原因。
改进与风险：后端错误保留原状态且允许有界恢复，当前已有实现；先研究候选答案切换，不改T1.3工具或自动选择GT最佳候选。

## case_12 · tool_failure
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/second_g5_appeared_s1/T1.1xC1/val/train_1114`
- [完整回放](case_12.html#review) / [GitHub可直接阅读的全步骤](case_12.md)；22条步骤记录/23次调用；最终F1=88.49
- 判定：核验否定后误删112个纯TP像素，损失较小；归属：Verifier判断 / Agent决策
- 关键步骤：[16, 17, 18, 20, 21]
完整0–21步：前期四次过大框被拒，缩框后恢复，最终改用文本实例。17步no意见对应112 TP、0 FP，18步删除，F1从88.75降88.49。核验被接受但不足以证明认知上的盲信；该小组件可能涉及标注边界，结论限于离线指标损失。
改进与风险：保留局部像素证据，不据此强制二次核验所有删除；风险是增加调用且保留真正FP。

## case_13 · tool_failure
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/whu_g3_any_s1/T1.1xC0/val/train_1007`
- [完整回放](case_13.html#review) / [GitHub可直接阅读的全步骤](case_13.md)；21条步骤记录/20次调用；最终F1=0.00
- 判定：像素损失成立，但“验证器语义错误”尚不能确认；归属：未确认 / Toolbox能力
- 关键步骤：[12, 14, 15, 16, 19, 20]
完整0–20步：候选mask#7只有182 TP、1738 FP，删除三个no组件后只留159 FP，最终F1=0。GT在多座外观未明显新增建筑边缘呈细轮廓；被标“有害删除”的组件只有112 TP、77 FP。可能是边界/配准与标注口径，不能仅以TP交集断言验证器语义错误；主要漏检在删除前已存在。
改进与风险：将纯像素损失与语义错判分开；不据这例盲加删除护栏，不开展新标注。

## case_14 · tool_failure
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t52_k1_s3/T5.2/val/val_31`
- [完整回放](case_14.html#review) / [GitHub可直接阅读的全步骤](case_14.md)；12条步骤记录/11次调用；最终F1=100.00
- 判定：空答案标签误报，但发现超出用户局部授权的编辑；归属：Agent决策（指令范围偏离）
- 关键步骤：[3, 7, 8, 9, 11]
完整0–11步：用户仅要求修改点击(384,424)的组件3，其余保持。3步完成该删除后，8步又删其余组件1/2/4/5；GT恰好为空，因此F1=100。空掩码本身是合法答案，不能判工具失败；但高F1不抵消超出局部反馈范围的指令偏离。
改进与风险：报告F1与指令遵循分开；已有任务约束能力需检查，不能以全局空答案规则处理。

## case_15 · tool_failure
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/g4dev_hintA_any_s1/T1.2xC0/val/train_1177`
- [完整回放](case_15.html#review) / [GitHub可直接阅读的全步骤](case_15.md)；23条步骤记录/22次调用；最终F1=47.79
- 判定：大框拒绝是工具能力边界，另有mask类型误用；归属：Toolbox能力 / Agent决策 / Harness状态或接口
- 关键步骤：[9, 14, 15, 16, 17, 18, 20, 22]
完整0–22步：14步单物体框覆盖12.59%被拒，15步缩框恢复。16步to_mask误接三个mask报类型错，17–18步改union。最早目标mask#4 F1=60.47，加入两个全FP候选后47.79；20步核验全yes未发现FP。不能把合法box_too_large拒绝当Harness Bug。
改进与风险：研究既有mask的显式答案操作；组合候选仍由Agent负责，不放宽大框保护或改旧T1.2语义代码。

## case_16 · state_termination
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/second_g5box_appeared_s2/T1.1xC4/val/train_1114`
- [完整回放](case_16.html#review) / [GitHub可直接阅读的全步骤](case_16.md)；22条步骤记录/21次调用；最终F1=12.36
- 判定：真实大幅答案退化，核验与Agent删减共同造成；归属：Verifier判断 / Agent决策
- 关键步骤：[7, 10, 11, 12, 19, 21]
完整0–21步：mask#5 F1=88.41；10步核验主要组件2/3为no，小组件4/5为yes；11步Agent另查看4/5后12步同时删2/3/4/5，损失4883 TP、减少262 FP，F1降12.36。最终保持此结果。并非状态指针写错；自动回滚需可观察证据，不能拿离线F1当在线准则。
改进与风险：候选保留与显式切换可支持回看，但不保证识别核验错误；高风险删除复核是后续候选，不本轮一次叠加多种规则。

## case_17 · state_termination
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/whu_g3_any_s1/T1.1xC0/val/train_1010`
- [完整回放](case_17.html#review) / [GitHub可直接阅读的全步骤](case_17.md)；32条步骤记录/30次调用；最终F1=0.00
- 判定：current≠latest不是Harness选错答案的证据；归属：Agent决策 / Verifier判断
- 关键步骤：[16, 17, 19, 21, 27, 29, 30]
完整0–30步：16步mask#5 F1=49.43，17步主动切换拆除候选仅0.23；19步核验否定mask#5，21步删后current仅1 FP。末尾29/30生成T1/T2中间掩码均F1=0，未设置current；预算兜底保留current符合语义。若自动选最新同样F1=0且更多FP，不能把mismatch当Bug。
改进与风险：明确候选与当前答案的区别，保留显式切换；禁止“最新即最好”自动兜底。

## case_18 · state_termination
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_s1/T5.1/val/val_47`
- [完整回放](case_18.html#review) / [GitHub可直接阅读的全步骤](case_18.md)；31条步骤记录/30次调用；最终F1=32.63
- 判定：预算内未收尾且核验后退化，组件重编号造成一次失败；归属：Agent决策 / Verifier判断
- 关键步骤：[16, 22, 26, 27, 28, 29, 30]
完整0–30步：大量局部查看后初次清理提升F1 24.71→45.06；26步submit报告引发恢复旧组件与再删除，27步add_region使ID重编号，28步沿用旧ID失败，29步inspect后30步正确映射但已用尽预算。最终32.63。语义编辑决策和编号记忆成本并存，不是remove_region未保留ID。
改进与风险：当前已在错误信息说明重编号，避免重复添加同样规则；显式答案切换可减少身份union，但此例需更好的剩余步数规划，暂不自动延长预算。

## case_19 · state_termination
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_s1/T1.1xC0/val/val_10`
- [完整回放](case_19.html#review) / [GitHub可直接阅读的全步骤](case_19.md)；12条步骤记录/11次调用；最终F1=68.70
- 判定：核验后删减同时减少TP/FP，最终F1净下降；归属：Verifier判断 / Agent决策
- 关键步骤：[6, 7, 8, 9, 10, 11]
完整0–11步：mask#5 TP21340/FP14099；7步no意见被8步批量删除，TP减少5157、FP减少7400，F1 71.52→68.70。9步看最终掩码但未恢复；图像和掩码显示部分真变化被去掉。不能称所有删除错误，也不能只凭减少FP认定核验有效。
改进与风险：以净效果和具体轮廓评估核验，保留候选以便自主恢复；不加GT驱动回滚。

## case_20 · state_termination
- 会话：`/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/json_qwen37flash_g3_t31_any_s1/T3.1/val/train_1032_1to5`
- [完整回放](case_20.html#review) / [GitHub可直接阅读的全步骤](case_20.md)；18条步骤记录/17次调用；最终F1=90.65
- 判定：显式submit成功建立current，标签不是终止异常；归属：Harness状态或接口（设计合法）；前置Agent类型误用
- 关键步骤：[13, 14, 15, 16, 17]
完整0–17步：13步ask无current失败，14步to_mask(mask)类型错误；15步显式提交mask#6被正常接受并建立current，返回固定核验报告，16步移除FP后17步正常提交F1=90.65。submit_without_current只是调用前状态，不代表未提交。T3实例以文字分割替代示例匹配的问题另列且本轮不改。
改进与风险：修正统计解释，保留显式submit合法行为；研究更直接的答案切换以免借submit设状态，不改T3工具。

