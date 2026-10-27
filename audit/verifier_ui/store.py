"""SQLite store for MLLM verifier runs: prompts, ICL demo sets, raw outputs, metrics.

Inference scripts write through RunRecorder; app.py only reads. A minimal ICL run:

    from store import RunRecorder, icl_template
    demos = [dict(dataset='second', split='train', cid=..., image=..., label=1, inputs={...})]
    with RunRecorder('verifier.db', name='icl_second_k8_s0', model='qwen3.5-4b',
                     prompt_name='general_v3', prompt_text=PROMPT, demos=demos,
                     mode='few', strategy='random balanced', seed=0,
                     template=icl_template(len(demos), PROMPT)) as rec:
        for q in queries:
            rec.record(q, p_yes=..., answer='yes', confidence=80, raw_text=text)

For batched many-shot, call rec.record_batch(queries, raw_text, per_query=[...]) once
per model call. Samples are dicts with dataset, split, cid, inputs and (if known) label.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).with_name('schema.sql')
FRAMING = {'t1': 'T1, earlier', 't2': 'T2, later',
           'context_t1': 'T1, earlier, wider context', 'context_t2': 'T2, later, wider context'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA.read_text())
    return db


def digest(value):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def add_prompt(db, name, text, parent_id=None, note=None):
    pid = digest(text)
    db.execute('INSERT OR IGNORE INTO prompt_version VALUES (?,?,?,?,?,?)',
               (pid, name, text, parent_id, note, now()))
    return pid


def add_sample(db, s):
    keys = ('dataset', 'split', 'cid', 'image', 'source', 'label', 'inputs')
    meta = {k: v for k, v in s.items() if k not in keys}
    db.execute('INSERT OR IGNORE INTO sample (dataset, split, cid, image, source, label, inputs, meta) '
               'VALUES (?,?,?,?,?,?,?,?)',
               (s['dataset'], s['split'], str(s['cid']), s.get('image'), s.get('source'),
                None if s.get('label') is None else int(s['label']),
                json.dumps(s['inputs']), json.dumps(meta, default=str) if meta else None))
    return db.execute('SELECT id FROM sample WHERE dataset=? AND split=? AND cid=?',
                      (s['dataset'], s['split'], str(s['cid']))).fetchone()[0]


def add_demo_set(db, demos, mode, name=None, strategy=None, seed=None, note=None, shown_labels=None):
    """demos: ordered sample dicts. shown_labels defaults to yes/no from each demo's label."""
    ids = [add_sample(db, d) for d in demos]
    shown = shown_labels or [None if d.get('label') is None else ('yes' if d['label'] else 'no') for d in demos]
    did = digest({'mode': mode, 'items': ids, 'shown': shown})
    db.execute('INSERT OR IGNORE INTO demo_set VALUES (?,?,?,?,?,?,?,?)',
               (did, name or f'{mode}_k{len(ids)}_{did[:6]}', mode, len(ids), strategy, seed, note, now()))
    db.executemany('INSERT OR IGNORE INTO demo_set_item VALUES (?,?,?,?)',
                   [(did, i, sid, lab) for i, (sid, lab) in enumerate(zip(ids, shown))])
    return did


def image_block(ref, view):
    out = []
    for key in (('t1', 't2', 'context_t1', 'context_t2') if view == 'context' else ('t1', 't2')):
        out += [{'type': 'text', 'text': f'{ref} ({FRAMING[key]}):'}, {'type': 'image', 'ref': f'{ref}.{key}'}]
    return out


def zero_shot_template(prompt, view='near'):
    """exp3 / exp6j layout: Image 1 (T1), Image 2 (T2) [, context pair], then the prompt."""
    content = []
    for i, key in enumerate(('t1', 't2', 'context_t1', 'context_t2')[: 4 if view == 'context' else 2]):
        content += [{'type': 'text', 'text': f'Image {i + 1} ({FRAMING[key]}):'},
                    {'type': 'image', 'ref': f'query[0].{key}'}]
    return [{'role': 'user', 'content': content + [{'type': 'text', 'text': prompt}]}]


def icl_template(k, prompt, view='near', batch=1):
    """Suggested ICL layout: prompt, then k labelled demos, then the query/queries.
    Scripts that lay out differently should pass their own template."""
    content = [{'type': 'text', 'text': prompt}]
    for i in range(k):
        content += image_block(f'demo[{i}]', view) + [{'type': 'text', 'ref': f'demo[{i}].answer'}]
    for j in range(batch):
        content += image_block(f'query[{j}]', view)
    return [{'role': 'user', 'content': content}]


def git_commit():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=Path(__file__).parent,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class RunRecorder:
    def __init__(self, db_path, name, model, prompt_text, template, prompt_name=None, demos=(),
                 mode='zero', strategy=None, seed=None, experiment=None, readout=None, view='near',
                 packing='multi', batch_size=1, config=None, prompt_parent=None, prompt_note=None,
                 replace=False):
        self.db = connect(db_path)
        with self.db:
            if replace:
                delete_run(self.db, name)
            pid = add_prompt(self.db, prompt_name or name, prompt_text, prompt_parent, prompt_note)
            did = add_demo_set(self.db, list(demos), mode if demos else 'zero', strategy=strategy, seed=seed)
            cur = self.db.execute(
                'INSERT INTO run (name, experiment, model, prompt_id, demo_set_id, readout, view, packing, '
                'batch_size, template, git_commit, config, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (name, experiment, model, pid, did, readout, view, packing, batch_size,
                 json.dumps(template, ensure_ascii=False), git_commit(),
                 json.dumps(config, default=str) if config else None, now()))
            self.run_id = cur.lastrowid
        self.calls = 0

    def record(self, sample, call_id=None, position=None, p_yes=None, logit_diff=None, yes_no_mass=None,
               answer=None, confidence=None, raw_text=None, **extra):
        sid = add_sample(self.db, sample)
        self.db.execute('INSERT OR REPLACE INTO output VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (self.run_id, sid, call_id, position, p_yes, logit_diff, yes_no_mass, answer,
                         confidence, raw_text, json.dumps(extra, default=str) if extra else None))

    def record_batch(self, queries, raw_text, per_query, **meta):
        """One call answering several queries; per_query[i] holds record() kwargs for queries[i]."""
        cur = self.db.execute('INSERT INTO call (run_id, idx, raw_text, meta) VALUES (?,?,?,?)',
                              (self.run_id, self.calls, raw_text, json.dumps(meta, default=str) if meta else None))
        self.calls += 1
        for pos, (q, kw) in enumerate(zip(queries, per_query, strict=True)):
            self.record(q, call_id=cur.lastrowid, position=pos, **kw)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()


def delete_run(db, name):
    row = db.execute('SELECT id FROM run WHERE name=?', (name,)).fetchone()
    if row:
        for table in ('output', 'call'):
            db.execute(f'DELETE FROM {table} WHERE run_id=?', (row[0],))
        db.execute('DELETE FROM run WHERE id=?', (row[0],))


# ---- metrics (computed on read) ----

def prediction(row):
    if row['answer'] in ('yes', 'no'):
        return int(row['answer'] == 'yes')
    return None if row['p_yes'] is None else int(row['p_yes'] > 0.5)


def auc(labels, scores):
    import numpy as np
    labels, scores = np.asarray(labels), np.asarray(scores, float)
    pos = labels == 1
    n1, n0 = pos.sum(), (~pos).sum()
    if not n1 or not n0:
        return None
    ranks = np.empty(len(scores))
    ranks[np.argsort(scores, kind='mergesort')] = np.arange(1, len(scores) + 1)
    _, inv, counts = np.unique(scores, return_inverse=True, return_counts=True)
    ranks = (np.bincount(inv, weights=ranks) / counts)[inv]  # average ranks for ties
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def metrics(rows, n_boot=2000, seed=0):
    """rows: dicts with label, p_yes, answer. Accuracy uses the generated answer when
    present, otherwise p_yes > 0.5 (same as exp3_analyze)."""
    import numpy as np
    rows = [r for r in rows if r['label'] is not None]
    pred = [prediction(r) for r in rows]
    scored = [(r['label'], p) for r, p in zip(rows, pred) if p is not None]
    out = {'n': len(rows), 'n_pos': sum(r['label'] for r in rows), 'acc': None, 'acc_ci': None,
           'yes_rate': None, 'auc': None, 'auc_ci': None}
    if scored:
        y, p = np.array(scored).T
        correct = (y == p).astype(float)
        out['acc'], out['yes_rate'] = float(correct.mean()), float(p.mean())
    rng = np.random.default_rng(seed)
    if scored:
        boot = correct[rng.integers(0, len(correct), (n_boot, len(correct)))].mean(1)
        out['acc_ci'] = [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))]
    with_p = [(r['label'], r['p_yes']) for r in rows if r['p_yes'] is not None]
    if with_p:
        y, s = map(np.array, zip(*with_p))
        out['auc'] = auc(y, s)
        if out['auc'] is not None:
            vals = [v for idx in rng.integers(0, len(y), (n_boot, len(y)))
                    if (v := auc(y[idx], s[idx])) is not None]
            out['auc_ci'] = [float(np.quantile(vals, .025)), float(np.quantile(vals, .975))]
    return out
