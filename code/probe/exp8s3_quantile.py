"""exp8s3 额外尝试：按接收比例冻结（不训练，只读已保存的分数与曲线）。

验证上按原规则选阈值（ΔF1 ≥ t 中 ② 像素召回最高）；记下它接收的补漏单元比例 q；
测试上取分数最高的 q 比例单元（阈值 = 测试分数的对应分位），在测试曲线上读一次。
"""
import json
import sys
from pathlib import Path

import numpy as np

RUN = Path(__file__).resolve().parent / 'run'
TARGETS = (-0.3, -0.5, -1.0, -2.0)
JUDGES = ('D', 'M0', 'M1', 'M2', 'M3', 'M1-ign3', 'M1-pF', 'M1-p25')
FIELDS = ('delta_f1', 'delta_iou', 'recall2', 'object_recall2', 'recall1', 'delta_miss_rate', 'added_fp')


def row(curve, i, n_images):
    out = {k: float(curve[k][i]) for k in FIELDS}
    out['added_fp_per_image'] = out.pop('added_fp') / n_images
    return out


def point(curve, threshold):
    # 曲线按阈值降序；取阈值 >= threshold 的最后一行，即接收全部分数 >= threshold 的单元。
    i = np.searchsorted(-curve['threshold'], -threshold, side='right') - 1
    return max(int(i), 0)


def main():
    result = {}
    for ds in ('second', 'sysu'):
        for judge in JUDGES:
            val = np.load(RUN / ds / 'merged' / 'val' / f'{judge}.npz')
            test = np.load(RUN / ds / 'merged' / 'test' / f'{judge}.npz')
            sv = np.sort(np.load(RUN / ds / 'scores' / judge / 'val.npz')['score'])[::-1]
            z = np.load(RUN / ds / 'scores' / judge / 'test.npz')
            st, n_images = np.sort(z['score'])[::-1], len(z['images'])
            n_val_images = len(np.load(RUN / ds / 'scores' / judge / 'val.npz')['images'])
            for t in TARGETS:
                ok = np.flatnonzero(val['delta_f1'] >= t)
                iv = ok[np.argmax(val['recall2'][ok])]
                theta_v = float(val['threshold'][iv])
                q = float((sv >= theta_v).mean())
                k = int(round(q * len(st)))
                theta_t = float(st[k - 1]) if k > 0 else np.inf
                it_q = point(test, theta_t)
                it_s = point(test, theta_v)
                result[f'{ds}/{judge}/{t}'] = dict(
                    dataset=ds, judge=judge, target=t, val_threshold=theta_v, accept_fraction=q,
                    test_threshold_quantile=theta_t,
                    val=row(val, iv, n_val_images),
                    test_score_frozen=row(test, it_s, n_images),
                    test_quantile_frozen=row(test, it_q, n_images))
    (RUN / 'quantile_frozen.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
    lines = ['| 数据集 | 判断器 | 验证 ΔF1 下限 | 接收比例 | 测试 ΔF1（分数冻结 / 比例冻结） | 测试 ② 像素召回（分数 / 比例） | 测试 ② 对象召回（比例） | 新增误检像素/图（比例） |',
             '|---|---|---:|---:|---|---|---:|---:|']
    for v in result.values():
        a, b = v['test_score_frozen'], v['test_quantile_frozen']
        lines.append(f"| {v['dataset']} | {v['judge']} | {v['target']} | {100 * v['accept_fraction']:.2f}% | "
                     f"{a['delta_f1']:.2f} / {b['delta_f1']:.2f} | {a['recall2']:.1f} / {b['recall2']:.1f} | "
                     f"{b['object_recall2']:.1f} | {b['added_fp_per_image']:.0f} |")
    (RUN / 'quantile_frozen.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    sys.exit(main())
