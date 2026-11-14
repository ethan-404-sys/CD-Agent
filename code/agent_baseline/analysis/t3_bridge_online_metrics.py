"""Score fixed agent10 sessions after submission, excluding the visual exemplar."""
import argparse
import json
from pathlib import Path
import numpy as np
from .io_retry import read_text, read_image, write_json, exists
from .t3_routes import pooled, pixel_counts, group, GROUPS


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--tasks', type=Path, required=True)
    args = p.parse_args()
    rows = [json.loads(line) for line in read_text(args.tasks).splitlines() if line.strip()]
    records = []
    for arm in ('baseline','bridge'):
        for seed in (1016,2027,3038):
            for row in rows:
                folder = args.output / 'online' / f'{arm}_s{seed}' / 'T3.1/val' / row['id']
                if not exists(folder / 'final.json'): continue
                final = json.loads(read_text(folder / 'final.json'))
                ex, truth = [read_image(row[k]) > 0 for k in ('exemplar_mask','gt_mask')]
                pred = read_image(folder / final['mask_path']) > 0
                steps = [json.loads(line) for line in read_text(folder / 'steps.jsonl').splitlines() if line.strip()]
                calls = [call for step in steps for call in step.get('calls',[])]
                records.append(dict(id=row['id'], arm=arm, seed=seed, group=group(row),
                    counts=pixel_counts(pred,truth,ex), cost_cny=final.get('cost_cny',0.),
                    submitted_by=final.get('submitted_by'), termination_reason=final.get('termination_reason'),
                    semantic_calls={tool:sum(c.get('tool')==tool for c in calls) for tool in
                        ('interpret_exemplar','rank_semantic_transition','match_exemplar','classify','segment_semantic')},
                    final_path=str(folder / 'final.json')))
    summaries = {}
    for arm in ('baseline','bridge'):
        summaries[arm] = {}
        for seed in (1016,2027,3038):
            selected = [r for r in records if r['arm']==arm and r['seed']==seed]
            summaries[arm][str(seed)] = dict(completed=len(selected), expected=len(rows),
                groups={g:pooled([r for r in selected if g=='全部' or r['group']==g]) for g in ('全部',*GROUPS)})
    write_json(args.output / 'online_summary.json', dict(records=records, summaries=summaries,
        cost_cny=sum(r['cost_cny'] for r in records), complete=len(records)==6*len(rows)))
    bills = list(args.output.glob('*/tasks/*/**/answer.json')) + list(args.output.glob('*/tasks/*/**/failure_*.json'))
    spent = sum(json.loads(read_text(path)).get('cost_cny',0.) for path in bills)
    write_json(args.output / 'metric.json', dict(stage='online', completed=len(records), expected=6*len(rows),
        failed=sum(r['termination_reason']=='error' for r in records), cumulative=True,
        cost_cny=spent + sum(r['cost_cny'] for r in records), result_path=str(args.output/'online_summary.json')))


if __name__ == '__main__': main()
