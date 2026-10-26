"""Offline paths, exact prompts, journals and input packing. See README_batch45.md."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
PROBE = WORKSPACE / 'wyh/CD-Agent/code/probe'
BASE = WORKSPACE / 'Chang-Agent/outputs/CD_1016_probe'
B43 = HERE.parent / 'batch43'
MODELS = WORKSPACE / 'models'
RUN = HERE / 'run'
SEED = 1025
PREFIX = '{"change": "'
FRAMING = ('Image 1 (T1, earlier):', 'Image 2 (T2, later):',
           'Image 3 (T1, earlier, wider context):', 'Image 4 (T2, later, wider context):')
MODEL_PATHS = {f'dars{n}b': MODELS / f'Mini-InternVL2-{n}B-DA-RS' for n in (1, 2, 4)}
MODEL_PATHS.update(earthdial=MODELS / 'EarthDial_4B_RGB', qwen=MODELS / 'Qwen3.5-4B')
sys.dont_write_bytecode = True
sys.path.insert(0, str(PROBE))
for k in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'):
    os.environ[k] = '1'
for k in ('HF_HOME', 'HF_MODULES_CACHE', 'MPLCONFIGDIR', 'XDG_CACHE_HOME'):
    os.environ[k] = str(HERE / '.cache' / k.lower())
for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[k] = '1'


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError(f'Output must remain inside {HERE}: {path}')
    return path


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_json(path, value):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def write_jsonl(path, rows):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    with tmp.open('w') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    tmp.replace(path)


def indexed(rows):
    result = {r['cid']: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('Duplicate candidate IDs')
    return result


def resume_rows(path):
    """Recover only an interrupted last write; interior malformed JSON is fatal."""
    path = local(path)
    if not path.exists():
        return {}
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
    return indexed(read_jsonl(path))


def sample_path(out, stage, dataset):
    return Path(out) / 'samples' / stage / f'{dataset}.jsonl'


def samples(out, stage, dataset):
    if stage == 'step1':
        return [{**r, 'dataset': dataset, 'weight': 1.0} for r in
                read_jsonl(BASE / 'exp3' / dataset / 'candidates.jsonl')]
    return read_jsonl(sample_path(out, stage, dataset))


def prompt_for(dataset, stage, view):
    # Read the exact prompt constants without importing exp3's torch dependencies.
    import ast
    module = ast.parse((PROBE / 'exp3_mllm.py').read_text())
    constants = {n.targets[0].id: ast.literal_eval(n.value) for n in module.body
                 if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                 and n.targets[0].id in ('PROMPT', 'GENERAL_PROMPT')}
    p = constants['PROMPT' if dataset in ('levir', 'whu') else 'GENERAL_PROMPT']
    if stage == 'step2':
        p = p.replace('red box', 'red contour')
        addition = ('The red contour marks only the candidate pixels outside the detector mask. '
                    'Judge change in these marked pixels only.\n')
        if view == 'context':
            addition += ('Images 3 and 4 show T1 and T2 over approximately three times the near-view '
                         'extent. Cyan contours show the detector mask D; red contours mark the target.\n')
        p = p.replace('Answer with JSON only:', addition + 'Answer with JSON only:')
    return p


def token_info(tokenizer):
    # SentencePiece's isolated-word token may contain a leading space. Score the
    # actual continuation of the exact JSON prefix, which differs for Phi-3.
    base = tokenizer.encode(PREFIX, add_special_tokens=False)
    isolated = {w: tokenizer.encode(w, add_special_tokens=False) for w in ('yes', 'no')}
    full = {w: tokenizer.encode(PREFIX+w, add_special_tokens=False) for w in ('yes', 'no')}
    if any(v[:len(base)] != base for v in full.values()):
        raise ValueError('Adding yes/no retokenizes the forced prefix')
    ids = {w:v[len(base):] for w,v in full.items()}
    if any(len(v) != 1 for v in ids.values()) or ids['yes'] == ids['no']:
        raise ValueError(f'Require distinct single-token yes/no continuations: {ids}')
    return {'ids': ids, 'isolated_ids': isolated, 'first_ids': [ids['yes'][0], ids['no'][0]],
            'readout': 'single_token_after_forced_prefix'}


def readout(logits, info):
    logits = logits.float()
    pair = logits[info['first_ids']]
    result = dict(p_yes=float(pair.softmax(0)[0]), logit_diff=float(pair[0] - pair[1]),
                  yes_no_mass=float((pair - logits.logsumexp(0)).exp().sum()))
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError('Nonfinite yes/no readout')
    return result


def input_images(row, view, packing):
    from PIL import Image, ImageDraw
    keys = ('t1', 't2', 'context_t1', 'context_t2') if view == 'context' else ('t1', 't2')
    images = []
    for k in keys:
        with Image.open(row['inputs'][k]) as im:
            images.append(im.convert('RGB'))
    if packing == 'multi':
        return images
    # Fixed full-resolution panels, 16-pixel white gutter, labels outside image pixels.
    width, height = images[0].size
    if any(im.size != (width, height) for im in images):
        raise ValueError('Contact sheet panels must have identical dimensions')
    sheet = Image.new('RGB', (width * 2 + 16, (height + 24) * (len(images) // 2) +
                             16 * (len(images) // 2 - 1)), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, im in enumerate(images):
        x, y = (i % 2) * (width + 16), (i // 2) * (height + 40)
        draw.text((x + 4, y + 4), FRAMING[i], fill='black')
        sheet.paste(im, (x, y + 24))
    return [sheet]


def shard_args(parser):
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--limit', type=int, default=0)


def check_shard(args):
    if not 0 <= args.shard < args.shards or args.limit < 0:
        raise ValueError('Require 0 <= shard < shards and limit >= 0')


def prediction_dir(out, stage, dataset, model, view, packing):
    return Path(out) / 'predictions' / stage / dataset / f'{model}_{view}_{packing}'


def predictions(folder, rows):
    """Require exactly one full, coherent run across its journal shards."""
    folder = Path(folder)
    config = read_json(folder / 'config.json')
    if config['samples'] != rows:
        raise ValueError(f'{folder}: sample manifest changed')
    found = []
    for shard in range(config['shards']):
        found.extend(read_jsonl(folder / f'part-{shard:03d}.jsonl'))
    result = indexed(found)
    if set(result) != set(indexed(rows)):
        raise ValueError(f'{folder}: incomplete prediction coverage {len(result)}/{len(rows)}')
    return [result[r['cid']] for r in rows]
