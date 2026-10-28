"""exp6l manifests, reviewed nested demonstrations and exact multimodal messages."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from functools import lru_cache
import fcntl
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
B49 = HERE.parent / 'batch49'
DB = B49 / 'run/verifier.db'
UI = B49 / 'audit/verifier_ui'
sys.path.insert(0, str(UI))
import store

SEED = 20261028
BOOT_SEED = 20261026
KS = (0, 4, 8, 12, 16)
STRATEGY = '类别平衡随机，种子20261028，嵌套前缀'
MODEL_NAMES = {'api': 'qwen3-vl-plus', 'qwen': 'Qwen3.5-4B',
               'dars4b': 'Mini-InternVL2-4B-DA-RS', 'earthdial': 'EarthDial_4B_RGB'}
PREFIX = '{"change": "'
FORMAT = ('For this request use JSON only, with keys in this order: '
          '"change" ("yes" or "no"), "confidence" (0–100 confidence in that answer), '
          '"reason" (one sentence). Judge whether at least half of the red-outlined candidate '
          'pixels are real change. Whole images provide context only.')
FRAMING = store.FRAMING
BATCH_FORMAT = (' Return {"answers": [{"change": "yes or no", "confidence": 0, '
                '"reason": "one sentence", "cid": "candidate ID"}, ...]} in the given '
                'candidate order. Answer only the listed candidates.')


def read(path):
    return json.loads(Path(path).read_text())


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError(f'Output must be inside {HERE}')
    return path


def write(path, obj):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        tmp = Path(f.name)
    tmp.replace(path)


def readonly(path=DB):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def manifests(dataset, final=False, half=False):
    kind = 'test' if final else 'dev'
    root = HERE / 'run' if half else B49 / 'run'
    stem = f'exp6l_{kind}_half_{dataset}' if half else f'exp6k_{kind}_{dataset}'
    rows = read(root / f'{stem}.json')
    images = read(root / f'{stem}_images.json')
    by_cid = {r['cid']: r for r in rows}
    listed = [cid for group in images.values() for cid in group['cids']]
    if len(by_cid) != len(rows) or len(listed) != len(rows) or set(listed) != set(by_cid):
        raise ValueError('Candidate and image manifests disagree')
    for name, group in images.items():
        if any(by_cid[c]['image'] != name for c in group['cids']):
            raise ValueError('Image grouping disagrees with candidate manifest')
    return rows, images


def export_reviewed(dataset, out, db_path=DB):
    """Use upstream --only-passed on a snapshot: upstream connect() mutates its input."""
    out = local(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='review-export-', dir=out.parent) as tmp:
        snapshot = Path(tmp) / 'verifier.db'
        with closing(readonly(db_path)) as src, closing(sqlite3.connect(snapshot)) as dest:
            src.backup(dest)
        subprocess.run([sys.executable, '-B', str(UI / 'export_demos.py'), '--db', str(snapshot),
                        '--set', f'exp6k_demos_{dataset}', '--only-passed', '--out', str(out)],
                       check=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    rows = read(out)
    for row in rows:
        note = (row.get('review_note') or '').strip()
        row['demo_meta'] = dict(review_status=row['review_status'], review_note=row.get('review_note'),
                                original_rationale=row.get('rationale'), rationale_overridden=bool(note))
        if note:
            row['rationale'] = note
        if row['dataset'] == 'second':
            row['semantic_text'] = store.semantic_line(row['semantic_t1'], row['semantic_t2'], row.get('semantic_full'))
    with closing(readonly(db_path)) as db:
        counts = db.execute('SELECT i.stratum,count(*) AS n FROM sample_set_item i '
                            'JOIN sample_set s ON s.id=i.set_id WHERE s.name=? GROUP BY i.stratum',
                            (f'exp6k_demos_{dataset}',)).fetchall()
    kept = Counter(r['stratum'] for r in rows)
    for r in counts:
        if 1 - kept[r['stratum']] / r['n'] > .25:
            raise ValueError(f'{dataset}/{r["stratum"]}: over 25% removed; replenish and audit before export')
    write(out, rows)
    return rows


def nested_demos(pool):
    strata = {s: sorted([r for r in pool if r['stratum'] == s], key=lambda r: r['cid'])
              for s in store.STRATA}
    if any(r['review_status'] != 'pass' for r in pool):
        raise ValueError('Demo pool contains unapproved samples')
    rng = random.Random(SEED)
    for rows in strata.values():
        rng.shuffle(rows)
    n2 = int(8 * len(strata['pos2']) / (len(strata['pos2']) + len(strata['pos_other'])) + .5)
    positives = strata['pos2'][:n2] + strata['pos_other'][:8-n2]
    if len(positives) != 8 or min(len(strata['hard_neg']), len(strata['rand_neg'])) < 4:
        raise ValueError('Insufficient reviewed examples for balanced 16-demo sequence')
    rng.shuffle(positives)
    # One hard and one random negative per four-example prefix.
    negatives = [r for pair in zip(strata['hard_neg'][:4], strata['rand_neg'][:4]) for r in pair]
    return [r for pair in zip(positives, negatives) for r in pair]


def prompt(dataset, variant, db_path=DB):
    name = 'exp6j_sysu_second_step2_near' if variant == 'Z0' else f'{dataset}_sys_v1_en'
    with closing(readonly(db_path)) as db:
        rows = db.execute('SELECT text FROM prompt_version WHERE name=?', (name,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f'Expected exactly one registered prompt: {name}')
    return name, rows[0]['text']


def image_parts(row, keys, ref):
    parts = []
    for key in keys:
        path = Path(row['inputs'][key])
        if not path.is_file():
            raise FileNotFoundError(path)
        parts += [{'type': 'text', 'text': f'{FRAMING[key]}:'},
                  {'type': 'image', 'path': str(path), 'ref': f'{ref}.{key}'}]
    return parts


def raw_pair(row):
    root = Path('/guisongxia01/pangchao/wangyihan/datasets/lim1')
    root /= 'SYSU-CD' if row['dataset'] == 'sysu' else 'SECOND'
    return dict(inputs={k: str(root / folder / row['image'])
                        for k, folder in [('full_t1', 'img1'), ('full_t2', 'img2')]})


def messages(config, queries):
    system = config['prompt_text']
    if config['form'] != 'A':
        raise ValueError('Only form A is supported')
    out = [{'role': 'system', 'content': system}]
    for i, demo in enumerate(config['demos']):
        content = image_parts(demo, ['t1', 't2'], f'demo[{i}]')
        content.append({'type': 'text', 'text': f'Candidate {demo["cid"]}. ' + FORMAT + (BATCH_FORMAT if config['bq'] else '')})
        answer = dict(change='yes' if demo['label'] else 'no', confidence=100, reason=demo['rationale'])
        if config['bq']:
            answer = dict(answers=[dict(**answer, cid=demo['cid'])])
        out += [{'role': 'user', 'content': content},
                {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}]
    content = []
    if config['bq']:
        content += image_parts(raw_pair(queries[0]), ['full_t1', 'full_t2'], 'shared')
    for i, query in enumerate(queries):
        # Deliberate allowlist: no query annotations or dataset-specific semantics.
        content.append({'type': 'text', 'text': f'Candidate {query["cid"]}; bbox {query["bbox"]}.'})
        keys = ['t1', 't2']
        if config['variant'] != 'Z0' and not config['bq']:
            keys += ['full_t1', 'full_t2']
        content += image_parts(query, keys, f'query[{i}]')
    instruction = FORMAT
    if config['bq']:
        instruction += BATCH_FORMAT + (' The whole images are unmarked; use each candidate bbox '
                                       'and red-outlined near views.')
    content.append({'type': 'text', 'text': instruction})
    out.append({'role': 'user', 'content': content})
    return out


def template(msgs):
    return [{**m, 'content': [({k: v for k, v in part.items() if k != 'path'})
                              for part in m['content']] if isinstance(m['content'], list) else [{'type': 'text', 'text': m['content']}]}
            for m in msgs]


def groups(config):
    rows, images = manifests(config['dataset'], config['final'], config['half'])
    if not config['bq']:
        return [[r] for r in rows]
    by_cid = {r['cid']: r for r in rows}
    return [[by_cid[c] for c in g['cids']] for g in images.values()]


def text_tokens(text):
    return math.ceil(sum(.25 if ord(c) < 128 else 1 for c in text))


def price(n):
    return (1, 10) if n <= 32768 else (1.5, 15) if n <= 131072 else (3, 30)


def cost(n, output, cached=0):
    inp, out = price(n)
    return ((n - .8 * cached) * inp + output * out) / 1e6


@lru_cache(maxsize=None)
def image_tokens(path):
    from PIL import Image
    with Image.open(path) as im:
        return math.ceil(im.width / 32) * math.ceil(im.height / 32) + 2


def estimate(config, query_group):
    # ESTIMATE.md framing allowances and image-token rule, with actual demo strings.
    def visual(row, keys):
        return sum(image_tokens(row['inputs'][key]) for key in keys)
    n = text_tokens(config['prompt_text'])
    for demo in config['demos']:
        n += visual(demo, ['t1', 't2']) + 48 + text_tokens(('yes' if demo['label'] else 'no') + '. ' + demo['rationale'])
    if config['bq']:
        n += visual(raw_pair(query_group[0]), ['full_t1', 'full_t2']) + 32
    for row in query_group:
        keys = ['t1', 't2'] + (['full_t1', 'full_t2'] if config['variant'] != 'Z0' and not config['bq'] else [])
        n += visual(row, keys) + 64
    output = len(query_group) * 48 + (8 if config['bq'] else 0)
    return dict(input_tokens=n, output_tokens=output, cost=cost(n, output), cached_tokens=0)


def add_arguments(p, *, required=True):
    p.add_argument('--dataset', choices=['sysu', 'second'], required=required)
    p.add_argument('--variant', choices=['Z0', 'Z1', 'F'], default='Z1')
    p.add_argument('--k', type=int, choices=KS, default=0)
    p.add_argument('--form', choices=['A'], default='A')
    p.add_argument('--bq', action='store_true')
    p.add_argument('--final', action='store_true')
    p.add_argument('--frozen', type=Path)
    p.add_argument('--prepared', type=Path, default=HERE / 'run/prepared')
    p.add_argument('--out', type=Path, required=required)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int, default=0, help='dry-run message inspection limit only')


def configuration(args, model):
    use_selection = args.final or (args.bq and args.frozen is not None)
    if args.bq and not args.dry_run and not args.frozen:
        raise ValueError('BQ requires --frozen from the individual-query development comparison')
    if use_selection:
        if not args.frozen:
            raise ValueError('--final requires a development-set freeze file')
        frozen = read(args.frozen)
        if args.final and frozen.get('stage') != 'final':
            raise ValueError('Test inference requires the final development freeze, after the BQ comparison')
        entry = frozen['selected'][args.dataset][MODEL_NAMES['qwen'] if model == 'earthdial' else MODEL_NAMES[model]]
        base = entry['config']
        if base['final']:
            raise ValueError('Selection must come from development data')
        for key in ('variant', 'k', 'form'):
            setattr(args, key, base[key])
        if not args.final and (base['bq'] or frozen.get('stage') != 'individual'):
            raise ValueError('BQ must use the individual-query development winner')
        if args.final:
            args.bq = base['bq']
            if hasattr(args, 'max_output_per_candidate'):
                args.max_output_per_candidate = base['max_output_per_candidate']
            if model != 'earthdial' and hasattr(args, 'context_limit'):
                if args.context_limit is not None and args.context_limit != base.get('context_limit'):
                    raise ValueError('Test context limit differs from frozen development configuration')
                args.context_limit = base.get('context_limit')
    if args.k not in KS or (args.variant == 'F') != (args.k > 0):
        raise ValueError('Z0/Z1 require k=0; F requires k=4/8/12/16')
    if args.k == 0 and args.form != 'A':
        raise ValueError('Zero-demo configurations use form A')
    if model != 'api' and (args.form != 'A' or args.bq):
        raise ValueError('Local models support form A and individual queries only')
    if model == 'earthdial' and not args.final:
        raise ValueError('EarthDial is a phase-two comparison only')
    if args.limit and not args.dry_run:
        raise ValueError('--limit is only allowed with --dry-run')
    order = read(args.prepared / f'order_{args.dataset}.json')
    name, system = prompt(args.dataset, args.variant)
    conf = dict(dataset=args.dataset, model=MODEL_NAMES[model], variant=args.variant, k=args.k,
                form='A', bq=args.bq, final=args.final, half=model == 'api', demos=order[:args.k],
                prompt_name=name, prompt_text=system, strategy=STRATEGY, seed=SEED,
                eval_set=(f'exp6l_{"test" if args.final else "dev"}_half_{args.dataset}' if model == 'api'
                          else f'exp6k_{"test" if args.final else "dev"}_{args.dataset}'),
                pool=f'exp6k_demos_{args.dataset}', mode='batched_many' if args.bq else 'few' if args.k else 'zero')
    rows, _ = manifests(args.dataset, args.final, half=model == 'api')
    conf['sample_order'] = [{key: r[key] for key in ('cid', 'image', 'unit_id', 'weight', 'label', 'stratum')} for r in rows]
    if use_selection:
        for key in ('demos', 'prompt_name', 'prompt_text'):
            if conf[key] != base[key]:
                raise ValueError(f'Frozen {key} differs from current preparation')
    return conf


def journal(path):
    path = local(path)
    if not path.exists():
        return []
    # Only recover an interrupted last JSON line, never an interior parse error.
    with path.open('rb+') as f:
        data = f.read()
        if data and not data.endswith(b'\n'):
            end = data.rfind(b'\n') + 1
            try:
                json.loads(data[end:])
            except (json.JSONDecodeError, UnicodeDecodeError):
                f.seek(end)
                f.truncate()
            else:
                f.write(b'\n')
    return [json.loads(line) for line in path.read_text().splitlines()]


def append(path, row):
    with local(path).open('a') as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def initialize(out, config):
    out = local(out)
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / '.writer.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = out / 'config.json'
    if path.exists() and read(path) != config:
        raise ValueError('Resume configuration changed; choose a different output directory')
    write(path, config)
    return lock


def dry_run(config, out, limit=1):
    gs = groups(config)
    estimates = [estimate(config, g) for g in gs]
    result = dict(calls=len(gs), candidates=sum(map(len, gs)),
                  **{k: sum(e[k] for e in estimates) for k in estimates[0]},
                  rule='ESTIMATE.md; 48 output tokens per candidate, uncached')
    write(out / 'dry_run.json', result)
    for i, group in enumerate(gs[:limit or 1]):
        write(out / f'messages-{i:03d}.json', messages(config, group))
    print(json.dumps(result, ensure_ascii=False))


def prepare_half(dataset, final):
    rows, images = manifests(dataset, final)
    layers = {s: sorted([r for r in rows if r['stratum'] == s], key=lambda r: r['cid'])
              for s in store.STRATA}
    sizes = {s: len(v) // 2 for s, v in layers.items()}
    # Largest remainder, stable STRATA order, preserves the exact half-set total.
    remaining = len(rows) // 2 - sum(sizes.values())
    for s in sorted(layers, key=lambda s: -(len(layers[s]) % 2))[:remaining]:
        sizes[s] += 1
    rng = random.Random(SEED)
    selected = {}
    sampling = read(B49 / 'run' / dataset / f'{"test" if final else "dev"}_sampling.json')['strata']
    for s, layer in layers.items():
        population = sampling[s]['population']
        if sampling[s]['selected'] != len(layer) or any(not math.isclose(r['weight'], population/len(layer)) for r in layer):
            raise ValueError('Source sampling populations disagree with manifest weights')
        for row in rng.sample(layer, sizes[s]):
            selected[row['cid']] = {**row, 'weight': population / sizes[s],
                                    'sampling_meta': dict(seed=SEED, original_weight=row['weight'],
                                                          stratum_population=population,
                                                          original_n=len(layer), selected_n=sizes[s])}
    half = [selected[r['cid']] for r in rows if r['cid'] in selected]
    half_images = {name: {**g, 'cids': [c for c in g['cids'] if c in selected]}
                   for name, g in images.items() if any(c in selected for c in g['cids'])}
    stem = f'exp6l_{"test" if final else "dev"}_half_{dataset}'
    for suffix, value in [('', half), ('_images', half_images)]:
        path = HERE / 'run' / (stem + suffix + '.json')
        if path.exists() and read(path) != value:
            raise ValueError('Existing half manifest differs; review upstream changes before replacing it')
        write(path, value)
    return dict(samples=len(half), images=len(half_images), strata=sizes)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['prepare'])
    p.add_argument('--out', type=Path, default=HERE / 'run/prepared')
    a = p.parse_args()
    for ds in ('sysu', 'second'):
        pool = export_reviewed(ds, a.out / f'exp6k_demos_{ds}.json')
        order = nested_demos(pool)
        write(a.out / f'order_{ds}.json', order)
        print(ds, len(pool), Counter(d['stratum'] for d in order))
        for final in (False, True):
            print(ds, 'test' if final else 'dev', prepare_half(ds, final))


def load_run(folder):
    folder = Path(folder)
    config = read(folder / 'config.json')
    paths = sorted(folder.glob('part-*.jsonl')) if config['model'] != MODEL_NAMES['api'] else [folder / 'predictions.jsonl']
    calls = [r for path in paths for r in journal(path)]
    predictions = {}
    for call in calls:
        if call['status'] != 'ok':
            continue
        for result in call['results']:
            if result['cid'] in predictions:
                raise ValueError('Repeated successful candidate in journals')
            predictions[result['cid']] = result
    expected, _ = manifests(config['dataset'], config['final'], config['half'])
    snapshot = [{key: r[key] for key in ('cid', 'image', 'unit_id', 'weight', 'label', 'stratum')} for r in expected]
    if config.get('sample_order') != snapshot:
        raise ValueError('Current eval_set differs from the run sample snapshot')
    if set(predictions) - {r['cid'] for r in expected}:
        raise ValueError('Journal contains candidates outside its eval_set')
    attempts = [read(p) for p in folder.glob('attempt-*.json')]
    billed = attempts if attempts else [r for r in calls if r['status'] == 'ok']
    ledger_path = config.get('budget_ledger')
    ledger_attempts = None
    if ledger_path and Path(ledger_path).exists():
        prefix = str(folder.resolve()) + ':'
        ledger_attempts = [r for r in read(ledger_path)['attempts'] if r['key'].startswith(prefix)]
        billed = ledger_attempts
    usage = {key: sum((r.get('usage') or {}).get(key, 0) for r in billed)
             for key in ('input_tokens', 'output_tokens', 'cached_tokens', 'cost')}
    usage['reserved_unknown_cny'] = (sum(r['reserved'] for r in ledger_attempts if 'cost' not in r)
                                     if ledger_attempts is not None else sum(r.get('reserved_cny', 0) for r in attempts))
    usage['api_calls'] = (len(ledger_attempts) if ledger_attempts is not None else len(attempts) if attempts
                          else len(calls) if config['model'] == MODEL_NAMES['api'] else 0)
    usage['seconds'] = sum(r.get('seconds', 0) for r in calls)
    usage['load_seconds'] = sum(r['load_seconds'] for p in folder.glob('loads-*.jsonl') for r in journal(p))
    usage['seconds_per_candidate'] = usage['seconds'] / len(predictions) if predictions else None
    usage['cost_per_candidate'] = usage['cost'] / len(predictions) if predictions else None
    complete = set(predictions) == {r['cid'] for r in expected}
    failed = {cid for call in calls if call['status'] == 'failed' for cid in call['cids']} - set(predictions)
    return config, predictions, calls, dict(complete=complete, completed=len(predictions),
        failed_candidates=len(failed), expected=len(expected), usage=usage)


def claim_final(config, out):
    if not config['final']:
        return
    path = HERE / 'run/final_registry.json'
    key = config['dataset'] + '/' + config['model']
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read(path) if path.exists() else {}
        entry = dict(out=str(local(out)), config=config)
        if key in data and data[key] != entry:
            raise ValueError('A test run is already claimed for this model/dataset; resume its existing directory')
        data[key] = entry
        write(path, data)


if __name__ == '__main__':
    main()
