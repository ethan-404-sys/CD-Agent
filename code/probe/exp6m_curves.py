"""导出训练曲线 PNG 与审核前端可读取的 curves.json。"""
from __future__ import annotations
import argparse
from pathlib import Path

import exp6m_common as c


def export(run, out):
    config = c.read(run / 'config.json')
    train = c.rows(run / 'train_log.jsonl')
    val = c.rows(run / 'val_log.jsonl') if (run / 'val_log.jsonl').exists() else []
    for records in (train, val):
        steps = [r['step'] for r in records]
        if steps != sorted(set(steps)):
            raise ValueError('日志步数重复或未递增，先恢复对应检查点')
    series = {}
    for key, source, field in [('train_loss', train, 'loss'), ('val_loss', val, 'val_loss'),
                              ('val_ap2', val, 'val_ap2'), ('val_auc2', val, 'val_auc2'), ('lr', train, 'lr')]:
        series[key] = [[r['step'], r[field]] for r in source]
    out = c.local(out)
    out.mkdir(parents=True, exist_ok=True)
    c.write(out / 'curves.json', dict(run=str(run.resolve()), dataset=config['dataset'], series=series))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(9, 10), sharex=True)
    for key, ax, label in [('train_loss', axes[0], 'train loss'), ('val_loss', axes[0], 'validation loss'),
                           ('lr', axes[1], 'learning rate'), ('val_ap2', axes[2], 'validation type-2 AP')]:
        points = [(x, y) for x, y in series[key] if y is not None]
        if points:
            ax.plot(*zip(*points), label=label)
    for ax, title in zip(axes, ['Answer-token cross entropy', 'Learning rate', 'Weighted type-2 AP'], strict=True):
        ax.set_ylabel(title)
        ax.grid(alpha=.25)
        if ax.lines:
            ax.legend()
    axes[-1].set_xlabel('Optimizer step')
    fig.suptitle(f'{config["dataset"]} / {run.name}')
    fig.tight_layout()
    fig.savefig(out / 'curves.png', dpi=160)
    plt.close(fig)
    return dict(train_steps=len(train), evaluations=len(val), output=str(out))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    c.require_slurm()
    print(c.json.dumps(export(a.run, a.out or a.run), ensure_ascii=False))


if __name__ == '__main__':
    main()
