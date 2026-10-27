"""exp8s7 报告图：测试部分预算曲线、冻结点 ΔF1–② 对象召回、验证/测试 ② AP（只读评估 JSON）。"""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

HERE = Path(__file__).resolve().parent
font_manager.fontManager.addfont('/persist_data/home/guisongxia/.local/share/fonts/SimHei.ttf')
plt.rcParams.update({'font.family': 'SimHei', 'axes.unicode_minus': False, 'font.size': 10})
FUSE = {'second': 'fuse-img-a0.75', 'sysu': 'fuse-img-a0.5'}
NAME = {'D': 'D 检测器均值', 'M1-ign3': 'M1-ign3（原）', 'oracle': 'oracle', 'meta-m1': '元数据重训',
        'img': '图像', 'img-meta': '图像+元数据拼接', 'fuse': '晚融合'}
COLOR = {'D': '#888888', 'M1-ign3': '#b07aa1', 'oracle': '#222222', 'meta-m1': '#4e79a7',
         'img': '#e15759', 'img-meta': '#f28e2b', 'fuse': '#59a14f'}
KS = ['1', '2', '3', '5', '10', '20', 'all']
OUT = HERE / 'figures'


def methods(ds):
    return [('D', 'D'), ('M1-ign3', 'M1-ign3'), ('oracle', 'oracle'), ('meta-m1', 'meta-m1-s1020'),
            ('img', 'img-s1020'), ('img-meta', 'img-meta-s1020'), ('fuse', FUSE[ds] + '-s1020')]


def load(ds, split):
    return json.loads((HERE / f'run/{ds}/evaluation/final/{split}.json').read_text())


fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
for ax, ds in zip(axes, ('second', 'sysu')):
    j = load(ds, 'test')
    for key, m in methods(ds):
        b = j['budget'][m]
        x = [b[k]['selected_per_image'] for k in KS if k in b]
        y = [b[k]['object_recall2'] for k in KS if k in b]
        ax.plot(x, y, marker='o', ms=3, color=COLOR[key], ls='--' if key == 'oracle' else '-', label=NAME[key])
    ax.set_xscale('log'); ax.set_xlabel('实际每图选取单元数（k = 1,2,3,5,10,20,全部）'); ax.set_ylabel('② 对象召回上限')
    ax.set_title(f'{ds.upper()} 测试：预算曲线（种子 1020）'); ax.grid(alpha=.3)
axes[0].legend(fontsize=8)
fig.tight_layout(); fig.savefig(OUT / 'budget_test.png', dpi=150)

fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
for ax, ds in zip(axes, ('second', 'sysu')):
    j = load(ds, 'test')
    for key, m in methods(ds):
        if key == 'oracle':
            continue
        p = j['points'][m]
        pts = [p[b] for b in ('0.5', '1.0', '2.0') if b in p]
        ax.plot([q['delta_f1'] for q in pts], [q['object_recall2'] for q in pts], marker='o', color=COLOR[key], label=NAME[key])
    ax.set_xlabel('测试实际 ΔF1（百分点）'); ax.set_ylabel('② 对象召回')
    ax.set_title(f'{ds.upper()} 测试：验证冻结的 ΔF1≥-0.5/-1/-2 三个阈值'); ax.grid(alpha=.3)
axes[0].legend(fontsize=8)
fig.tight_layout(); fig.savefig(OUT / 'frozen_points_test.png', dpi=150)

fig, axes = plt.subplots(1, 2, figsize=(11, 4.0))
for ax, ds in zip(axes, ('second', 'sysu')):
    keys = [k for k, _ in methods(ds) if k != 'oracle']
    vals = {}
    for split in ('val', 'test'):
        j = load(ds, split)
        row = []
        for key, m in methods(ds):
            if key == 'oracle':
                continue
            base = m.rsplit('-s', 1)[0] if m.endswith(('1020',)) else m
            if base in j.get('seed_means', {}):
                row.append(j['seed_means'][base]['ranking']['cell2']['ap'])
            else:
                row.append(j['ranking'][m]['cell2']['ap'])
        vals[split] = row
    x = range(len(keys))
    ax.bar([i - .2 for i in x], vals['val'], .4, label='验证', color='#bbbbbb')
    ax.bar([i + .2 for i in x], vals['test'], .4, label='测试', color=[COLOR[k] for k in keys])
    ax.set_xticks(list(x)); ax.set_xticklabels([NAME[k] for k in keys], rotation=30, ha='right', fontsize=8)
    ax.set_ylabel('② 正例 vs 补漏负例 AP（学习方法为三种子均值）'); ax.set_title(ds.upper()); ax.grid(axis='y', alpha=.3)
axes[0].legend()
fig.tight_layout(); fig.savefig(OUT / 'ap_val_test.png', dpi=150)
print('ok')
