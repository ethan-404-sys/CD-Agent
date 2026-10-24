"""Validate frozen-point reconstruction and render Chinese tables plus standalone PNG figures."""
from exp8s3_common import *
from exp8s3_evaluate import scalar


def fmt(value, digits=3):
    return '—' if value is None or (isinstance(value, float) and not np.isfinite(value)) else f'{value:.{digits}f}' if isinstance(value, (float, np.floating)) else str(value)


def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---']*len(headers)) + ' |',
            *['| ' + ' | '.join(fmt(v) for v in row) + ' |' for row in rows], '']


def percent(n, d):
    return 100*n/d if d else None


def metric_ci(row, metric):
    ci = row[metric + '_ci95']
    return fmt(row[metric], 4) + (f' [{ci[0]:.4f}, {ci[1]:.4f}]' if ci else ' [区间不可用]')


def envelope(curve, grid):
    # Curves can turn back in F1: use the best recall satisfying each F1 floor.
    order = np.argsort(curve['delta_f1'])[::-1]
    xs = curve['delta_f1'][order]
    ys = np.maximum.accumulate(np.nan_to_num(curve['recall2'][order], nan=0.))
    idx = np.searchsorted(-xs, -grid, side='right')-1
    return np.where(idx >= 0, ys[np.maximum(idx, 0)], np.nan)


def report(args):
    config = manifest(args)
    summary = read_json(args.out / args.dataset / 'merged/summary.json')
    frozen = read_json(args.out / args.dataset / 'merged/frozen.json')
    methods = config['methods']
    ranks = {m: read_json(args.out / args.dataset / 'ranking' / (m+'.json')) for m in methods}
    training = {m: read_json(args.out / args.dataset / 'models' / m / 'training.json') for m in methods}
    texts = [f'# 实验 8 补充 3：{args.dataset.upper()} 补漏判断器', '',
             '**这是每划分少量真实图的冒烟输出，不能用于实验结论。**' if args.limit else '训练、验证和测试沿用 exp8 冻结划分；测试曲线只作描述。', '',
             '## 数据与规则', '',
             f'冻结规则：`{json.dumps(config["rule"], ensure_ascii=False, sort_keys=True)}`。', '',
             'U5 单元已经是对象级单元，所有 method 均按自身 r < τ 分到补漏。单元不拆分；面积超过 A_max 仍按自身 r 分流，并单独计数。预过滤丢弃新增像素 < 100 的补漏单元，训练、评分、并入均不再使用。', '',
             '特征维数、来源顺序及划分沿用 V1。SECOND 使用交叉拟合训练/验证检测器和官方测试检测器。训练格标签按 batch34/batch39 定义由缓存真值和预测重建；验证/测试直接读取 batch34 格标签，并逐图核对 batch30 基线。', '',
             '正例率同时报告占全部补漏单元和占参与监督单元的比例。正例格归属按新增真值多数像素，平手取较小格号。② 排序子集为②多数格正例与全部补漏负例。', '']
    rows = []
    counts_record = {}
    for split in SPLITS:
        parts = load_parts(args, split)
        data = assemble(args, split)
        y, majority = data['label'], data['majority']
        pos, neg = int((y == 1).sum()), int((y == 0).sum())
        counts_record[split] = dict(images=len(parts), units=len(y), positive=pos, negative=neg,
            middle=int((y < 0).sum()), positive_cells={str(c):int(((y == 1) & (majority == c)).sum()) for c in CELLS},
            relabeled=sum(int(p['relabeled']) for p in parts),
            dropped=sum(int(p['dropped']) for p in parts), oversized=sum(int(p['oversized']) for p in parts),
            oversized_fill=sum(int(p['oversized_fill']) for p in parts))
        row = counts_record[split]
        rows.append([split, row['images'], len(y), pos, neg, row['middle'], percent(pos, len(y)),
                     percent(pos, pos+neg), *[percent(row['positive_cells'][str(c)], pos) for c in CELLS],
                     row['dropped'], row['oversized'], row['oversized_fill'], row['relabeled']])
    texts += table(['划分','图数','单元','正例','负例','中间','正例/全部%','正例/监督%',
                    '正例①%','正例②%','正例③%','正例⑤%','过滤数','超面积数','超面积补漏数','标签变化数'], rows)
    texts += ['已核对旧 exp8_pool.describe_units：上游标签按完整单元真值占比生成。本实验按任务规定使用新增像素真值占比重新标注；表中“标签变化数”统计保留补漏单元中与原标签不同的数量。原标签只用于连接和核对既有 M0 分数。', '']
    texts += ['## 排序指标', '', '括号为 2000 次按图整群自助法的 95% 区间，随机种子 20261024。空组图也参与抽图；单类重抽次数列在 JSON 中。'
              if not args.limit else '冒烟自助次数以表中为准；单类样本指标为未定义。', '']
    rows = []
    for split in ('val', 'test'):
        for m in methods:
            for group in ('fill', 'cell2'):
                row = ranks[m][split][group]
                rows.append([split,m,'补漏全部' if group == 'fill' else '②正例/补漏负例',row['positive'],row['negative'],
                             metric_ci(row,'auc'),metric_ci(row,'ap'),row['bootstrap_valid'],row['bootstrap_replicates']])
    texts += table(['划分','判断器','分组','正例','负例','AUC [95% CI]','AP [95% CI]','有效重抽','总重抽'], rows)
    texts += ['## 基线、覆盖上限与冻结点', '',
              'ΔF1、ΔIoU、召回和漏检率变化均为百分点。①、②像素召回分母分别为全部对应格漏检像素。②对象按所属 GT 目标去重，最终覆盖率达到 50% 记命中。整体漏检率分母为全部 GT 像素。', '',
              '正例并入上限表示全部 p≥50% 单元的并集覆盖率；它不限制包含中间单元的完整阈值曲线。①可达上限采用全部补漏单元并集。', '']
    texts += table(['划分','基线F1%','基线核对','①漏检像素','②漏检像素','②目标数','①可达上限%','②正例并入上限%','②全部单元上限%'],
       [[s,summary[s]['baseline_f1'],summary[s]['baseline_check'],summary[s]['cell_totals'][0],summary[s]['cell_totals'][1],
         summary[s]['objects2'],summary[s]['cell1_upper_percent'],summary[s]['positive_cell2_upper_percent'],summary[s]['all_cell2_upper_percent']] for s in ('val','test')])
    texts += ['验证在 ΔF1≥−0.3/−0.5/−1/−2 的点中取最高②召回；同召回取最高阈值。`—` 阈值表示不接收任何单元。测试使用同一冻结值，不重新选择。重复冻结阈值逐图只计算一次。', '',
              '新增误检连通域：新增 FP 像素掩码的 8 连通域，报告均值/图；新增 FP/图同时列出，总数见 summary.json。代价为 (−ΔF1)/(②召回百分点)，F1 上升时可为负值，零召回时未定义。', '']
    point_rows = []
    for split in ('val', 'test'):
        perimage = [read_json(args.out / args.dataset / 'points' / split / (Path(n).stem+'.json')) for n in names(args, split)]
        if [p['image'] for p in perimage] != names(args, split):
            raise ValueError('Point inventory mismatch')
        for m in methods:
            for budget in BUDGETS:
                key = str(budget); row = summary[split]['methods'][m][key]
                for stat in ('added_fp','added_tp','recovered2','recovered1','object_hits2'):
                    value = sum(p['methods'][m][key][stat] for p in perimage)
                    if value != row[stat]:
                        raise ValueError(f'{split}/{m}/{key}: histogram and explicit frozen masks disagree for {stat}')
                if any(p['methods'][m][key]['threshold'] != frozen['methods'][m][key]['threshold'] for p in perimage):
                    raise ValueError('Point thresholds differ from the validation freeze')
                row['added_fp_components'] = sum(p['methods'][m][key]['added_fp_components'] for p in perimage)
                row['added_fp_components_per_image'] = row['added_fp_components']/len(perimage)
                row['added_fp_per_image'] = row['added_fp']/len(perimage)
                point_rows.append([split,m,-budget,row['threshold'],row['delta_f1'],row['delta_iou'],row['recall2'],
                       row['object_recall2'],row['recall1'],row['delta_miss_rate'],row['added_fp'],row['added_fp_per_image'],
                       row['added_fp_components_per_image'],row['f1_cost_per_recall2_pp']])
    texts += table(['划分','判断器','验证F1下限','冻结阈值','ΔF1','ΔIoU','②像素%','②对象%','①像素%',
                    '漏检率变化','新增FP','新增FP/图','新增FP域/图','F1代价/②百分点'],point_rows)
    texts += ['## 训练变体与正例数量', '',
              'M1/M2 沿用 V1 的 AdamW、学习率 0.001、权重衰减 0.0001、Dropout 0.15、批量 2048、最多 50 epoch、验证 AP 耐心 7。M2 隐层为 512→256→128→64。', '',
              'M3 固定 31 叶、学习率 0.1、叶内至少 20 样本、L2=1；按外部验证 AP 逐迭代早停，最多 300 次、耐心 20，不使用 sklearn 内部随机验证集。', '',
              '变体只改变训练标签/选择；早停与排序评估统一使用原 p≥50% / p<10% 标签。M1-pF 的 F 使用训练划分全部图的检测器像素 micro F1（冒烟为所选训练图）。忽略③和降低正例门槛均用种子1020。', '',
              '三个种子为1020、1021、1022。同一种子共享正例随机排列，保留数量向上取整；25%是50%的子集，负例全留。归一化与正例权重按实际留下的训练单元重算。', '']
    texts += table(['方法','训练正例','训练负例','正例权重','正例门槛','训练检测器F1','最佳轮次','验证AP'],
       [[m,training[m].get('positive'),training[m].get('negative'),training[m].get('pos_weight'),training[m].get('positive_threshold'),
         training[m].get('train_detector_f1'),training[m].get('best_epoch'),training[m].get('best_val_ap')] for m in methods if m not in ('M0','D','O')])
    qty_rows=[]
    for group in ('M1-25','M1-50','M1'):
        family=[group,group+'-s1021',group+'-s1022']
        values=[ranks[m]['val']['fill'] for m in family]
        for metric in ('auc','ap'):
            numbers=[v[metric] for v in values if v[metric] is not None]
            qty_rows.append([group,metric,np.mean(numbers).item() if numbers else None,
                             min(numbers) if numbers else None,max(numbers) if numbers else None,len(numbers)])
    texts += table(['正例数量组','验证指标','三种子均值','最小','最大','有效种子'],qty_rows)
    texts += ['## 判断问题的数值依据', '']
    val, test = summary['val']['methods'], summary['test']['methods']
    comparison = ['M0','M1','M2','M3','M1-ign3','M1-pF','M1-p25','O']
    texts += table(['方法','验证−0.5点②召回%','测试②召回%','测试ΔF1','测试是否守住−0.5'],
       [[m,val[m]['0.5']['recall2'],test[m]['0.5']['recall2'],test[m]['0.5']['delta_f1'],
         '是' if test[m]['0.5']['delta_f1'] is not None and test[m]['0.5']['delta_f1'] >= -.5-1e-10 else '否'] for m in comparison])
    curves = {s:{m:archive(args.out / args.dataset / 'merged' / s / (m+'.npz')) for m in methods} for s in ('val','test')}
    for s in ('val','test'):
        c=curves[s]['O']; reached=np.flatnonzero(c['recall2'] >= 50.)
        if len(reached):
            i=reached[0]
            texts += [f'{s} oracle 首个达到②像素召回50%的描述点：新增像素精度 {fmt(scalar(c["added_precision"][i]))}%，ΔF1 {fmt(scalar(c["delta_f1"][i]))}；这描述 oracle 排序下并入像素的精度，不等同于分类正例精度。', '']
        else:
            texts += [f'{s} oracle 曲线未达到②像素召回50%，当前补漏候选覆盖不足。', '']
    texts += ['数量增加是否有收益见三种子曲线均值与范围；这些结果不直接证明人造正例的收益，也不单凭单次容量对比确定瓶颈。M1 与 M0、容量及标签变体的差异用上述验证选择后的测试点和独立排序区间描述。', '',
              '## 实现决定与限制', '',
              'M0 的验证/测试分数逐值复用已有 full 分数；上游未保存训练分数，训练分数仅用原冻结 full checkpoint 推理获得，不重训。O 使用真值，只作为参考。', '',
              '对象 t* 是达到50%覆盖的临界分数，即仍能命中的最高接收阈值（下降扫阈值时首次命中）；未覆盖像素不参与排序，不能在阈值降到最低时误计为覆盖。', '',
              '数量图在共同 ΔF1 下限网格上，分别取每种子可行点中最高②召回，再求三种子均值与范围；不对可能折返的原始曲线直接插值。测试对应图仅作描述。', '']
    texts += [str(x) for x in config['limitations']]
    if args.limit:
        texts += ['', f'冒烟选图规则：{config["smoke_selection"]}。实际图名见 manifest.json。没有保存冒烟 checkpoint。']
    texts += ['', '## 每阶段实测资源', '']
    times=[read_json(p) for p in sorted((args.out / args.dataset / 'timing').glob('*.json'))]
    texts += table(['阶段','作业数','累计秒','最慢作业秒','最大RSS MiB'],
        [[stage,len(rows),sum(r['seconds'] for r in rows),max(r['seconds'] for r in rows),max(r['peak_rss_mib'] for r in rows)]
         for stage in sorted({r['stage'] for r in times}) if (rows := [r for r in times if r['stage']==stage])])
    out=args.out / args.dataset
    write_json(out / 'summary.json',dict(config=config,counts=counts_record,ranking=ranks,evaluation=summary,
                                       verification='all frozen pixel/object counts match explicit masks',timing=times))
    (out / 'tables.md').write_text('\n'.join(texts)+'\n')
    plots(args,curves,summary)


def plots(args, curves, summary):
    os.environ['MPLCONFIGDIR'] = str(args.out / '_matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dest=args.out / args.dataset / 'figures'; dest.mkdir(parents=True,exist_ok=True)
    main=list(manifest(args)['methods'])
    variants=['M1','M1-ign3','M1-pF','M1-p25']
    for split in ('val','test'):
        fig,axes=plt.subplots(1,3,figsize=(19,6))
        for ax in axes:
            ax.set_prop_cycle(color=plt.cm.tab20(np.linspace(0,1,len(main))))
        for m in main:
            c=curves[split][m]
            for ax,key in zip(axes,('recall2','recall1','object_recall2')):
                ax.plot(c['delta_f1'],c[key],label=m,linewidth=.9 if '-s' in m else 1.5,alpha=.55 if '-s' in m else .9)
        for ax,title in zip(axes,('Cell 2 pixel recall','Cell 1 pixel recall (all missed pixels)','Cell 2 target coverage >= 50%')):
            ax.set(xlabel='Delta F1 (percentage points)',ylabel='Recall (%)',title=title,ylim=(0,100))
            ax.grid(alpha=.2)
        upper=summary[split]['positive_cell2_upper_percent']
        if upper is not None: axes[0].axhline(upper,color='black',linestyle='--',label='All positive units')
        upper=summary[split]['cell1_upper_percent']
        if upper is not None: axes[1].axhline(upper,color='black',linestyle='--',label='Reachable cell 1')
        axes[0].legend(fontsize=7,ncol=2)
        fig.suptitle(f'{args.dataset.upper()} / {split}' + (' / SMOKE' if args.limit else ''))
        fig.tight_layout();fig.savefig(dest/f'main_{split}.png',dpi=160);plt.close(fig)
        fig,ax=plt.subplots(figsize=(8,6))
        for m in variants:
            c=curves[split][m];ax.plot(c['delta_f1'],c['recall2'],label=m)
        ax.set(xlabel='Delta F1 (percentage points)',ylabel='Cell 2 pixel recall (%)',title=f'{args.dataset.upper()} {split} training variants',ylim=(0,100))
        ax.grid(alpha=.2);ax.legend();fig.tight_layout();fig.savefig(dest/f'variants_{split}.png',dpi=160);plt.close(fig)
        grid=np.linspace(-2,0,201)
        fig,ax=plt.subplots(figsize=(8,6))
        for group,label in (('M1-25','25% positives'),('M1-50','50% positives'),('M1','100% positives')):
            values=np.vstack([envelope(curves[split][m],grid) for m in (group,group+'-s1021',group+'-s1022')])
            line,=ax.plot(grid,values.mean(0),label=label)
            ax.fill_between(grid,values.min(0),values.max(0),color=line.get_color(),alpha=.2)
        ax.set(xlabel='Allowed Delta F1 floor (percentage points)',ylabel='Best feasible cell 2 recall (%)',title=f'{args.dataset.upper()} {split}: 3 seeds mean / range',ylim=(0,100))
        ax.grid(alpha=.2);ax.legend();fig.tight_layout();fig.savefig(dest/f'positives_{split}.png',dpi=160);plt.close(fig)


def main():
    p=parser(__doc__);args=setup(p.parse_args());start=time.monotonic()
    report(args);timing(args,'report',start)

if __name__ == '__main__':
    main()
