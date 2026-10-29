"""验证选择与晚融合冻结、测试配对整群区间、面积分档和过关线。"""
from __future__ import annotations
import argparse
from pathlib import Path

import exp6m_common as c
import numpy as np


def load_run(path):
    config = c.read(path / 'config.json')
    data = config['sample_order']
    c.validate_manifest(data, config['dataset'], 'test' if config['final'] else 'val')
    predictions = c.rows(path / 'predictions.jsonl')
    mapping = {r['cid']: r for r in predictions}
    if len(mapping) != len(predictions) or set(mapping) != {r['cid'] for r in data}:
        raise ValueError(f'预测覆盖不完整或重复: {path}')
    scores = np.array([mapping[r['cid']]['p_yes'] for r in data], float)
    if not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
        raise ValueError('预测概率无效')
    return config, data, scores


def logit(scores):
    clipped = np.clip(np.asarray(scores, float), 1e-6, 1-1e-6)
    return np.log(clipped) - np.log1p(-clipped)


def transform(scores, kind):
    # 融合分数本身是无界线性组合（非概率），截断取 logit 会把两端压成并列值，故原值标准化。
    return logit(scores) if kind == 'logit' else np.asarray(scores, float)


def normalization(scores, weights, kind='logit'):
    values = transform(scores, kind)
    mean = float(np.average(values, weights=weights))
    std = float(np.sqrt(np.average((values-mean)**2, weights=weights)))
    # 常数模型本就没有排序信息，标准化后为0；不制造除零或虚假信号。
    return dict(mean=mean, std=std if std > 0 else 1., constant=std == 0)


def standardize(scores, norm, kind='logit'):
    return (transform(scores, kind)-norm['mean'])/norm['std']


def primary(rows):
    return np.array([r['stratum'] == 'pos2' or r['label'] == 0 for r in rows])


def select_late(rows, ft, fuse):
    weights = np.array([r['weight'] for r in rows])
    norms = dict(ft=normalization(ft, weights), fuse=normalization(fuse, weights, 'raw'))
    x, y = standardize(ft, norms['ft']), standardize(fuse, norms['fuse'], 'raw')
    grid = []
    e = c.upstream_eval()
    for i in range(11):
        w = i/10
        metrics = e.ranking(rows, w*x+(1-w)*y, n_boot=0)
        grid.append(dict(w=w, val_ap2=metrics['primary_ap'], val_auc2=metrics['primary_auc']))
    winner = max(grid, key=lambda v: (v['val_ap2'], -v['w']))
    return dict(w=winner['w'], normalization=norms, grid=grid,
                rule='微调概率取logit、融合分数取原值，加权验证全集拟合均值/总体标准差；按②AP选w，相同AP优先更小w',
                clip=1e-6, fuse_transform='raw')


def late_score(ft, fuse, selection):
    norm = selection['normalization']
    w = selection['w']
    # 旧冻结文件（无 fuse_transform）沿用当时的 logit 口径，保证已冻结方案可复现。
    kind = selection.get('fuse_transform', 'logit')
    return w*standardize(ft, norm['ft']) + (1-w)*standardize(fuse, norm['fuse'], kind)


def select(runs, out, dry_run=False):
    candidates, shared, dataset = [], None, None
    ratios = set()
    e = c.upstream_eval()
    for path in runs:
        config, rows, scores = load_run(path)
        if config['final'] or any(r['split'] != 'val' for r in rows):
            raise ValueError('选择禁止使用测试数据')
        if shared is None:
            shared, dataset = rows, config['dataset']
        if rows != shared or config['dataset'] != dataset:
            raise ValueError('所有待选方案必须使用同一数据集、同一验证清单和权重')
        training = c.read(Path(config['train_run']) / 'config.json')
        train = training['train_sample_order']
        pos = sum(r['label'] == 1 for r in train)
        ratio = (len(train)-pos)/pos
        if ratio not in (1., 3., 5.):
            raise ValueError(f'{path}: 实际负正比例 {ratio:.6f} 不是1/3/5；先解决采样缺口')
        ratios.add(int(ratio))
        if config['prompt_text'] != training['prompt_text'] or config['question'] != c.QUESTION:
            raise ValueError('预测提示与训练配置不一致')
        info = c.read(Path(config['adapter']) / 'checkpoint.json')
        if Path(info['run']).resolve() != Path(config['train_run']).resolve():
            raise ValueError('预测检查点不属于所声明的训练目录')
        metrics = e.ranking(rows, scores, n_boot=0)
        candidates.append(dict(source=str(path.resolve()), adapter=config['adapter'], ratio=int(ratio),
                               step=info['step'], context_limit=config['context_limit'], batch=config['batch'],
                               val_ap2=metrics['primary_ap'], val_auc2=metrics['primary_auc']))
    if ratios != {1, 3, 5}:
        raise ValueError('验证冻结必须完整比较实际比例1:1/1:3/1:5')
    if any(r['val_ap2'] is None for r in candidates):
        raise ValueError('存在无法计算② AP的候选')
    chosen = min(candidates, key=lambda v: (-v['val_ap2'], v['step'], v['adapter']))
    config, rows, scores = load_run(Path(chosen['source']))
    controls = e.controls(dataset, rows, False, c.FUSE[dataset])
    fusion = select_late(rows, scores, controls[c.FUSE[dataset]])
    if dry_run:
        return dict(dataset=dataset, candidates=candidates, aligned_controls={k: len(v) for k, v in controls.items()},
                    dry_run=True, frozen_written=False)
    result = dict(dataset=dataset, selected=chosen, candidates=candidates, late_fusion=fusion,
                  training_config=c.read(Path(config['train_run']) / 'config.json'),
                  validation_manifest=config['manifest'], validation_sample_order=rows,
                  comparator=c.FUSE[dataset], validation_metrics={
                      'finetuned': e.ranking(rows, scores, n_boot=0),
                      'late_fusion': e.ranking(rows, late_score(scores, controls[c.FUSE[dataset]], fusion), n_boot=0),
                      **{name: e.ranking(rows, values, n_boot=0) for name, values in controls.items()}},
                  rule='仅验证②加权AP选择；同分选更早检查点，然后按路径稳定排序',
                  bootstrap=2000, seed=c.BOOT_SEED,
                  pass_rules=dict(ft_minus_fuse_lower_gt=-.02, late_gain_gt=.02, late_ci_lower_gt=0))
    path = c.local(out)
    if path.exists():
        raise ValueError('冻结文件已存在，不覆盖；选择结束后只运行该方案测试')
    c.write(path, result)
    return dict(dataset=dataset, selected=chosen, late_weight=fusion['w'], frozen=str(path))


def paired_intervals(rows, methods, n_boot=2000):
    """相同按图抽样计算两个配对差，保留每次有效样本数。"""
    e = c.upstream_eval()
    y = np.array([r['label'] for r in rows])
    weights = np.array([r['weight'] for r in rows])
    mask = primary(rows)
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(row['image'], []).append(i)
    members = [np.asarray(v) for v in groups.values()]
    values = {key: np.asarray(value) for key, value in methods.items()}
    compare = ('finetuned', 'late_fusion')
    def measure(idx):
        idx = idx[mask[idx]]
        if not len(idx) or len(np.unique(y[idx])) < 2:
            return None
        return {name: e.c.store.ap(y[idx], s[idx], weights[idx]) for name, s in values.items()}
    point = measure(np.arange(len(rows)))
    boot = {name: [] for name in compare}
    rng = np.random.default_rng(c.BOOT_SEED)
    for _ in range(n_boot):
        idx = np.concatenate([members[g] for g in rng.integers(0, len(members), len(members))])
        value = measure(idx)
        if value is not None:
            for name in compare:
                boot[name].append(value[name]-value['fusion'])
    return {name+'_minus_fusion': dict(value=point[name]-point['fusion'] if point else None,
               ci95=np.quantile(boot[name], [.025, .975]).tolist() if boot[name] else None,
               valid_bootstrap=len(boot[name])) for name in compare}


def pass_lines(differences):
    ft, late = [differences[key] for key in ('finetuned_minus_fusion', 'late_fusion_minus_fusion')]
    first = ft['ci95'] is not None and ft['ci95'][0] > -.02
    second = late['ci95'] is not None and late['value'] > .02 and late['ci95'][0] > 0
    return dict(line1='是' if first else '否', line2='是' if second else '否', useful='是' if first or second else '否',
                rule1='微调−融合的②AP配对区间下限 > −0.02',
                rule2='晚融合−融合的②AP点差 > 0.02 且配对区间下限 > 0')


def area_bins(rows, methods):
    e = c.upstream_eval()
    import exp6l_area_bins as area
    result = {}
    for name, lo, hi in area.BINS:
        ids = [i for i, r in enumerate(rows) if (r['stratum'] == 'pos2' or r['label'] == 0) and lo <= r['novel'] < hi]
        if not ids:
            result[name] = None
            continue
        sub = [rows[i] for i in ids]
        scores = {k: np.asarray(v)[ids] for k, v in methods.items()}
        if len({r['label'] for r in sub}) < 2:
            result[name] = dict(n_pos=sum(r['label'] for r in sub), n_neg=sum(1-r['label'] for r in sub),
                                reference_only=True, reason='该面积档缺少正例或负例，无法计算AUC/配对区间',
                                methods={k: e.ranking(sub, v, n_boot=0) for k, v in scores.items()})
            continue
        # 同 exp6l 的权重、整群抽样和少于10个正例仅供参考口径。
        result[name] = area.bin_stats(sub, scores['finetuned'],
                         {k: v for k, v in scores.items() if k != 'finetuned'}, 2000)
    return result


def final_eval(run, frozen_path, out, dry_run=False):
    if not dry_run and c.local(out).exists():
        raise ValueError('最终测试评估已存在，拒绝覆盖')
    e = c.upstream_eval()
    frozen = c.read(frozen_path)
    config, rows, ft = load_run(run)
    if not config['final'] or config['frozen'] != frozen or config['adapter'] != frozen['selected']['adapter']:
        raise ValueError('测试预测与冻结方案不一致')
    expected = c.rows(c.B49 / 'run' / f'exp6k_test_{config["dataset"]}.json')
    if rows != expected:
        raise ValueError('测试集合改变')
    registry = c.read(c.HERE / 'run/final_registry.json')[config['dataset']]
    if registry != dict(out=str(run.resolve()), config=config):
        raise ValueError('测试预测未按原方案登记')
    controls = e.controls(config['dataset'], rows, True, frozen['comparator'])
    qwen_dir = c.B51 / 'run' / f'final_qwen_{config["dataset"]}'
    # 只读上游日志，不调用上游 load_run（后者带日志末行修复写入）。
    qconf = c.read(qwen_dir / 'config.json')
    if not qconf['final'] or qconf['k'] != 16 or qconf['dataset'] != config['dataset']:
        raise ValueError('exp6l 对照必须为该数据集最终 Qwen k=16')
    predictions = {}
    for path in sorted(qwen_dir.glob('part-*.jsonl')):
        for call in c.rows(path):
            if call['status'] == 'ok':
                for row in call['results']:
                    if row['cid'] in predictions:
                        raise ValueError('exp6l 对照有重复预测')
                    predictions[row['cid']] = row
    if set(predictions) != {r['cid'] for r in rows}:
        raise ValueError('exp6l 对照覆盖不完整')
    qwen, _ = e.effective_scores(predictions, rows)
    fuse = np.array(controls[frozen['comparator']])
    methods = dict(finetuned=ft, late_fusion=late_score(ft, fuse, frozen['late_fusion']),
                   **controls, qwen_k16=qwen)
    if dry_run:
        return dict(dataset=config['dataset'], candidates=len(rows), aligned_methods=list(methods), dry_run=True)
    metrics = {name: e.ranking(rows, values, n_boot=2000) for name, values in methods.items()}
    differences = paired_intervals(rows, dict(finetuned=ft, late_fusion=methods['late_fusion'], fusion=fuse))
    gap = {name: dict(val_ap2=frozen['validation_metrics'][name]['primary_ap'], test_ap2=metrics[name]['primary_ap'],
                     val_minus_test=frozen['validation_metrics'][name]['primary_ap']-metrics[name]['primary_ap'])
           for name in frozen['validation_metrics']}
    inference = c.read(run / 'status.json')
    result = dict(dataset=config['dataset'], source=str(run.resolve()), frozen=str(frozen_path.resolve()),
                  metrics=metrics, paired_differences=differences, pass_lines=pass_lines(differences),
                  area_bins=area_bins(rows, methods), validation_test_gap=gap,
                  seconds_per_candidate=inference['seconds_per_candidate'], bootstrap=2000, seed=c.BOOT_SEED,
                  detector_note=('SECOND train/val 是交叉拟合检测器、test 是官方检测器；候选分布不同。'
                                 '这里用同一验证抽样与测试抽样比较微调、img-s1020和融合的AP落差。'
                                 if config['dataset'] == 'second' else 'SYSU 各划分使用同一官方检测器。'),
                  exp8s7_report_reference=dict(second_img_val=.206, second_img_test=.129,
                         source='CD-Agent/doc/exp6m_qwen_finetune.md；完整池参考，非本次加权样本复算'),
                  gt_note='训练与验证剔除已知审核疑点；最终测试保留exp6k固定1500条与权重以保证同样本对照。')
    path = c.local(out)
    with c.lock(path.with_suffix('.lock')):
        if path.exists():
            raise ValueError('最终测试评估已存在，拒绝覆盖')
        c.write(path, result)
    return dict(dataset=config['dataset'], pass_lines=result['pass_lines'], output=str(path))


def check_controls(dataset, manifest, out):
    """无微调预测时，用真实验证清单检查对照覆盖；不作模型选择。"""
    data = c.rows(manifest)
    c.validate_manifest(data, dataset, 'val')
    controls = c.upstream_eval().controls(dataset, data, False, c.FUSE[dataset])
    result = dict(dataset=dataset, candidates=len(data), dry_run=True, frozen_written=False,
                  aligned_controls={k: len(v) for k, v in controls.items()})
    c.write(out, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='stage', required=True)
    s = sub.add_parser('select')
    s.add_argument('--runs', nargs='+', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True, help='frozen.json')
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('test')
    s.add_argument('--run', type=Path, required=True)
    s.add_argument('--frozen', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('check', help='真实验证清单与现有对照的dry-run，不需要模型预测')
    s.add_argument('--dataset', choices=['sysu', 'second'], required=True)
    s.add_argument('--manifest', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.stage == 'check':
        print(c.json.dumps(check_controls(a.dataset, a.manifest, a.out), ensure_ascii=False))
        return
    if not a.dry_run:
        c.require_slurm()
    if a.stage == 'select':
        result = select(a.runs, a.out, a.dry_run)
    else:
        result = final_eval(a.run, a.frozen, a.out, a.dry_run)
    print(c.json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
