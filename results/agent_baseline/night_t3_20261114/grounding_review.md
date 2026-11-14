# 12例区域指代审查

由主Agent看图审查，不是独立人工标注准确率。先保存影像观察，再读预测，最后打开ID/条件映射；case01 A/t1提前读到预测，已披露。输入图数量可识别处理条件，且历史影像可能熟悉，不称完全盲法。确认例只作机制诊断，不用于调下一方案参数。

| 例 | 任务 | 划分 | 结论 |
|---|---|---|---|
| [case_01](grounding_blind_v1/case_01/review_contact.jpg) | train_2571_4to5 | tune | 两条件均定位到池塘末端和后期屋顶，water→building与可见影像相容；本例A/t1预测先于看图，不能称预测盲审。 |
| [case_02](grounding_blind_v1/case_02/review_contact.jpg) | train_1129_4to5 | confirm | 两条件均识别细条和后期屋顶；T1低植被/狭窄水体的视觉区分不可靠，不据类别标签把它机械判错。 |
| [case_03](grounding_blind_v1/case_03/review_contact.jpg) | train_2822_1to2 | tune | 两条件两期均描述中央大型工厂屋顶，而二值支持只在上边缘。这是可核查的区域错指，不依赖GT类别。 |
| [case_04](grounding_blind_v1/case_04/review_contact.jpg) | train_2828_1to5 | confirm | 支持为极细屋顶边缘；两期都似屋顶或紧邻屋顶结构。回复泛化为大建筑，定位证据不充分；变化方向保留歧义。 |
| [case_05](grounding_blind_v1/case_05/review_contact.jpg) | train_127_3to2 | tune | A即B2在T2识别裸露地面，B即B1把粗糙地面说成矩形屋顶；B1有明确视觉误读。T1乔木/低植被细分类仍不确定。 |
| [case_06](grounding_blind_v1/case_06/review_contact.jpg) | train_1136_5to1 | tune | 两条件均正确描述T形屋顶与后期绿地，支持对应明确。此例成功不能代表全部地表类。 |
| [case_07](grounding_blind_v1/case_07/review_contact.jpg) | train_2130_2to4 | confirm | B即B1描述右下方波浪/中心道路，实际支持在左下角，并错误引入SAR/backscatter解释；区域错指与影像模态幻觉。A即B2的T1位置更合理，T2水体分类可疑但本审查不强行改判。 |
| [case_08](grounding_blind_v1/case_08/review_contact.jpg) | train_2227_3to2 | confirm | 两条件T2均说低植被，图上更像清理后粗糙地面；受分辨率/季相影响，列为语义疑义，不据此宣称确定错误。 |
| [case_09](grounding_blind_v1/case_09/review_contact.jpg) | train_1538_1to3 | tune | 两条件T2均描述大厂房屋顶，支持实际为细长绿化带/树冠。这是明确的区域错指；T1裸地/硬化地面/窄棚仍有歧义。 |
| [case_10](grounding_blind_v1/case_10/review_contact.jpg) | train_2792_1to5 | confirm | 回复描述整排建筑，支持只是一条极细边线；建筑类别可能相容，但不能确认新增变化或精确指代正确。 |
| [case_11](grounding_blind_v1/case_11/review_contact.jpg) | train_2912_5to2 | tune | 两期可见屋顶，类别building在视觉上相容；B2的T2文字转向邻近灰/蓝大屋顶，理由定位不足。解盲后原转换为building→non-vegetated，不自动改GT，标记影像/标签不一致待复核。 |
| [case_12](grounding_blind_v1/case_12/review_contact.jpg) | train_23_2to1 | confirm | 两条件把支持中的开放铺地和后期树冠说成屋顶/空调设备，属于语义误读；后期支持本身混合树木与铺地，不能强行给独立单类真值。 |

明确区域错指见case03两条件、case07的B1、case09两条件；case05的B1及case12两条件存在可见地物误读。边缘碎片、地表细分类和case11标签冲突保留疑义，不为迎合自动标签改写视觉判断。全60的13/5/7标签对吻合只能作另一个诊断指标。

显示/坐标机制不在本轮修改范围；支持图与实际送入PNG已保留。优先转向不依赖VLM类别正确性的固定候选特征消融，同时保留B0文本提议作上限/失败来源诊断。
