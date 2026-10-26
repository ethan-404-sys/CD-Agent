"""Save both directions of MLLM/D disagreement; preserve human cause annotations."""
from __future__ import annotations
import argparse
import csv
import html
import random
from collections import Counter
from exp6j_common import *

CATEGORIES = ('认错地物','季节或色调','配准错位','真值可疑','太碎看不清')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',choices=MODEL_PATHS,required=True)
    p.add_argument('--view',choices=('near','context'),default='context')
    p.add_argument('--packing',choices=('multi','sheet'),default='multi')
    p.add_argument('--datasets',nargs='+',choices=('sysu','second'),default=['sysu','second'])
    p.add_argument('--out',type=Path,default=RUN)
    p.add_argument('--annotations',type=Path,help='Reviewed CSV with dataset,cid,category,explanation')
    args = p.parse_args()
    args.out = local(args.out)
    from PIL import Image,ImageDraw
    reviewed = {}
    if args.annotations:
        with args.annotations.open(newline='') as f:
            for r in csv.DictReader(f):
                if not r['category'].strip():
                    continue
                if r['category'] not in CATEGORIES or not r['explanation'].strip():
                    raise ValueError('Every reviewed row requires one allowed category and a sentence')
                key = (r['dataset'],r['cid'])
                if key in reviewed:
                    raise ValueError(f'Duplicate annotation: {key}')
                reviewed[key] = r
    tag = f'{args.model}_{args.view}_{args.packing}'
    dest = local(HERE / 'results/exp6j_error_examples' / tag)
    dest.mkdir(parents=True,exist_ok=True)
    existing = indexed(read_jsonl(dest/'index.jsonl')) if (dest/'index.jsonl').exists() else {}
    all_records, availability = [], {}
    for dataset in args.datasets:
        rows = indexed(samples(args.out,'step2',dataset))
        decisions = read_jsonl(args.out/'evaluation/decisions'/dataset/f'{tag}.jsonl')
        if set(indexed(decisions)) != set(rows):
            raise ValueError('Error decisions do not cover the exact sample set')
        for direction in ('MLLM_right_D_wrong','MLLM_wrong_D_right'):
            pool = [r for r in decisions if r['mllm_correct'] != r['detector_correct'] and
                    r['mllm_correct'] == (direction=='MLLM_right_D_wrong')]
            chosen = random.Random(SEED).sample(sorted(pool,key=lambda r:r['cid']),min(10,len(pool)))
            availability[f'{dataset}/{direction}'] = dict(available=len(pool),selected=len(chosen))
            for r in chosen:
                row = rows[r['cid']]
                images = input_images(row,'context','multi')
                canvas = Image.new('RGB',(384*2+16,2*(384+24)+64),'white')
                draw = ImageDraw.Draw(canvas)
                draw.text((4,4),f"{r['cid']} GT={r['label']} P(yes)={r['p_yes']:.4f} D={r['D']:.4f}",fill='black')
                draw.text((4,22),direction+'; red=novel pixels; cyan=D',fill='black')
                for i,im in enumerate(images):
                    x,y = (i%2)*400,48+(i//2)*408
                    draw.text((x+4,y+4),FRAMING[i],fill='black')
                    canvas.paste(im,(x,y+24))
                name = f"{dataset}_{Path(row['image']).stem}_{row['unit_id']}.png"
                canvas.save(dest/name)
                previous = existing.get(row['cid'],{})
                annotation = reviewed.get((dataset,row['cid']),previous)
                explanation = annotation.get('explanation') or (
                    f"该单元真值为{'变化' if r['label'] else '无变化'}，MLLM 判断"
                    f"{'正确' if r['mllm_correct'] else '错误'}，检测器按另一验证半集所选阈值判断"
                    f"{'正确' if r['detector_correct'] else '错误'}；具体原因待看图复核。")
                all_records.append(dict(dataset=dataset,cid=row['cid'],direction=direction,
                    category=annotation.get('category',''),explanation=explanation,panel=name,
                    p_yes=r['p_yes'],D=r['D'],label=r['label']))
    if set(reviewed)-{(r['dataset'],r['cid']) for r in all_records}:
        raise ValueError('Annotations include units outside the selected error panels')
    # Preserve annotations for datasets not requested in this invocation.
    all_records.extend(r for r in existing.values() if r['dataset'] not in args.datasets)
    write_jsonl(dest/'index.jsonl',all_records)
    with (dest/'annotations.csv').open('w',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=('dataset','cid','category','explanation'))
        writer.writeheader()
        writer.writerows({k:r[k] for k in writer.fieldnames} for r in all_records)
    counts = Counter(r['category'] for r in all_records if r['category'])
    wrong = [r for r in all_records if r['direction']=='MLLM_wrong_D_right']
    classified_wrong = [r for r in wrong if r['category']]
    write_json(dest/'summary.json',dict(availability=availability,total=len(all_records),
        classified=sum(counts.values()),category_counts=dict(counts),
        mllm_wrong_examples=len(wrong),classified_mllm_wrong=len(classified_wrong),
        too_small_fraction_among_reviewed_mllm_wrong=(sum(r['category']=='太碎看不清' for r in classified_wrong)/len(classified_wrong))
        if classified_wrong else None,
        scope='Balanced disagreement examples, not a population estimate of all model errors'))
    body = ['<!doctype html><meta charset="utf-8"><title>6j error review</title>',
            '<h1>6j 错误复核</h1><p>分类：'+ ' / '.join(CATEGORIES) +
            '。填写 annotations.csv 后用 --annotations 导入；空白表示尚未复核。</p>']
    for r in all_records:
        body.append(f'<h2>{html.escape(r["cid"])} — {html.escape(r["category"] or "待复核")}</h2>'
                    f'<p>{html.escape(r["explanation"])}</p><img width="800" src="{r["panel"]}">')
    (dest/'index.html').write_text('\n'.join(body))
    print(dest)

if __name__ == '__main__':
    main()
