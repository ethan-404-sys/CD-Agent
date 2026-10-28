"""Model-free dry-run acceptance using actual audited demos, manifests, images and score files."""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from contextlib import closing
import inspect
import json
import math
from pathlib import Path
import subprocess

import exp6l_common as c
from exp6l_budget import config
from exp6l_eval import controls
from exp6l_writedb import template_for


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dry-run', action='store_true', required=True)
    a = p.parse_args()
    reports = []
    source_info = c.DB.stat()
    for ds in ('sysu', 'second'):
        pool = c.read(c.HERE / 'run/prepared' / f'exp6k_demos_{ds}.json')
        order = c.read(c.HERE / 'run/prepared' / f'order_{ds}.json')
        assert order == c.nested_demos(pool)
        assert len({r['cid'] for r in order}) == 16
        assert all(r['review_status'] == 'pass' for r in pool)
        assert all(r['rationale'] == r['review_note'].strip() for r in pool if (r.get('review_note') or '').strip())
        if ds == 'sysu':
            revised = next(r for r in pool if r['cid'] == 'train_14994:2')
            assert revised['rationale'] == '感觉像是水体变成陆地了'
            assert revised['demo_meta']['rationale_overridden']
        for k in c.KS:
            demo = order[:k]
            assert sum(r['label'] for r in demo) == k//2
            assert all(r['label'] == (1 if i%2 == 0 else 0) for i, r in enumerate(demo))
            assert sum(r['stratum'] == 'hard_neg' for r in demo) >= k/4
        for final in (False, True):
            full, _ = c.manifests(ds, final)
            half, images = c.manifests(ds, final, True)
            assert len(half)*2 == len(full) == (3000//2 if final else 1200//2)
            assert {r['cid'] for r in half} <= {r['cid'] for r in full}
            for name in c.store.STRATA:
                assert math.isclose(sum(r['weight'] for r in half if r['stratum']==name),
                                    sum(r['weight'] for r in full if r['stratum']==name))
            summary = c.prepare_half(ds, final)
            assert summary['images'] == len(images)
            frozen = c.read(c.HERE.parent / 'batch48/run' / ds / 'evaluation/final/frozen.json')
            fused = [name for name in frozen['methods'] if name.startswith('fuse-img-') and name.endswith('-s1020')]
            assert len(fused) == 1
            for half_flag, rows in [(True, half), (False, full)]:
                aligned = controls(ds, rows, final, fused[0])
                assert all(len(values)==len(rows) and all(math.isfinite(v) for v in values) for values in aligned.values())
            reports.append(dict(dataset=ds, split='test' if final else 'dev', **summary, aligned_controls=list(aligned)))
        for model in ('api', 'qwen', 'dars4b'):
            for variant, k in [('Z0',0),('Z1',0),*[('F',k) for k in c.KS[1:]]]:
                conf = config(ds,variant,k,model)
                group = c.groups(conf)[0]
                msgs = c.messages(conf,group)
                assert len(msgs)==2+2*k
                assert msgs[0]['content']==conf['prompt_text']
                assert [m['role'] for m in msgs]==['system']+['user','assistant']*k+['user']
                for i in range(k):
                    answer=json.loads(msgs[2+2*i]['content'])
                    assert set(answer)=={'change','confidence','reason'}
                    assert answer['reason']==conf['demos'][i]['rationale']
                query=json.dumps(msgs[-1],ensure_ascii=False)
                assert all(word not in query for word in ('semantic_text','change_kind','stratum','pos2','hard_neg','"label"'))
                paths=[p['path'] for p in msgs[-1]['content'] if p['type']=='image']
                assert paths==[group[0]['inputs'][key] for key in (['t1','t2'] if variant=='Z0' else ['t1','t2','full_t1','full_t2'])]
                assert all(isinstance(m['content'],list) for m in template_for(conf,group))
                if k==16:
                    c.write(c.HERE/'verification'/f'{ds}_{model}_messages.json',msgs)
        bq=dict(config(ds,'F',16),bq=True,mode='batched_many')
        groups=c.groups(bq)
        assert sum(map(len,groups))==300
        largest=max(groups,key=len)
        msgs=c.messages(bq,largest)
        assert len([p for p in msgs[-1]['content'] if p['type']=='image'])==2+2*len(largest)
        assert json.loads(msgs[2]['content'])['answers'][0]['cid']==order[0]['cid']
        assert all(r['image']==largest[0]['image'] for r in largest)
        c.write(c.HERE/'verification'/f'{ds}_BQ_messages.json',msgs)
        c.write(c.HERE/'verification'/f'{ds}_BQ_template.json',template_for(bq,largest))
        reports.append(dict(dataset=ds,bq_groups=len(groups),bq_largest=len(largest),query_annotation_fields_absent=True))
    with closing(c.readonly()) as db:
        columns={r['name'] for r in db.execute('PRAGMA table_info(output)')}
        assert {'extra','p_yes','answer','confidence'} <= columns
    signature=inspect.signature(c.store.RunRecorder)
    assert all(key in signature.parameters for key in ('demos','mode','form','pool','eval_set','readout','strategy','seed','template','batch_by'))
    for path in c.HERE.glob('exp6l_*.py'):
        ast.parse(path.read_text(),filename=str(path))
    subprocess.run(['bash','-n', 'run_exp6l_job.sh','run_exp6l_cpu.sbatch','run_exp6l_gpu.sbatch','submit_exp6l.sh'],cwd=c.HERE,check=True)
    end=c.DB.stat()
    assert (source_info.st_size,source_info.st_mtime_ns)==(end.st_size,end.st_mtime_ns)
    result=dict(passed=True,checks=reports,shared_database_unchanged=True,
                not_executed=['API request/logprobs/retries/budget under network concurrency','GPU model forward and actual context limits',
                              '2000-replicate statistics','RunRecorder database ingestion','Slurm submission'])
    c.write(c.HERE/'verification/dry_run_checks.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
