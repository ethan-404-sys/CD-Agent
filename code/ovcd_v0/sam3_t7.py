"""T7 source "SAM3 text": building instances on T1 and on T2 separately; the B candidates are the
8-connected components of (S1 XOR S2) & ~M0 (round 5, Codex batch62 spec).

  segment (GPU, omniovcd-env): per image and time, SAM3 with the text "building" at resolution 1008
           and the default instance threshold 0.5; saves the instance masks (packed) and scores
           -> RUN/sam3/<split>/<stem>.npz
  queue   (CPU): A = the A items of an existing tag (same uids and masks, so their cached answers
           are reused); B = components of (S1 ^ S2) & ~M0 with area in [50, 20000], ranked by the
           highest SAM3 score of an instance overlapping the component (ties: component id), top
           keep_b, uid = 200000 + component id; copies the reused A answers into the new call cache.

    python sam3_t7.py segment --split val --shard 0 --shards 2
    python sam3_t7.py queue --tag r5 --from-tag r4 --m0 imc --split val
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from common import RUN, components, load_m0, load_rgb, names, read_jsonl, write_json

SAM3_TOOLS = '/guisongxia01/pangchao/wangyihan/Chang-Agent/probe'
OUT = RUN / 'sam3'


def segment(a):
    sys.path.insert(0, SAM3_TOOLS)
    import torch
    from PIL import Image
    import sam3_tools
    proc = sam3_tools.build_processor()
    for name in names(a.split)[a.shard::a.shards]:
        out = OUT / a.split / (Path(name).stem + '.npz')
        if out.exists():
            continue
        t0 = time.time()
        arrays = {}
        for t, im in enumerate(load_rgb(a.split, name)):
            with torch.inference_mode(), sam3_tools.autocast():
                state = proc.set_image(Image.fromarray(im))
                state = proc.set_text_prompt(prompt='building', state=state)
            m = state['masks'][:, 0].cpu().numpy().astype(bool) if len(state['scores']) else np.zeros((0,) + im.shape[:2], bool)
            arrays[f'masks{t}'] = np.packbits(m.reshape(len(m), im.shape[0] * im.shape[1]), axis=1)
            arrays[f'scores{t}'] = state['scores'].float().cpu().numpy()
        arrays['shape'] = np.array(im.shape[:2])
        out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out, **arrays)
        print(name, len(arrays['scores0']), len(arrays['scores1']), f'{time.time() - t0:.1f}s', flush=True)


def read(split, name):
    z = np.load(OUT / split / (Path(name).stem + '.npz'))
    h, w = z['shape']
    inst = []
    for t in (0, 1):
        packed = z[f'masks{t}']
        m = np.unpackbits(packed, axis=1, count=h * w).reshape(-1, h, w).astype(bool) if len(packed) else np.zeros((0, h, w), bool)
        inst.append((m, z[f'scores{t}']))
    return inst


def queue(a):
    import items as I
    rows = []
    for name in names(a.split):
        old = I.Store(a.from_tag, a.split, name)
        m0 = load_m0(a.m0, a.split, name)
        inst = read(a.split, name)
        s1, s2 = (m.any(0) if len(m) else np.zeros(m0.shape, bool) for m, _ in inst)
        lab, comps = components((s1 ^ s2) & ~m0)
        B, store = [], {}
        for c in comps:
            if not a.min_area <= c['area'] <= a.max_area:
                continue
            cm = lab == c['id']
            sc = [float(s) for m, ss in inst for mi, s in zip(m, ss) if (mi & cm).any()]
            B.append(dict(uid=200000 + c['id'], bbox=c['bbox'], area=c['area'], score=max(sc) if sc else 0.0,
                          time='t1' if (cm & s1).sum() >= (cm & s2).sum() else 't2', source='sam3_text'))
            store[200000 + c['id']] = cm
        B.sort(key=lambda it: (-it['score'], it['uid']))
        B = B[:a.keep_b]
        A = old.meta['A']
        for it in A:
            store[it['uid']] = old.mask(it['uid'])
        I.save(a.tag, a.split, name, A, B, {u: store[u] for u in [it['uid'] for it in A + B]}, m0.shape)
        # reuse the cached A answers (identical crop, contour and prompt)
        src = {r['uid']: r for r in read_jsonl(RUN / 'calls' / a.from_tag / a.split / (Path(name).stem + '.jsonl'))}
        dst = RUN / 'calls' / a.tag / a.split / (Path(name).stem + '.jsonl')
        have = {r['uid'] for r in read_jsonl(dst)}
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open('a') as f:
            for it in A:
                if it['uid'] not in have:
                    f.write(json.dumps(src[it['uid']]) + '\n')
        rows.append(dict(name=name, A=len(A), B=len(B), B_pool=len(comps), s1=int(s1.sum()), s2=int(s2.sum())))
        print(rows[-1], flush=True)
    write_json(RUN / 'items' / a.tag / f'{a.split}_counts.json', dict(args=vars(a), rows=rows))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['segment', 'queue'])
    p.add_argument('--split', choices=['val', 'test'], required=True)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--tag', default='r5')
    p.add_argument('--from-tag', default='r4')
    p.add_argument('--m0', default='imc')
    p.add_argument('--min-area', type=int, default=50)
    p.add_argument('--max-area', type=int, default=20000)
    p.add_argument('--keep-b', type=int, default=50)
    a = p.parse_args()
    segment(a) if a.stage == 'segment' else queue(a)
