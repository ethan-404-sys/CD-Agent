"""exp6j 零样本 MLLM 在 exp8s7 过滤后同样本（验证部分）上的 AUC/AP，与 exp8s7 eval 的 exp6j 对齐口径一致：
标签用过滤后单元的 label（1/0，-1 不计），② 子集 = ② 为主的正例 vs 全部负例。不做自助法，只给点估计和样本数。"""
import json, sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score

HERE = Path(__file__).resolve().parent
PRED = HERE.parent / 'batch45/run/predictions/step2'
out = {}
for ds in ('second', 'sysu'):
    z = np.load(HERE / f'run/{ds}/units/val.npz')
    key = {(str(z['images'][i]), int(u)): j for j, (i, u) in enumerate(zip(z['image_id'], z['id']))}
    cid = {}
    for line in open(HERE.parent / f'batch45/run/samples/step2/{ds}.jsonl'):
        r = json.loads(line)
        j = key.get((r['image'], r['unit_id']))
        if j is not None and z['label'][j] >= 0:
            cid[r['cid']] = j
    res = {}
    for folder in sorted((PRED / ds).iterdir()):
        score = {}
        for f in folder.glob('*.jsonl'):
            for line in open(f):
                r = json.loads(line)
                if r.get('p_yes') is not None:
                    score[r['cid']] = r['p_yes']
        common = [c for c in cid if c in score]
        idx = np.array([cid[c] for c in common]); s = np.array([score[c] for c in common])
        y, maj = z['label'][idx], z['majority'][idx]
        row = {}
        for name, keep in (('all', np.ones(len(y), bool)), ('cell2', (y == 0) | (maj == 2))):
            yy, ss = y[keep], s[keep]
            row[name] = dict(positive=int(yy.sum()), negative=int((yy == 0).sum()),
                             auc=float(roc_auc_score(yy, ss)), ap=float(average_precision_score(yy, ss)))
        res[folder.name] = dict(matched=len(common), **row)
        print(ds, folder.name, len(common), {k: (round(v['auc'], 3), round(v['ap'], 3)) for k, v in row.items()}, flush=True)
    out[ds] = res
(HERE / 'run/exp6j_mllm_aligned.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))
