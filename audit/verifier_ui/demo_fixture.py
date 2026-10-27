"""Build a synthetic store (fake crops, made-up scores) to try the UI without the server data.

  python demo_fixture.py /tmp/verifier_demo     -> /tmp/verifier_demo/verifier.db
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from store import RunRecorder, icl_template, zero_shot_template

PROMPT_V1 = ('Image 1 and Image 2 are co-registered remote-sensing images of the same area. The red contour marks '
             'the same location in both.\nQuestion: inside the red contour, is there a real land-cover change?\n'
             'Answer with JSON only: {"change": "yes" or "no", "confidence": integer 0-100 for your answer}')
PROMPT_V2 = PROMPT_V1.replace('real land-cover change?', 'real land-cover change? Ignore season, shadows and '
                              'slight misregistration.')


def make_samples(root, dataset, split, n, rng):
    out = []
    for i in range(n):
        label = int(rng.random() < 0.4)
        inputs = {}
        for t in ('t1', 't2'):
            path = root / 'crops' / f'{dataset}_{split}_{i}_{t}.png'
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                base = (90 + 20 * (dataset == 'sysu'), 120, 80)
                im = Image.new('RGB', (128, 128), base)
                d = ImageDraw.Draw(im)
                if t == 't2' and label:
                    d.rectangle((40, 40, 88, 88), fill=(190, 190, 185))
                d.rectangle((32, 32, 96, 96), outline=(230, 30, 30), width=2)
                im.save(path)
            inputs[t] = str(path)
        out.append(dict(dataset=dataset, split=split, cid=f'{dataset}_{split}_{i}', image=f'{dataset}_img_{i // 3}',
                        source=rng.choice(['G0.05', 'H1', 'B']), label=label, inputs=inputs))
    return out


def fake_score(label, skill, rng):
    logit = (1 if label else -1) * skill + 0.8 + rng.gauss(0, 1.2)  # yes-biased like exp3
    return 1 / (1 + math.exp(-logit))


def main():
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    db = root / 'verifier.db'
    db.unlink(missing_ok=True)
    rng = random.Random(0)
    queries = {ds: make_samples(root, ds, 'test', 120, rng) for ds in ('second', 'sysu')}
    pool = {ds: make_samples(root, ds, 'train', 64, rng) for ds in ('second', 'sysu')}
    pool['second'][0] = {**queries['second'][0]}  # one planted leak to show the warning

    def run(name, prompt, prompt_name, mode, demos, skill, batch=1, parent=None):
        template = zero_shot_template(prompt) if mode == 'zero' else icl_template(len(demos), prompt, batch=batch)
        with RunRecorder(db, name=name, model='qwen3.5-4b', prompt_text=prompt, prompt_name=prompt_name,
                         prompt_parent=parent, template=template, demos=demos, mode=mode,
                         strategy='first k of the pool (synthetic)', seed=0, experiment='fixture',
                         readout='forced_prefix_p_yes + greedy_json', batch_size=batch) as rec:
            for ds, qs in queries.items():
                if batch == 1:
                    for q in qs:
                        p = fake_score(q['label'], skill, rng)
                        ans = 'yes' if p > .5 else 'no'
                        rec.record(q, p_yes=p, yes_no_mass=.97, answer=ans, confidence=int(50 + abs(p - .5) * 100),
                                   raw_text=f'{{"change": "{ans}", "confidence": {int(50 + abs(p - .5) * 100)}}}')
                    continue
                for i in range(0, len(qs), batch):
                    chunk = qs[i:i + batch]
                    ps = [fake_score(q['label'], skill, rng) for q in chunk]
                    answers = ['yes' if p > .5 else 'no' for p in ps]
                    raw = '\n'.join(f'{j + 1}. {{"change": "{a}"}}' for j, a in enumerate(answers))
                    rec.record_batch(chunk, raw, [dict(answer=a, raw_text=f'{{"change": "{a}"}}') for a in answers])

    v1 = run('zero_v1', PROMPT_V1, 'general_v1', 'zero', [], 0.6)
    from store import digest
    run('zero_v2', PROMPT_V2, 'general_v2', 'zero', [], 0.75, parent=digest(PROMPT_V1))
    for k in (1, 2, 4, 8):
        run(f'few_k{k}_same', PROMPT_V2, 'general_v2', 'few', pool['second'][:k], 0.75 + 0.12 * math.log2(k + 1))
        run(f'few_k{k}_cross', PROMPT_V2, 'general_v2', 'few', pool['sysu'][:k], 0.75 + 0.05 * math.log2(k + 1))
    for k in (16, 32, 64):
        run(f'many_k{k}_same', PROMPT_V2, 'general_v2', 'many', pool['second'][:k], 1.1 + 0.08 * math.log2(k))
    for k in (16, 32):
        run(f'batched_k{k}_b8', PROMPT_V2, 'general_v2', 'batched_many', pool['second'][:k], 1.0 + 0.06 * math.log2(k), batch=8)
    print(db)


if __name__ == '__main__':
    main()
