"""Serialized final ingestion of exp6l JSONL journals through store.RunRecorder."""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
from pathlib import Path

import exp6l_common as c


def run_name(config, folder):
    return f'exp6l_{config["dataset"]}_{"test" if config["final"] else "dev"}_{Path(folder).name}'


def template_for(config, group, abstract_query=True):
    msgs = c.messages(config, group)
    if config['bq']:
        for part in msgs[-1]['content']:
            if part.get('ref', '').startswith('shared.'):
                part['ref'] = part['ref'].replace('shared.', 'query[0].raw_')
    if abstract_query:
        index = 0
        for part in msgs[-1]['content']:
            if part['type'] == 'text' and part.get('text', '').startswith('Candidate '):
                part['text'] = f'Candidate {{query[{index}].cid}}; bbox {{query[{index}].bbox}}.'
                index += 1
    return c.template(msgs)


def insert(folder, db_path, replace=False):
    config, predictions, calls, report = c.load_run(folder)
    rows, _ = c.manifests(config['dataset'], config['final'], config['half'])
    rows = copy.deepcopy(rows)
    by_cid = {r['cid']: r for r in rows}
    # Samples are shared by all runs; keep BQ reference paths across later individual imports.
    for row in rows:
        row['inputs'].update({'raw_' + key: path for key, path in c.raw_pair(row)['inputs'].items()})
    readouts = sorted({r['readout'] for r in predictions.values()})
    readout = readouts[0] if len(readouts) == 1 else 'mixed' if readouts else 'failed'
    name = run_name(config, folder)
    with c.closing(c.readonly(db_path)) as db:
        existing = db.execute('SELECT config FROM run WHERE name=?', (name,)).fetchone()
    if existing and not replace:
        raise ValueError(f'Run already exists: {name}; explicit --replace is required')
    first_group = c.groups(config)[0]
    # Register the half-set once, with its recalculated weights, before constructing RunRecorder.
    db = c.store.connect(db_path)
    with db:
        if config['half']:
            c.store.add_sample_set(db, config['eval_set'], 'test' if config['final'] else 'dev',
                                   config['dataset'], rows,
                                   note='stratified half sample; seed 20261028; population/new stratum count')
    db.close()
    failed = [r for r in calls if r['status'] != 'ok']
    metadata = dict(exp6l=config, report=report, failures=failed,
                    source=str(Path(folder).resolve()), readouts=readouts,
                    usage=report['usage'], mode=config['mode'])
    with c.store.RunRecorder(db_path, name=name, model=config['model'],
            prompt_name=config['prompt_name'], prompt_text=config['prompt_text'], demos=config['demos'],
            mode=config['mode'], form='A', pool=config['pool'], eval_set=config['eval_set'],
            readout=readout, strategy=c.STRATEGY, seed=c.SEED,
            template=template_for(config, first_group), experiment='exp6l',
            view='near' if config['variant'] == 'Z0' else 'near_full', packing='multi',
            batch_size=max(map(len, c.groups(config))), batch_by='image' if config['bq'] else None,
            config=metadata, replace=replace) as recorder:
        if not config['demos']:
            # Upstream merges all empty demo lists and forces mode=zero. Preserve the
            # requested dataset pool and BQ mode on an experiment-specific empty set.
            did = f'exp6l-{config["mode"]}-A-{config["dataset"]}'
            recorder.db.execute('INSERT OR IGNORE INTO demo_set VALUES (?,?,?,?,?,?,?,?,?,?)',
                (did, did, config['mode'], 'A', 0, c.store.set_id_of(recorder.db, config['pool']),
                 c.STRATEGY, c.SEED, 'empty exp6l demo set with dataset-specific pool', c.store.now()))
            recorder.db.execute('UPDATE run SET demo_set_id=? WHERE id=?', (did, recorder.run_id))
        for call in calls:
            if call['status'] != 'ok':
                continue
            group = [by_cid[cid] for cid in call['cids']]
            results = [{key: value for key, value in r.items() if key != 'cid'} for r in call['results']]
            if config['bq']:
                recorder.record_batch(group, call['raw_text'], per_query=results,
                    group_key=group[0]['image'], usage=call['usage'],
                    cached_tokens=call['usage'].get('cached_tokens', 0),
                    template=template_for(config, group, abstract_query=False), seconds=call.get('seconds'))
            else:
                recorder.record(group[0], **results[0], raw_text=call['raw_text'], usage=call['usage'],
                                usage_meta=call['usage'], seconds=call.get('seconds'))
        for call in failed:
            for cid in call['cids']:
                if cid not in predictions:
                    recorder.record(by_cid[cid], error=call.get('error', call['status']), readout='failed')
        # Include paid failed attempts in totals, without counting successful BQ answers twice.
        recorder.usage.update({key: report['usage'][key] for key in recorder.usage})
        recorder.has_usage = True
    return name


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', type=Path, nargs='+', required=True)
    p.add_argument('--db', type=Path, default=c.DB)
    p.add_argument('--replace', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    # All workers using this entry point share one lock, including writers to the shared DB.
    lock_path = c.HERE / 'run/writedb.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        names = []
        for folder in a.runs:
            conf, pred, calls, report = c.load_run(folder)
            name = run_name(conf, folder)
            if name in names:
                raise ValueError('Duplicate run name in ingestion batch')
            names.append(name)
            if a.dry_run:
                template_for(conf, c.groups(conf)[0])
                print(json.dumps(dict(name=name, **report), ensure_ascii=False))
            else:
                print(insert(folder, a.db, a.replace))


if __name__ == '__main__':
    main()
