"""Pick test error-analysis samples: best frozen MLLM vs D, on ② positives and fill negatives.
MLLM says yes when p_yes >= 0.5; D's threshold maximises weighted Youden J on the dev set."""
import json, random
from pathlib import Path
from PIL import Image, ImageDraw
B49 = Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch49/run')
RUN = Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch51/run')
OUT = Path('/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch51/erran')
KEEP = ('pos2', 'hard_neg', 'rand_neg')


def d_threshold(dev):
    rows = [r for r in dev if r['stratum'] in KEEP]
    pw = sum(r['weight'] for r in rows if r['label']); nw = sum(r['weight'] for r in rows if not r['label'])
    best = None
    for t in sorted({r['det_mean'] for r in rows}):
        tpr = sum(r['weight'] for r in rows if r['label'] and r['det_mean'] >= t) / pw
        fpr = sum(r['weight'] for r in rows if not r['label'] and r['det_mean'] >= t) / nw
        if best is None or tpr - fpr > best[0]:
            best = (tpr - fpr, t)
    return best[1]


def main():
    summary = {}
    for ds in ('sysu', 'second'):
        dev = json.load(open(B49 / f'exp6k_dev_{ds}.json'))
        test = {r['cid']: r for r in json.load(open(B49 / f'exp6k_test_{ds}.json'))}
        t = d_threshold(dev)
        p = {}
        for line in open(RUN / f'final_qwen_{ds}' / 'part-000.jsonl'):
            g = json.loads(line)
            for cid, res in zip(g['cids'], g['results']):
                p[cid] = res['p_yes']
        rows = [r for r in test.values() if r['stratum'] in KEEP]
        groups = {'mllm_wrong_d_right': [], 'mllm_right_d_wrong': []}
        for r in rows:
            m_ok = (p[r['cid']] >= 0.5) == bool(r['label'])
            d_ok = (r['det_mean'] >= t) == bool(r['label'])
            if m_ok != d_ok:
                groups['mllm_right_d_wrong' if m_ok else 'mllm_wrong_d_right'].append(r)
        rng = random.Random(20261028)
        picked = {}
        for name, items in groups.items():
            items.sort(key=lambda r: r['cid'])
            pos = [r for r in items if r['label']]; neg = [r for r in items if not r['label']]
            npos = min(5, len(pos)); nneg = min(10 - npos, len(neg)); npos = min(10 - nneg, len(pos))
            sel = rng.sample(pos, npos) + rng.sample(neg, nneg)
            picked[name] = [dict(cid=r['cid'], label=r['label'], stratum=r['stratum'], p_yes=round(p[r['cid']], 3),
                                 det_mean=round(r['det_mean'], 3), area=r['area'], t1=r['inputs']['t1'], t2=r['inputs']['t2'])
                            for r in sel]
            sheet = Image.new('RGB', (2 * 260 + 20, 10 * 280), 'white'); dr = ImageDraw.Draw(sheet)
            for i, s in enumerate(picked[name]):
                for j, key in enumerate(('t1', 't2')):
                    im = Image.open(s[key]).convert('RGB'); im.thumbnail((256, 256))
                    sheet.paste(im, (j * 270, i * 280 + 20))
                dr.text((4, i * 280 + 4), f"#{i+1} {s['cid']} label={s['label']} {s['stratum']} p_yes={s['p_yes']} D={s['det_mean']} area={s['area']}", fill='black')
            sheet.save(OUT / f'{ds}_{name}.jpg', quality=88)
        summary[ds] = dict(d_threshold=t, counts={k: dict(total=len(v), pos=sum(r['label'] for r in v)) for k, v in groups.items()}, picked=picked)
    json.dump(summary, open(OUT / 'picked.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: dict(t=v['d_threshold'], counts=v['counts']) for k, v in summary.items()}))


main()
