"""Read existing metadata and calculate token, CNY and GPU-hour budgets; no inference."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sqlite3
import statistics as st

from tokenizers import Tokenizer
from exp6l_register_prompts import HERE, B45, B49, MODELS, module_at, read

DATASETS = ('sysu', 'second')


def text_tokens(text):
    """Budget assumption: four ASCII characters or one non-ASCII character per token."""
    return math.ceil(sum(0.25 if ord(c) < 128 else 1 for c in text))


def price(n):
    return (1, 10) if n <= 32768 else (1.5, 15) if n <= 131072 else (3, 30)


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def main():
    materials = {r['name']: r['text'] for r in read(HERE / 'prompt_materials.json')}
    store = module_at('exp6l_budget_store', B49 / 'audit/verifier_ui/store.py')
    evidence = {'datasets': {}, 'timings': {}, 'local_lengths': {}}
    with sqlite3.connect(f'{(B49 / "run/verifier.db").as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for ds in DATASETS:
            system = (B49 / 'prompts' / f'{ds}_sys_v1_en.txt').read_text().strip()
            demos = db.execute('SELECT s.*, r.status, i.stratum FROM sample s '
                'JOIN sample_set_item i ON s.id=i.sample_id JOIN sample_set ss ON ss.id=i.set_id '
                'LEFT JOIN sample_review r ON s.id=r.sample_id WHERE ss.name=?',
                (f'exp6k_demos256_{ds}',)).fetchall()
            passed = [dict(r) for r in demos if r['status'] == 'pass']
            semantics, answers = [], []
            for r in passed:
                meta = json.loads(r['meta'] or '{}')
                semantic = store.semantic_line(meta['semantic_t1'], meta['semantic_t2'], meta.get('semantic_full')) if ds == 'second' else ''
                semantics.append(semantic or '')
                answers.append(('yes' if r['label'] else 'no') + '. ' + (r['rationale'] or ''))
            full = 66 if ds == 'sysu' else 258
            # Common fixed text allowance includes framing, short question, role separators.
            answer_budget = math.ceil(st.mean(text_tokens(s) for s in answers))
            semantic_budget = math.ceil(st.mean(text_tokens(s) for s in semantics))
            demo = {'A': 292 + 48 + answer_budget,
                    'B': (3 if ds == 'sysu' else 5) * full + 48 + answer_budget + semantic_budget}
            demo['AB'] = demo['B'] + 292 + 12
            item = dict(system_chars=len(system), system_tokens=text_tokens(system),
                        system_raw_chars=len((B49 / 'prompts' / f'{ds}_sys_v1_en.txt').read_text()),
                        passed=len(passed), rejected=sum(r['status'] == 'reject' for r in demos),
                        unreviewed=sum(r['status'] is None for r in demos),
                        answer_chars_mean=st.mean(map(len, answers)), answer_tokens=answer_budget,
                        semantic_chars_mean=st.mean(map(len, semantics)), semantic_tokens=semantic_budget,
                        demo_tokens=demo, query_tokens=292 + 2 * full + 64, full_pair=2 * full)
            for split in ('dev', 'test'):
                rows = read(B49 / 'run' / f'exp6k_{split}_{ds}.json')
                groups = read(B49 / 'run' / f'exp6k_{split}_{ds}_images.json')
                assert len(rows) == (600 if split == 'dev' else 1500)
                assert {r['cid'] for r in rows} == {cid for g in groups.values() for cid in g['cids']}
                assert len({r['image'] for r in rows}) == len(groups)
                item[split] = dict(samples=len(rows), images=len(groups),
                    selected_max=max(len(g['cids']) for g in groups.values()),
                    all_candidates=sum(g['all_candidates'] for g in groups.values()),
                    all_max=max(g['all_candidates'] for g in groups.values()),
                    eligible_candidates=sum(g['eligible_candidates'] for g in groups.values()))
            evidence['datasets'][ds] = item

    costs = []

    def add(plan, name, ds, k=0, repeats=1, form='A', split='dev', batch=None, z0=False):
        d = evidence['datasets'][ds]
        system = text_tokens(materials['exp6j_sysu_second_step2_near']) if z0 else d['system_tokens']
        n = d[split]['samples']
        if batch:
            groups = read(B49 / 'run' / f'exp6k_{split}_{ds}_images.json')
            sizes = [len(g['cids']) if batch == 'selected' else g['all_candidates'] for g in groups.values()]
            # Share the unmarked whole-image pair; identify each query by bbox and its marked crops.
            inputs = [system + k*d['demo_tokens'][form] + d['full_pair'] + 32 + m*(292+64) for m in sizes]
            outputs = [48*m+8 for m in sizes]
        else:
            inputs = [system + k*d['demo_tokens'][form] + (292+64 if z0 else d['query_tokens'])] * n
            outputs = [48] * n
        if any(i > 260096 or o > 32768 or i+o > 262144 for i, o in zip(inputs, outputs)):
            raise ValueError(f'Budget exceeds model limits: {name} {ds}')
        cost = sum((i*price(i)[0]+o*price(i)[1])/1e6 for i,o in zip(inputs, outputs))*repeats
        # Sensitivity only; cannot assume which actual request prefix will hit.
        half_hit = sum((i*price(i)[0]*0.6+o*price(i)[1])/1e6 for i,o in zip(inputs, outputs))*repeats
        costs.append(dict(plan=plan,item=name,dataset=ds,k=k,form=form,repeats=repeats,
                          calls=len(inputs)*repeats,input_tokens=sum(inputs)*repeats,
                          output_tokens=sum(outputs)*repeats,cny=cost,cny_50pct_input_hit=half_hit,
                          max_input=max(inputs),max_output=max(outputs)))

    for plan, seeds, selected_k in [('full',3,16), ('lean',1,8)]:
        for ds in DATASETS:
            add(plan,'Z0',ds,z0=True)
            add(plan,'Z1',ds)
            for k in (2,4,8,16):
                add(plan,f'F{k} random3',ds,k,3)
                add(plan,f'F{k} retrieval',ds,k)
            for k in (32,64,128):
                add(plan,f'M{k} random{seeds}',ds,k,seeds)
                add(plan,f'M{k} retrieval',ds,k)
            # A with this k/strategy is already in F: reuse it, only B and AB add calls.
            for form in ('B','AB'):
                add(plan,f'form {form}',ds,selected_k,form=form)
            add(plan,'BQ all',ds,selected_k,batch='all')
            add(plan,'test frozen A',ds,selected_k,split='test')
    for ds in DATASETS:
        for k in (8,16,32,64,128):
            add('sensitivity','BQ selected',ds,k,batch='selected')
            add('sensitivity','BQ all',ds,k,batch='all')
            for form in ('A','B','AB'):
                add('sensitivity','test frozen',ds,k,form=form,split='test')
                if form != 'A':
                    add('sensitivity','form',ds,k,form=form)

    # Local model token counts use the real tokenizer and saved exp6j image grids.
    models = {'qwen':'Qwen3.5-4B','dars4b':'Mini-InternVL2-4B-DA-RS','earthdial':'EarthDial_4B_RGB'}
    gpu_rows = []
    for model, folder in models.items():
        tokenizer_path = MODELS / folder / 'tokenizer.json'
        if model == 'earthdial':
            # EarthDial has a SentencePiece tokenizer; the DA-RS tokenizer shares the Phi-3 text vocabulary.
            # Do not substitute it: retain explicit character estimates for this control only.
            encode = text_tokens
        else:
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            encode = lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)
        for ds in DATASETS:
            d = evidence['datasets'][ds]
            stats = {}
            for view in ('near','context'):
                directory = B45 / 'run/predictions/step2' / ds / f'{model}_{view}_multi'
                rows = [json.loads(line) for f in sorted(directory.glob('part-*.jsonl')) for line in f.read_text().splitlines()]
                raw = materials[f'exp6j_step2_sysu_second_{model}_{view}_multi']
                if model == 'qwen':
                    # Split once so newly inserted tokens cannot be expanded again.
                    parts = raw.split('<|image_pad|>')
                    expanded = parts[0] + ''.join('<|image_pad|>'*(t*h*w//4)+tail
                        for (t,h,w),tail in zip(rows[0]['image_grid_thw'],parts[1:]))
                    visual = sum(t*h*w//4 for t,h,w in rows[0]['image_grid_thw'])
                else:
                    parts = raw.split('<image>')
                    expanded = parts[0] + ''.join('<img>'+'<IMG_CONTEXT>'*(256*n)+'</img>'+tail
                        for n,tail in zip(rows[0]['num_patches_list'],parts[1:]))
                    visual = 256*sum(rows[0]['num_patches_list'])
                length = encode(expanded) if model != 'earthdial' else text_tokens(raw.replace('<image>',''))+visual+2*len(parts[1:])
                if model == 'dars4b':
                    length += 1  # LlamaTokenizerFast adds the configured BOS at model input.
                stats[view] = dict(n=len(rows),seconds=st.mean(r['seconds'] for r in rows),
                    median=st.median(r['seconds'] for r in rows),peak_gib=max(r['peak_gib'] for r in rows),
                    visual_tokens=visual,input_tokens=length)
            evidence['timings'][f'{model}/{ds}'] = stats
            slope = (stats['context']['seconds']-stats['near']['seconds'])/(stats['context']['input_tokens']-stats['near']['input_tokens'])
            system = encode(materials[f'{ds}_sys_v1_en'])
            demo_vision = 292 if model == 'qwen' else 516
            demo = demo_vision+48+d['answer_tokens']
            # Old user text is replaced by the system prompt and a 64-token query framing budget.
            query_visual = 292+d['full_pair'] if model == 'qwen' else 1032
            base = system+query_visual+64
            evidence['local_lengths'][f'{model}/{ds}'] = dict(system_tokens=system,base=base,demo=demo,
                                                            seconds_per_added_token=slope)
            for k in (0,2,4,8,16):
                length = base+k*demo
                seconds = stats['near']['seconds']+slope*(length-stats['near']['input_tokens'])
                gpu_rows.append(dict(model=model,dataset=ds,k=k,input_tokens=length,
                                     seconds=seconds,dev_hours=600*seconds/3600,
                                     test_hours=1500*seconds/3600,
                                     within_tokenizer_limit=length <= (262144 if model=='qwen' else 8192 if model=='dars4b' else 4096)))

    evidence['totals'] = {p:{key:sum(r[key] for r in costs if r['plan']==p)
                            for key in ('calls','input_tokens','output_tokens','cny','cny_50pct_input_hit')}
                          for p in ('full','lean')}
    evidence['gpu_totals'] = {}
    for model in models:
        load = read(B45 / 'run/step0' / f'{model}.json')['load_seconds']/3600
        summary = {'one_load_hours':load}
        for maximum in (8,16):
            z0 = sum(evidence['timings'][f'{model}/{ds}']['near']['seconds']*600/3600 for ds in DATASETS)
            z1 = sum(r['dev_hours'] for r in gpu_rows if r['model']==model and r['k']==0)
            few = sum(4*r['dev_hours'] for r in gpu_rows if r['model']==model and 0<r['k']<=maximum)
            summary[f'dev_kmax{maximum}'] = dict(z0=z0,z1=z1,few=few,inference=z0+z1+few,with_one_load=z0+z1+few+load)
        summary['test_hours_by_k'] = {k:sum(r['test_hours'] for r in gpu_rows if r['model']==model and r['k']==k)
                                      for k in (0,2,4,8,16)}
        evidence['gpu_totals'][model] = summary
    for name, rows in [('cost_breakdown.csv',costs),('gpu_breakdown.csv',gpu_rows)]:
        with (HERE / name).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (HERE/'estimate_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    report=[]
    for plan in ('full','lean'):
        report += [f'## {plan} 逐项预算', '', '两数据集合计；token 单位为百万；费用未抵扣缓存。', '']
        names=list(dict.fromkeys(r['item'] for r in costs if r['plan']==plan))
        rows=[]
        for name in names:
            group=[r for r in costs if r['plan']==plan and r['item']==name]
            rows.append([name,sum(r['calls'] for r in group),f"{sum(r['input_tokens'] for r in group)/1e6:.6f}",
                         f"{sum(r['output_tokens'] for r in group)/1e6:.6f}",f"{sum(r['cny'] for r in group):.3f}"])
        totals=evidence['totals'][plan]
        rows.append(['合计',totals['calls'],f"{totals['input_tokens']/1e6:.6f}",f"{totals['output_tokens']/1e6:.6f}",f"{totals['cny']:.3f}"])
        report += [table(['项目','调用次数','输入 M token','输出 M token','元'],rows),'']
    report += ['## 本地时延线性估算', '', table(['模型','数据集','k','输入 token','秒/候选','600 候选 GPU 小时','1500 候选 GPU 小时'],
        [[r['model'],r['dataset'],r['k'],r['input_tokens'],f"{r['seconds']:.4f}",f"{r['dev_hours']:.3f}",f"{r['test_hours']:.3f}"] for r in gpu_rows]),'']
    (HERE/'estimate_tables.md').write_text('\n'.join(report))
    print(json.dumps(evidence['totals'],indent=2))


if __name__ == '__main__':
    main()
