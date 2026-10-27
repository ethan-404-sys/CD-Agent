"""中文表格草稿、原始汇总JSON及标准绘图库输出。缺项明确标记待完成。"""
from exp8s8_common import *
import time


def number(v, percent=False):
    return '无有效分母' if v is None else f'{100*v:.2f}%' if percent else f'{v:.4f}'


def report(a):
    result = dict(partial=bool(a.limit), limit=a.limit, cells={}, filter={}, rank={}, icl={}, missing=[])
    for ds, split in SCOPES:
        for stage in ('cells', 'filter', 'rank'):
            path = a.out / ds / f'{stage}_{split}.json'
            if path.exists():
                doc = read(path)
                if doc['limit'] != a.limit:
                    raise ValueError('报告混入不同范围结果')
                result[stage][f'{ds}/{split}'] = doc
            else:
                result['missing'].append(str(path.relative_to(a.out)))
    for ds in ('levir', 'whu'):
        result['icl'][ds] = {}
        for kind in ('selection', 'icl_split', 'dev_sampling', 'test_sampling', 'database_receipt'):
            path = a.out / ds / f'{kind}.json'
            if path.exists():
                result['icl'][ds][kind] = read(path)
        for kind in ('selection', 'icl_split', 'test_sampling', 'database_receipt'):
            if kind not in result['icl'][ds]:
                result['missing'].append(f'{ds}/{kind}.json')
        if ds=='levir' and 'dev_sampling' not in result['icl'][ds]:
            result['missing'].append('levir/dev_sampling.json')
    verification = a.out / 'verification.json'
    if verification.exists():
        result['verification'] = read(verification)
    else:
        result['missing'].append('verification.json')
    stats = a.out / 'icl_stats.json'
    if stats.exists():
        result['icl_stats'] = read(stats)
    if (a.out / 'frozen.json').exists():
        result['frozen'] = read(a.out / 'frozen.json')
    references = {}
    for ds in ('second', 'sysu'):
        path = HERE.parent / f'batch48/run/{ds}/evaluation/final/test.json'
        if path.exists():
            doc = read(path)
            references[ds] = dict(source=str(path), D=doc['ranking']['D']['cell2'])
    result['references'] = references
    lines = ['# 实验8补充8：LEVIR / WHU 补漏流程', '',
             f"状态：{'真实小样本冒烟，不能作为全量研究结果' if a.limit else '完整划分结果草稿'}。",
             '所有比例按像素/对象总数汇总；分母为零显示“无有效分母”。', '',
             '## 四类漏检', '', '| 数据 | 格 | 漏检像素 | 占全部FN | 连通域 | 占全部FN连通域 | 真值目标 | 占有FN目标 |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for scope, d in result['cells'].items():
        for g in ('1', '2', '3', '5', '2far'):
            row = d['groups'][g]
            lines.append(f"| {scope} | {g} | {d[g+'_pixels']} | {number(row['pixel_share'],True)} | {d[g+'_components']} | {number(row['component_share'],True)} | {d[g+'_objects']} | {number(row['object_share'],True)} |")
    lines += ['', '2far：棋盘距离>5的②，无检测器前景也计为远。目标按格内去重，同一目标可含多个格，目标占比不保证相加为1。', '',
              '| 数据 | 补漏/精修单元 | ①+②像素覆盖 | 全部③误分像素 | ③≥30%误分连通域/像素 |', '|---|---:|---:|---:|---:|']
    for scope, d in result['cells'].items():
        g, b = d['groups']['3'], d['groups']['3b']
        lines.append(f"| {scope} | {d['fill_units']}/{d['refine_units']} | {number(d['coverage12'],True)} | {number(g['routed_fill_pixel_fraction'],True)} | {number(b['routed_fill_component_fraction'],True)}/{number(b['routed_fill_pixel_fraction'],True)} |")
    lines += ['', '③误分按覆盖最多候选决定整个漏检连通域去向；像素覆盖按补漏候选并集，两者口径不同。', '',
              '## 过滤', '', '| 数据 | 方案 | 单元/图 | ②对象覆盖 | 距离>5的②覆盖 |', '|---|---|---:|---:|---:|']
    for scope, d in result['filter'].items():
        rows = dict(d['schemes'])
        rows.update({f"敏感性 {g}m A={v['area_threshold']}": v for g, v in d['sensitivity'].items()})
        for name, r in rows.items():
            lines.append(f"| {scope} | {name} | {r['candidates_per_image']:.3f} | {number(r['object_recall2'],True)} | {number(r['object_recall2_far'],True)} |")
    lines += ['', f"LEVIR val 冻结方案：{result.get('frozen',{}).get('scheme','待执行')}。测试全方案表仅作描述。",
              'WHU主方案沿用像素阈值。敏感性面积=ceil(A×(0.5/gsd)²)，仅换算面积，gsd为0.2/0.3米假设，未由本地PNG证实。', '',
              '## 排序与预算', '', '| 数据 | 方法 | 子集 | AUC（95%区间） | AP（95%区间） | 正/负 |', '|---|---|---|---:|---:|---:|']
    for scope, d in result['rank'].items():
        for method, groups in d['ranking'].items():
            for name, row in groups.items():
                display = []
                for metric in ('auc','ap'):
                    ci = row[metric+'_ci95']
                    display.append(number(row[metric])+(f' [{ci[0]:.4f}, {ci[1]:.4f}]' if ci else ''))
                lines.append(f"| {scope} | {method} | {name} | {display[0]} | {display[1]} | {row['positive']}/{row['negative']} |")
    lines += ['', 'cell2=②正例vs全部补漏负例；hard=②正例vs当前评估图D最高20%负例。按图整群自助法2000次、种子20261026（小冒烟可减少次数）。', '',
              '| 数据 | 方法 | k | ②对象召回 | 单元/图 |', '|---|---|---:|---:|---:|']
    for scope, d in result['rank'].items():
        for method, group in d['budget'].items():
            for k, row in group.items():
                lines.append(f"| {scope} | {method} | {k} | {number(row['object_recall2'],True)} | {number(row['selected_per_image'])} |")
    lines += ['', '| 历史参照 | D ②AUC | D ②AP |', '|---|---:|---:|']
    for ds, r in references.items():
        lines.append(f"| {ds} | {number(r['D']['auc'])} | {number(r['D']['ap'])} |")
    lines += ['', '跨数据集数值仅描述比较。是否明显提高需结合完整结果及样本构成；冒烟不能支持该结论。', '', '## ICL材料', '']
    if (a.out / 'icl_stats.md').exists():
        lines += (a.out / 'icl_stats.md').read_text().splitlines()[1:]
    for ds, items in result['icl'].items():
        split = items.get('icl_split')
        if split:
            lines += ['', f"{ds}：示例图{len(split['demo_images'])}张，邻接排除{len(split['neighbor_images'])}张，剩余{len(split['remaining_images'])}张；{split['spatial_note']}；负例定稿={split['finalized']}。"]
        selection = items.get('selection', {})
        if ds=='levir' and 'selection_policy' in selection:
            lines += ['', selection['selection_policy'], '', '改动理由：'+selection['selection_reason']]
            dev = selection['dev_counts']
            lines += ['', f"按当前示例图划分，开发集实际候选{dev['candidates']}个："
                      f"②正例{dev['positive_cell2']}个，其他正例{dev['positive_other']}个，"
                      f"正例合计{dev['positive']}个，负例{dev['negative']}个。"]
            sampling = items.get('dev_sampling')
            if sampling and split and (sampling['demo_images'] != split['demo_images'] or
                             sampling['sampled_candidates'] != dev['candidates']):
                result['missing'].append('LEVIR示例选择已改变，需重跑sample及后续材料阶段')
    lines += ['', '正例理由规则：WHU用两期建筑真值；LEVIR的B来源用现成SAM3建筑语义作推断。新增区域超过一半支持新建/拆除才写方向；其他写“有变化”。G来源且无两期真值也写“有变化”。负例由看图标注，空白未审核项不能作为已完成标注。',
              '', '前端默认demo_pool来自train，因此LEVIR val与WHU test示例都会显示wrong_split；真实来源保留，说明写入meta/note。图像互斥另行校验。', '', '## 完成状态与限制', '']
    if result['missing']:
        lines += ['以下阶段尚未生成，不能计为完成：'] + [f'- `{x}`' for x in result['missing']]
    if a.limit:
        lines += ['', '正式Slurm计算未执行：当前会话创建Slurm连接被拒绝。共享审核库写入未执行：目标不在当前可写范围。冒烟数据库仅用于验证接口。']
    lines += ['', '定义与出处见[consult/ANSWER_defs.md](../consult/ANSWER_defs.md)；执行说明见[README_batch50.md](../README_batch50.md)。', '']
    result['complete'] = (not a.limit and not result['missing']
                          and result.get('verification',{}).get('status')=='passed'
                          and all(result['icl'][d].get('icl_split',{}).get('finalized',False)
                                  and result['icl'][d].get('database_receipt',{}).get('shared_database',False)
                                  for d in ('levir','whu')))
    write(a.out / 'exp8s8_levir_whu_pipeline.json', result)
    local(a.out / 'exp8s8_levir_whu_pipeline.md').write_text('\n'.join(lines))
    figures(a, result)


def figures(a, result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    folder = local(a.out / 'figures')
    folder.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5))
    for scope, d in result['filter'].items():
        rows = d['schemes']
        points = [(r['candidates_per_image'], r['object_recall2'], s) for s, r in rows.items() if r['object_recall2'] is not None]
        if points:
            ax.scatter([p[0] for p in points], [p[1] for p in points], label=scope)
            for x, y, s in points: ax.annotate(s, (x,y), fontsize=7)
    ax.set(xlabel='Units per image', ylabel='Cell-2 object coverage', title='Filter comparison'+(' (SMOKE)' if a.limit else ''))
    if ax.collections: ax.legend()
    fig.tight_layout()
    fig.savefig(folder / 'filter_coverage_units.png', dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(15,4), squeeze=False)
    for ax, (ds, split) in zip(axes[0], SCOPES):
        doc = result['rank'].get(f'{ds}/{split}')
        if doc:
            for method, rows in doc['budget'].items():
                values = [rows[str(k)]['object_recall2'] for k in KS]
                if any(v is not None for v in values):
                    ax.plot(range(len(KS)), [np.nan if v is None else v for v in values], marker='o', label=method)
            if ax.lines:
                ax.legend()
            else:
                ax.text(.5,.5,'No cell-2 targets\nin evaluation images',ha='center',transform=ax.transAxes)
        else:
            ax.text(.5,.5,'Pending',ha='center',transform=ax.transAxes)
        ax.set(title=f'{ds}/{split}'+(' SMOKE' if a.limit else ''), xlabel='Per-image budget', ylabel='Cell-2 object recall')
        ax.set_xticks(range(len(KS)), [str(k) for k in KS])
        ax.set_ylim(0,1)
    fig.tight_layout()
    fig.savefig(folder / 'budget_curves.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    a = setup(parser(__doc__, ('run',)))
    started = time.monotonic()
    report(a)
    finish(a, 'report', started)
