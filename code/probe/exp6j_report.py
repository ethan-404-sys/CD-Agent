"""Generate reviewable experiment tables, DA-RS selection and measured/planning runtime budgets."""
from __future__ import annotations
import argparse
from exp6j_common import *


def formatted(metric,key):
    value = metric.get(key)
    if value is None:
        return '待运行'
    ci = metric.get(key+'_ci95')
    return f'{value:.4f}' + (f' [{ci[0]:.4f}, {ci[1]:.4f}]' if ci else '')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=RUN)
    p.add_argument('--primary-packing',choices=('multi','sheet'),default='multi')
    args = p.parse_args()
    out = local(args.out)
    results = local(HERE/'results')
    results.mkdir(exist_ok=True)
    reports = [read_json(path) for path in sorted((out/'evaluation').glob('*.json'))]
    lines = ['# 实验 6j：遥感 MLLM 零样本变化判断', '',
        '这是由本目录脚本生成的报告；待运行项保留为空，不代表实验已经完成。', '',
        'EarthDial 本地 inference.py 只给出单图入口。config 为 InternVLChatModel + Phi3ForCausalLM、'
        'phi3-chat 模板、448 像素；本地缺少 EarthDial 建模包和模型卡。通过本地 DA-RS 4B 代码适配，'
        '能否成功加载和多图前向由 step0 实测。多图前向成功不能证明训练时使用了多时相输入；'
        '本地文件未证实变化训练任务及官方变化提示。', '',
        '## 第 0 步：加载和输入探测', '',
        '| 模型 | 加载秒 | 输入方式 | 平均秒/样本 | 峰值 GiB | 多图前向 |',
        '|---|---:|---|---:|---:|---|']
    probe_times = {}
    for name in MODEL_PATHS:
        path = out/'step0'/f'{name}.json'
        if not path.exists():
            lines.append(f'| {name} | 待运行 | multi / sheet | 待运行 | 待运行 | 待验证 |')
            continue
        record = read_json(path)
        for packing in ('multi','sheet'):
            runs = [r for r in record['probes'] if r['packing']==packing]
            if not runs:
                continue
            seconds = sum(r['seconds'] for r in runs)/len(runs)
            probe_times[(name,packing)] = seconds
            lines.append(f"| {name} | {record['load_seconds']:.2f} | {packing} | {seconds:.3f} | "
                         f"{max(r['peak_gib'] for r in runs):.2f} | {record.get('multi_image_forward_verified',False)} |")
    lines += ['', '## 第 1 步：exp3 原样本', '',
              '| 数据集 | 模型 | 输入 | 子集 | AUC [95% CI] | AP [95% CI] | yes 比例 |',
              '|---|---|---|---|---|---|---:|']
    selection = {}
    emitted = set()
    for report in reports:
        if report['stage']!='step1':
            continue
        for name,record in report.items():
            if not isinstance(record,dict) or 'subsets' not in record:
                continue
            for subset,m in record['subsets'].items():
                key = (report['dataset'],name,report['packing'],subset)
                if key in emitted:
                    continue
                emitted.add(key)
                lines.append(f"| {report['dataset']} | {name} | {report['packing']} | {subset} | "
                             f"{formatted(m,'auc')} | {formatted(m,'ap')} | {m.get('yes_rate','—')} |")
            if name.startswith('dars') and report['packing']==args.primary_packing:
                selection.setdefault(name,{})[report['dataset']] = record['subsets']['main']['auc']
    complete = all(set(selection.get(name,{}))=={'levir','whu','sysu','second'} for name in ('dars1b','dars2b','dars4b'))
    best = None
    if complete:
        means = {name:sum(values.values())/4 for name,values in selection.items()}
        best = max(means,key=lambda name:(means[name],-int(name[4])))
        write_json(out/'selection.json',dict(best_dars=best,mean_main_auc=means,
            rule='mean of four dataset main AUC; exact tie chooses smaller model',packing=args.primary_packing))
        lines += ['',f'第 2 步 DA-RS 选择：**{best}**，按四数据集 main AUC 均值选择，同分选较小规模。']
    else:
        if not emitted:
            for dataset in ('levir','whu','sysu','second'):
                lines.append(f'| {dataset} | DA-RS 1B/2B/4B、EarthDial、Qwen3.5、qwen3.7-plus、D | 待运行 | 四个子集 | 待运行 | 待运行 | — |')
        lines += ['', 'DA-RS 规模尚未选定：需要 1B、2B、4B 在四个数据集上的完整第 1 步结果。']
    lines += ['', '## 第 2 步：补漏组', '',
              '正例新增像素精度 ≥ 0.5，负例 < 0.1；中间区间不进入二分类评估。按图整群自助法 2000 次，'
              '逆纳入概率加权；难负例从总体按 D 排序最高的 20% 定义。', '',
              '| 数据集 | 分组 | 模型与输入 | AUC [95% CI] | AP [95% CI] | yes 比例 | 两词元质量 | ΔAUC 对 D / M1 |',
              '|---|---|---|---|---|---:|---:|---|']
    fusion_lines = []
    have_step2 = False
    for report in reports:
        if report['stage']!='step2':
            continue
        have_step2 = True
        for group,methods in report['groups'].items():
            for name,m in methods.items():
                lines.append(f"| {report['dataset']} | {group} | {name} | {formatted(m,'auc')} | {formatted(m,'ap')} | "
                    f"{m.get('yes_rate','—')} | {m.get('mass_mean_weighted','—')} | {m.get('delta_auc_D','—')} / {m.get('delta_auc_M1','—')} |")
                if 'fusion' in m:
                    f = m['fusion']
                    fusion_lines.append(f"| {report['dataset']} | {group} | {name} | {f['fold_mean_auc']:.4f} | "
                      f"{f['fold_mean_ap']:.4f} | {f['M1_fold_mean_ap']:.4f} | {f['fold_mean_ap_gain']:+.4f} | {formatted(f,'ap')} |")
    if not have_step2:
        for dataset in ('sysu','second'):
            for group in ('② vs 补漏负例','全部正例 vs 补漏负例','② vs 难负例'):
                lines.append(f'| {dataset} | {group} | 最佳 DA-RS / EarthDial / Qwen × near / context，另加 Qwen sheet | 待运行 | 待运行 | — | — | — |')
    lines += ['', '## 两半交叉融合', '',
              '每数据集按原图随机对半，分别在另一半拟合 P(yes) + M1 逻辑回归；没有测试集选阈值。'
              '主结果为两折指标均值；OOF 区间固定已拟合的分数，不包含重新拟合的不确定性。', '',
              '| 数据集 | 分组 | 模型与输入 | 融合 AUC 均值 | 融合 AP 均值 | M1 AP 均值 | AP 增益 | OOF AP [95% CI] |',
              '|---|---|---|---:|---:|---:|---:|---|', *fusion_lines,
              '', '## 错误分析', '',
              '每数据集、每个对比方向随机选至多 10 例。MLLM 用 P(yes)>0.5；D 阈值在另一验证半集最大化加权 F1。'
              '请以错误图目录的复核状态为准；类别留空时尚不能判断主要错误原因。']
    for path in sorted((results/'exp6j_error_examples').glob('*/summary.json')):
        r=read_json(path)
        lines.append(f"- {path.parent.name}：已分类 {r['classified']}/{r['total']}；类别 {r['category_counts']}；"
                     f"MLLM 判错且 D 判对的已复核例中“太碎看不清”比例：{r['too_small_fraction_among_reviewed_mllm_wrong']}。")
    lines += ['', '## 全量测试计算量', '',
              '按每张原图 k 个候选估算：图数 × k × 实测秒/候选 ÷ 3600；不含加载与排队。'
              '未完成相应输入模式的推理时，使用 step0 近景测时，context 暂乘 2 并标为估算。', '',
              '| 数据集 | 模型与输入 | 计时来源 | k=5 | k=10 | k=20 | k=50 |',
              '|---|---|---|---:|---:|---:|---:|']
    for dataset in ('sysu','second'):
        manifest=read_json(B43/'run'/dataset/'manifest.json')
        n=len(manifest['images']['test'])
        for name in (best or 'dars4b','earthdial','qwen'):
            for view in ('near','context'):
                for packing in ('multi','sheet') if name=='qwen' else (args.primary_packing,):
                    folder=prediction_dir(out,'step2',dataset,name,view,packing)
                    timed=[r['seconds'] for path in folder.glob('part-*.jsonl') for r in read_jsonl(path)]
                    if timed:
                        seconds=sum(timed)/len(timed); source=f'实测 {len(timed)} 例'
                    elif (name,packing) in probe_times:
                        seconds=probe_times[(name,packing)]*(2 if view=='context' else 1); source='step0 估算'
                    else:
                        seconds=4 if view=='context' else 2; source='规划假设，非实测'
                    hours=[n*k*seconds/3600 for k in (5,10,20,50)]
                    cells=[f'{v:.2f}' + (' **>12h**' if v>12 else '') for v in hours]
                    lines.append(f'| {dataset} ({n}图) | {name}/{view}/{packing} | {source} | '+ ' | '.join(cells)+' |')
    lines += ['', '## 当前实验提交预算', '',
              '以下为每个模型/视图跨两个验证数据集的总 GPU 小时；未渲染时按每数据集 2400 条规划。'
              '上下文无实测时取近景的两倍，实测后重新生成本报告。', '',
              '| 模型 | 阶段/输入 | 条数 | 秒/条 | 预计 GPU 小时 | 依据 |',
              '|---|---|---:|---:|---:|---|']
    budgets=[]
    for name in MODEL_PATHS:
        packing='sheet' if name=='qwen' else args.primary_packing
        sec=probe_times.get((name,packing),2.0)
        estimate=2248*sec/3600
        lines.append(f'| {name} | step1/{packing} | 2248 | {sec:.2f} | {estimate:.2f}'+
                     (' **>12h**' if estimate>12 else '')+' | step0 或 2 秒规划假设 |')
        budgets.append(dict(model=name,stage='step1',packing=packing,hours=estimate,over_12h=estimate>12))
    for name in (best or 'dars4b','earthdial','qwen'):
        for view in ('near','context'):
            for packing in ('multi','sheet') if name=='qwen' else (args.primary_packing,):
                count=0; times=[]
                for dataset in ('sysu','second'):
                    path=sample_path(out,'step2',dataset)
                    count+=len(read_jsonl(path)) if path.exists() else 2400
                    folder=prediction_dir(out,'step2',dataset,name,view,packing)
                    times.extend(r['seconds'] for f in folder.glob('part-*.jsonl') for r in read_jsonl(f))
                sec=sum(times)/len(times) if times else probe_times.get((name,packing),2.0)*(2 if view=='context' else 1)
                estimate=count*sec/3600
                source='实际推理平均值' if times else 'step0 外推或规划假设'
                lines.append(f'| {name} | step2/{view}/{packing} | {count} | {sec:.2f} | {estimate:.2f}'+
                             (' **>12h**' if estimate>12 else '')+f' | {source} |')
                budgets.append(dict(model=name,stage='step2',view=view,packing=packing,count=count,
                                    seconds=sec,hours=estimate,over_12h=estimate>12,source=source))
    write_json(out/'runtime_plan.json',budgets)
    lines += ['', '## 待回答的问题', '',
        '1. 第 1 步比较遥感模型与两个 Qwen 对照、三种 DA-RS 规模；EarthDial 的训练因果解释受本地证据限制。',
        '2. 第 2 步主看 cell2_vs_negative 的 AUC/AP 及相对 D、M1 增益，0.05 AUC 是描述性参考。',
        '3. 同模型同样本比较 context 与 near；另检查 Qwen sheet 对照。',
        '4. 以两折 AP 均值之差判断融合增益。',
        '5. 人工复核后汇总错误类别，“太碎看不清”的比例只描述所选分歧样例。',
        '6. 上表按 k 给出全量测试预算，超过 12 GPU 小时的单项已标明。', '',
        '所有新文件均保存在 batch45；主仓库 results/summary.md 未修改。']
    (results/'exp6j_rs_mllm_zero_shot.md').write_text('\n'.join(lines)+'\n')
    (results/'summary.md').write_text('### 6j：遥感 MLLM 零样本变化判断\n\n'
        '完整代码和离线探测见 README_batch45.md；结果见 [实验报告](exp6j_rs_mllm_zero_shot.md)。'
        '这是供主仓库实验 6 节后追加的独立小节；GPU 结果和人工错误分类的完成状态以报告为准。\n')
    print(results/'exp6j_rs_mllm_zero_shot.md')

if __name__ == '__main__':
    main()
