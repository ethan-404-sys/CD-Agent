"""Generate Chinese tables, two scientific figures, and a summary section."""
from exp8s5_common import *
from exp8s5_budget import plot


def num(v, digits=2):
    return '—' if v is None else f'{v:.{digits}f}'


def pct(v):
    return '—' if v is None else f'{100 * v:.2f}%'


def table(headers, rows):
    return '\n' + '| ' + ' | '.join(headers) + ' |\n|' + '|'.join('---' for _ in headers) + '|\n' + ''.join(
        '| ' + ' | '.join(str(x) for x in row) + ' |\n' for row in rows) + '\n'


def read_scope(a, ds, name):
    row = read(a.out / ds / name)
    if row['limit'] != a.limit:
        raise ValueError(f'{ds}/{name}: report scope mismatch')
    return row


def build(a):
    lines = ['# 实验8补充5：②判定检查、碎片过滤与候选预算\n']
    if a.limit:
        lines.append(f'**真实缓存冒烟草稿：每个数据集、每个划分仅 {a.limit} 张固定图，优先使用含②目标的样本；train_2697 另外单独详查。以下统计不能代表全量结论。**\n')
    else:
        lines.append('完整缓存统计。参数只在验证部分选择，测试部分按冻结方案描述。\n')
    lines.append('候选负责列出有预算的待查清单；补漏、去误检与掩码精修交给后续MLLM。此实验没有执行任何模型。调用耗时以0.44秒/候选估算，不包含调度、输入输出等额外耗时。\n')
    lines.append('对象口径：按GT目标去重，D与候选新增真值并集覆盖GT目标至少50%算命中；SECOND按语义转移分目标。像素口径：候选新增掩码并集覆盖该格全部漏检像素的比例。附加漏检连通域指标保存在JSON。分母为零时显示“—”。\n')
    detail = read(a.out / 'inspection/train_2697.json')
    lines += ['## 1. train_2697 与全量自检\n', detail['diagnosis'] + '。\n']
    lines.append(table(['GT目标/漏检域', '目标/漏检域像素', 'D覆盖率', '棋盘距离', '欧氏距离', '(t1,t2)', '结论'],
        [(f'{r["target"]}/{r["id"]}', f'{r["target_area"]}/{r["area"]}', pct(r['detector_coverage']),
          r['distance'], num(r['distance_euclidean']), r['transition'], r['reason']) for r in detail['targets']]))
    lines.append('语义类别编号见 [详查JSON](../inspection/train_2697.json)。[原分辨率叠加图](../inspection/train_2697-native.png)；[右上角逐像素4倍放大图](../inspection/train_2697-upper-right-4x.png)。棋盘距离d表示最近像素中心相隔d步；中间空格层数为d−1。\n')
    check_table, distances, proportions, relaxed = [], [], [], []
    for ds in ('second', 'sysu'):
        for split in ('train', 'test'):
            report = read_scope(a, ds, f'check_{split}.json')
            t, r = report['counts'], report['ratios']
            check_table.append([ds, split, t['images'], t['cell2_objects'], t['cell2_components'],
                t['cached_cell2_overlapping_objects'], t['cached_cell2_overlap_pixels'],
                t['corrected_cell2_overlapping_objects'], t['changed_pixels']])
            for bucket in ('2', '3', '4_5', 'gt5', 'infinite'):
                label = {'2': '2', '3': '3', '4_5': '4–5', 'gt5': '>5', 'infinite': '图内无D'}[bucket]
                distances.append([ds, split, label, t[f'distance_{bucket}_objects'],
                    t[f'distance_{bucket}_components'], t[f'distance_{bucket}_pixels'], pct(r[f'distance_{bucket}_pixel_fraction'])])
            for prefix in ('before', 'after'):
                for group in CELLS:
                    proportions.append([ds, split, '修正前' if prefix == 'before' else '修正后', group, t[f'{prefix}_cell{group}_pixels'],
                        pct(r[f'{prefix}_cell{group}_fn_fraction']), pct(r[f'{prefix}_cell{group}_gt_fraction'])])
            for prefix in ('after', 'radius3'):
                for group in (1, 2):
                    relaxed.append([ds, split, 1 if prefix == 'after' else 3, group,
                        t[f'{prefix}_cell{group}_objects'], t[f'{prefix}_cell{group}_components'],
                        t[f'{prefix}_cell{group}_pixels'], pct(r[f'{prefix}_cell{group}_fn_fraction']),
                        pct(r[f'{prefix}_cell{group}_gt_fraction'])])
    lines.append(table(['数据集', '划分', '图数', '②GT对象', '②漏检域', '原②所属GT与D重叠数', '原②像素与D重叠', '修正后重叠对象', '变化标签像素'], check_table))
    lines.append('原标签与冻结定义不一致时，按语义GT对象、目标检测覆盖率、漏检连通域大小及到D的距离重建；逐图JSON保留旧格分布与修正格，原缓存不改。重叠检查同时检查②像素和其所属完整GT目标，避免只检查GT减D产生的必然零值。\n')
    lines.append(table(['数据集', '划分', '棋盘距离', 'GT对象数', '漏检域数', '像素数', '占②像素'], distances))
    lines.append('距离分布按漏检域归档，GT目标在每个距离档内去重；同一目标若含多个距离档的漏检域，跨档不可相加。\n')
    lines.append(table(['数据集', '划分', '修正前/后', '格', '像素数', '占FN', '占全部GT'], proportions))
    lines += ['### 邻接从1像素放宽到3像素（仅对照，不改定义）\n',
              table(['数据集', '划分', '膨胀半径', '格', 'GT对象数', '漏检域数', '像素数', '占FN', '占全部GT'], relaxed)]
    lines.append('## 2. 碎片过滤与验证冻结\n')
    lines.append('F0原样；F1新增面积≥100；F2先3×3开运算再要求新增面积≥100；F3新增掩码最大内切圆半径≥2像素。F3旨在去除细线、保留紧凑小区域，不额外裁剪掩码。每个数据集独立选择：②像素和对象覆盖相对F0均损失≤1个百分点，候选数最少；同分按F0/F1/F2/F3顺序。\n')
    filters, selection_summary = [], []
    for ds in ('second', 'sysu'):
        choice = frozen(a, ds)
        for split in ('val', 'test'):
            report = read_scope(a, ds, f'filter_{split}.json')
            for scheme, r in report['filters'].items():
                filters.append([ds, split, scheme + ('（选中）' if scheme == choice['scheme'] else ''),
                    num(r['candidates_per_image']), pct(r['pixel_recall1']), pct(r['pixel_recall2']),
                    pct(r['object_recall2']), r['dropped'], pct(r['dropped_positive_fraction']),
                    pct(r['dropped_any_truth_fraction'])])
            base, selected = report['filters']['F0'], report['filters'][choice['scheme']]
            selection_summary.append([ds, split, choice['scheme'],
                num(base['candidates_per_image'] - selected['candidates_per_image']),
                num(100 * (base['pixel_recall2'] - selected['pixel_recall2'])) if base['pixel_recall2'] is not None else '—',
                num(100 * (base['object_recall2'] - selected['object_recall2'])) if base['object_recall2'] is not None else '—'])
    lines.append(table(['数据集', '划分', '方案', '单元/图', '①像素覆盖', '②像素覆盖', '②对象覆盖', '丢弃数', '丢弃中正例比例', '丢弃中含任意GT比例'], filters))
    lines.append(table(['数据集', '划分', '冻结方案', '每图减少单元', '②像素损失/百分点', '②对象损失/百分点'], selection_summary))
    lines.append('正例按处理后的新增真值精度≥50%定义，含③为主的正例；丢弃比例使用处理前标签。测试的F0–F3全表仅作描述，未重选。审核页沿用原图并在每个数据集的6张固定流程图后追加第3、5步并排对比；冒烟只显示限量图。\n')
    lines.append('## 3. 候选预算曲线\n')
    lines.append('D、M1-ign3使用原单元缓存分数降序；面积按处理后完整面积降序；oracle按处理后新增真值精度降序。随机使用1020/1021/1022三个种子，均值曲线附最小/最大范围。oracle是给定精度排序的参照，并非集合覆盖最优解。所有候选都允许后续MLLM正确精修，所以召回上限按候选覆盖真值并集计算，不只接受预先标为正例的候选。\n')
    lines.append('![k与②对象召回](../figures/exp8s5/budget-k.png)\n\n![实际候选数与②对象召回](../figures/exp8s5/budget-candidates.png)\n')
    targets, comparisons, operating = [], [], []
    for ds in ('second', 'sysu'):
        reports = {split: read_scope(a, ds, f'budget_{split}.json') for split in ('val', 'test')}
        for split, report in reports.items():
            lines.append(f'### {ds.upper()} / {split}\n')
            rows = []
            for method, ks in report['methods'].items():
                for k in KS:
                    r = ks[str(k)]
                    rows.append([method, k, pct(r['pixel_recall2']), pct(r['object_recall2']),
                        pct(r['pixel_recall1']), pct(r['object_recall1']), num(r['positives'], 1),
                        pct(r['positive_fraction']), num(r['positive_per_image']),
                        num(r['candidates_per_image']), num(r['mllm_calls'], 1), num(r['gpu_hours'], 4)])
                if method.startswith('random-') and method != 'random-mean':
                    continue
                for target in (.5, .7):
                    point = report['target_budgets'][method][str(target)]
                    targets.append([ds, split, method, pct(target), point['k'] if point else '未达到',
                        num(point['candidates_per_image']) if point else '—',
                        pct(point['object_recall2']) if point else pct(ks['all']['object_recall2']),
                        num(point['gpu_hours'], 4) if point else '—'])
                    if method in ('D', 'M1-ign3'):
                        diff = []
                        for reference in ('random-mean', 'area', 'oracle'):
                            ref = report['target_budgets'][reference][str(target)]
                            diff.append(num(point['candidates_per_image'] - ref['candidates_per_image']) if point and ref else '不可比较')
                        comparisons.append([ds, split, method, pct(target), *diff])
            lines.append(table(['排序', 'k', '②像素召回', '②对象召回', '①像素召回', '①对象召回',
                                '正例总数', '正例比例', '正例/图', '候选/图', 'MLLM调用数', '估算GPU小时'], rows))
        for method in ('D', 'M1-ign3', 'area', 'random-mean', 'oracle'):
            for target in (.5, .7):
                point = reports['val']['target_budgets'][method][str(target)]
                r = reports['test']['methods'][method][str(point['k'])] if point else None
                operating.append([ds, method, pct(target), point['k'] if point else '验证未达到',
                    pct(r['object_recall2']) if r else '—', num(r['candidates_per_image']) if r else '—',
                    num(r['gpu_hours'], 4) if r else '—'])
    lines += ['### 50% / 70%对象覆盖所需预算\n',
              '取预设k网格中首次达到的点，不在点间插值。测试点属于描述性曲线。未达到时显示全部候选的覆盖上限，GPU小时不虚构。\n',
              table(['数据集', '划分', '排序', '覆盖目标', '所需k', '候选/图', '实际覆盖/全部上限', 'GPU小时'], targets),
              table(['数据集', '划分', '排序', '覆盖目标', '相对随机多用候选/图', '相对面积多用候选/图', '相对oracle多用候选/图'], comparisons),
              '### 验证预算直接应用于测试\n',
              table(['数据集', '排序', '验证覆盖目标', '验证选定k', '测试实际覆盖', '测试候选/图', '测试GPU小时'], operating)]
    lines.append('## 验证与限制\n')
    lines.append('合并检查完整图清单与冒烟范围；预算召回随k单调；全部候选在所有排序下的计数一致，且与独立过滤统计相同；分片随机顺序由图编号确定；页面构建检查所有本地链接。每阶段记录耗时及torch导入检查。正式全量由Slurm运行后才可回答全量统计，冒烟结果不得替代。页面发布由Claude完成；本目录只生成可发布文件。\n')
    folder = local(a.out / 'results')
    folder.mkdir(parents=True, exist_ok=True)
    plot(a)
    (folder / 'exp8s5_checks_budget.md').write_text('\n'.join(lines))
    summary = ['## 实验8补充5\n',
               '**限量冒烟，等待全量Slurm统计。**\n' if a.limit else '完整缓存统计已生成。\n',
               'train_2697核查：' + detail['diagnosis'] + '。\n',
               table(['数据集', '划分', '过滤方案', '减少候选/图', '②像素损失/百分点', '②对象损失/百分点'], selection_summary),
               '[完整表格与预算曲线](exp8s5_checks_budget.md)。本段供追加到主仓库results/summary.md的实验8节后；此次只写batch46。\n']
    (folder / 'summary.md').write_text('\n'.join(summary))


if __name__ == '__main__':
    a = setup(parser(__doc__, ('build',)))
    start = time.monotonic()
    build(a)
    timed(a, 'report', start)
