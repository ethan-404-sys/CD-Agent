"""Read-only audit UI for MLLM verifier runs.

  VERIFIER_DB=/path/verifier.db uvicorn app:app --port 8765    (from this directory)
  then on the laptop: ssh -L 8765:localhost:8765 <server>, open http://localhost:8765
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

import store

DB = Path(os.environ.get('VERIFIER_DB', Path(__file__).with_name('verifier.db')))
STATIC = Path(__file__).with_name('static')
app = FastAPI(title='MLLM verifier audit')


def db():
    return store.connect(DB)


def rows(sql, *args):
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def demo_items(demo_set_id):
    return rows('SELECT i.position, i.shown_label, s.* FROM demo_set_item i JOIN sample s ON s.id = i.sample_id '
                'WHERE i.demo_set_id = ? ORDER BY i.position', demo_set_id)


def domain(demos, dataset):
    if not demos:
        return 'none'
    same = sum(d['dataset'] == dataset for d in demos)
    return 'same' if same == len(demos) else 'cross' if same == 0 else 'mixed'


@lru_cache(maxsize=4096)
def run_metrics(run_id, dataset, n_outputs):
    # n_outputs is part of the key so a run that is still being written is recomputed.
    out = rows('SELECT s.label, o.p_yes, o.answer FROM output o JOIN sample s ON s.id = o.sample_id '
               'WHERE o.run_id = ? AND s.dataset = ?', run_id, dataset)
    return store.metrics(out)


def run_summaries():
    runs = rows('SELECT r.*, p.name AS prompt_name, d.mode, d.k, d.name AS demo_set_name FROM run r '
                'JOIN prompt_version p ON p.id = r.prompt_id JOIN demo_set d ON d.id = r.demo_set_id ORDER BY r.id')
    counts = defaultdict(dict)
    for r in rows('SELECT o.run_id, s.dataset, COUNT(*) AS n FROM output o JOIN sample s ON s.id = o.sample_id '
                  'GROUP BY o.run_id, s.dataset'):
        counts[r['run_id']][r['dataset']] = r['n']
    demos = {}
    for r in runs:
        r.pop('template')
        if r['demo_set_id'] not in demos:
            demos[r['demo_set_id']] = demo_items(r['demo_set_id'])
        r['datasets'] = {ds: {**run_metrics(r['id'], ds, n), 'demo_domain': domain(demos[r['demo_set_id']], ds)}
                         for ds, n in sorted(counts[r['id']].items())}
    return runs


@app.get('/api/prompts')
def prompts():
    return rows('SELECT p.*, COUNT(r.id) AS runs, GROUP_CONCAT(DISTINCT r.model) AS models FROM prompt_version p '
                'LEFT JOIN run r ON r.prompt_id = p.id GROUP BY p.id ORDER BY p.created_at, p.name')


@app.get('/api/demo_sets')
def demo_sets():
    sets = rows('SELECT d.*, COUNT(DISTINCT r.id) AS runs FROM demo_set d LEFT JOIN run r ON r.demo_set_id = d.id '
                "GROUP BY d.id ORDER BY CASE d.mode WHEN 'zero' THEN 0 WHEN 'few' THEN 1 WHEN 'many' THEN 2 ELSE 3 END, d.k")
    for d in sets:
        items = demo_items(d['id'])
        d['sources'] = dict(sorted(
            {f"{i['dataset']}/{i['split']}": sum(1 for j in items if (j['dataset'], j['split']) == (i['dataset'], i['split']))
             for i in items}.items()))
        d['n_pos'] = sum(i['label'] == 1 for i in items)
        d['n_test_split'] = sum(i['split'] == 'test' for i in items)
    return sets


@app.get('/api/demo_sets/{demo_set_id}')
def demo_set(demo_set_id: str):
    head = rows('SELECT * FROM demo_set WHERE id = ?', demo_set_id)
    if not head:
        raise HTTPException(404)
    return {**head[0], 'items': demo_items(demo_set_id)}


@app.get('/api/runs')
def runs():
    return run_summaries()


@app.get('/api/runs/{run_id}')
def run(run_id: int):
    found = [r for r in run_summaries() if r['id'] == run_id]
    if not found:
        raise HTTPException(404)
    r = found[0]
    r['template'] = json.loads(rows('SELECT template FROM run WHERE id = ?', run_id)[0]['template'])
    r['prompt_text'] = rows('SELECT text FROM prompt_version WHERE id = ?', r['prompt_id'])[0]['text']
    r['demos'] = demo_items(r['demo_set_id'])
    # Leakage checks: demo crops taken from the test split, or from the same source image as a query.
    demo_images = {(d['dataset'], d['image']) for d in r['demos'] if d['image']}
    demo_ids = {d['id'] for d in r['demos']}
    queries = rows('SELECT s.id, s.dataset, s.image FROM output o JOIN sample s ON s.id = o.sample_id WHERE o.run_id = ?',
                   run_id)
    r['leaks'] = {'demos_from_test_split': sum(d['split'] == 'test' for d in r['demos']),
                  'queries_sharing_demo_image': sum((q['dataset'], q['image']) in demo_images for q in queries),
                  'queries_that_are_demos': sum(q['id'] in demo_ids for q in queries)}
    r['calls'] = rows('SELECT COUNT(*) AS n FROM call WHERE run_id = ?', run_id)[0]['n']
    return r


@app.get('/api/runs/{run_id}/outputs')
def outputs(run_id: int, dataset: str = '', source: str = '', verdict: str = '', limit: int = 100, offset: int = 0):
    """verdict: '' | wrong | right | fp | fn. Wrong answers sort first, most confident first."""
    out = rows('SELECT o.*, s.dataset, s.split, s.cid, s.image, s.source, s.label, s.inputs, c.raw_text AS call_raw '
               'FROM output o JOIN sample s ON s.id = o.sample_id LEFT JOIN call c ON c.id = o.call_id '
               'WHERE o.run_id = ?', run_id)
    for o in out:
        o['pred'] = store.prediction(o)
        o['correct'] = None if o['pred'] is None or o['label'] is None else o['pred'] == o['label']
        o['inputs'] = list(json.loads(o['inputs']))
    keep = {'wrong': lambda o: o['correct'] is False, 'right': lambda o: o['correct'] is True,
            'fp': lambda o: o['pred'] == 1 and o['label'] == 0, 'fn': lambda o: o['pred'] == 0 and o['label'] == 1}
    out = [o for o in out if (not dataset or o['dataset'] == dataset) and (not source or o['source'] == source)
           and (not verdict or keep[verdict](o))]
    out.sort(key=lambda o: (o['correct'] is not False, -abs((o['p_yes'] if o['p_yes'] is not None else .5) - .5),
                            o['dataset'], o['cid']))
    sources = sorted({o['source'] for o in out if o['source']})
    return {'total': len(out), 'sources': sources, 'rows': out[offset: offset + limit]}


@app.get('/api/calls/{call_id}')
def call_members(call_id: int):
    """Queries answered together in one batched call, in prompt order."""
    return rows('SELECT o.position, o.answer, s.* FROM output o JOIN sample s ON s.id = o.sample_id '
                'WHERE o.call_id = ? ORDER BY o.position', call_id)


@app.get('/api/shots')
def shots():
    """One point per (run, query dataset): x = number of demos, y = accuracy / AUC."""
    points = []
    for r in run_summaries():
        for ds, m in r['datasets'].items():
            points.append({'run': r['name'], 'run_id': r['id'], 'model': r['model'], 'prompt_id': r['prompt_id'],
                           'prompt_name': r['prompt_name'], 'mode': r['mode'], 'k': r['k'], 'dataset': ds,
                           'demo_domain': m['demo_domain'], 'view': r['view'], 'packing': r['packing'],
                           'batch_size': r['batch_size'], **{k: m[k] for k in ('n', 'acc', 'acc_ci', 'auc', 'auc_ci')}})
    return points


@app.get('/img/{sample_id}/{key}')
def image(sample_id: int, key: str):
    # Only paths registered for a sample are served.
    found = rows('SELECT inputs FROM sample WHERE id = ?', sample_id)
    path = found and json.loads(found[0]['inputs']).get(key)
    if not path or not Path(path).is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')
