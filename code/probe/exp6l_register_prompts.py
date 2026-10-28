"""Register historical prompts in a local verifier database copy; never load weights."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

from jinja2 import Environment

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
WORK = HERE.parents[2]
PROBE = WORK / 'wyh/CD-Agent/code/probe'
B45 = HERE.parent / 'batch45'
B49 = HERE.parent / 'batch49'
MODELS = WORK / 'models'


def read(path):
    return json.loads(Path(path).read_text())


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_symbols(path, names, namespace=None):
    """Execute only selected constant assignments and pure rendering functions."""
    tree = ast.parse(path.read_text())
    selected = [node for node in tree.body
                if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in names)
                or (isinstance(node, ast.FunctionDef) and node.name in names)]
    env = {} if namespace is None else dict(namespace)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), env)
    return {name: env[name] for name in names}


def qwen_render(prompt, labels, prefix):
    template = Environment().from_string((MODELS / 'Qwen3.5-4B/chat_template.jinja').read_text())
    content = []
    for label in labels:
        content.extend([{'type': 'text', 'text': label}, {'type': 'image'}])
    content.append({'type': 'text', 'text': prompt})
    return template.render(messages=[{'role': 'user', 'content': content}],
                           add_generation_prompt=True, enable_thinking=False) + prefix


def collect():
    old = source_symbols(PROBE / 'exp3_mllm.py', ['PROMPT', 'GENERAL_PROMPT', 'PREFIX'])
    common = source_symbols(B45 / 'exp6j_common.py', ['FRAMING', 'PREFIX', 'prompt_for'], {'PROBE': PROBE})
    v2 = source_symbols(PROBE / 'exp6v2_common.py', ['PROMPT', 'INSTRUCTIONS', 'MARKER'], old)
    records = []

    def add(name, text, note, parent=None):
        records.append(dict(name=name, text=text, note=note, parent=parent))

    add('exp3_levir_whu', old['PROMPT'], f'来源 {PROBE}/exp3_mllm.py PROMPT；exp3、exp6j step1 LEVIR/WHU。')
    add('exp3_sysu_second', old['GENERAL_PROMPT'], f'来源 {PROBE}/exp3_mllm.py GENERAL_PROMPT；exp3、exp6j step1 SYSU/SECOND。')
    for view in ('near', 'context'):
        add(f'exp6j_sysu_second_step2_{view}', common['prompt_for']('sysu', 'step2', view),
            f'来源 {B45}/exp6j_common.py prompt_for；exp6j step2 SYSU/SECOND 共用；'
            '已执行 red box→red contour 和新增像素说明拼接；context 另含青色 D 与第 3/4 图说明。',
            'exp3_sysu_second')

    # Saved run configs are the evidence for the actual experiment prompt, not a new draft.
    seen = set()
    for path in sorted((B45 / 'run/predictions').glob('step*/*/*/config.json')):
        config = read(path)
        stage, ds, model = config['stage'], config['dataset'], config['model']
        view, packing = config['view'], config['packing']
        prompt = common['prompt_for'](ds, stage, view)
        if config['prompt'] != prompt:
            raise ValueError(f'Historical config and current source disagree: {path}')
        group = 'levir_whu' if ds in ('levir', 'whu') else 'sysu_second'
        key = (stage, group, model, view, packing)
        if key in seen:
            continue
        seen.add(key)
        labels = common['FRAMING'][:4 if view == 'context' else 2] if packing == 'multi' else ('Panel sheet (numbered images):',)
        parent = f'exp6j_sysu_second_step2_{view}' if stage == 'step2' else f'exp3_{group}'
        if model == 'qwen':
            rendered = qwen_render(prompt, labels, common['PREFIX'])
            detail = '按本地 chat_template.jinja 渲染，保留 processor 展开前的图像标记；不含图像数值。'
        else:
            model_path = Path(config['engine']['model'])
            code_path = Path(config['engine'].get('code_from') or model_path)
            convmod = module_at('exp6l_conversation_' + model, code_path / 'conversation.py')
            conv = convmod.get_conv_template(config['engine']['template'])
            question = '\n'.join(f'{label}\n<image>' for label in labels) + '\n' + prompt
            conv.append_message(conv.roles[0], question)
            conv.append_message(conv.roles[1], None)
            rendered = conv.get_prompt() + common['PREFIX']
            detail = '按实际 conversation.py 渲染，保留 inputs() 展开前的 <image>；每块展开为 256 个图像词元。'
        add('exp6j_' + '_'.join(key), rendered,
            f'来源 {path}；{B45}/exp6j_models.py；实验 exp6j {stage} {group} {model} {view} {packing}。'
            + detail + ' 已包含模型默认 system、角色边界和强制回答前缀。', parent)
        if stage == 'step2':
            sample_path = sorted(path.parent.glob('part-*.jsonl'))[0]
            with sample_path.open() as handle:
                sample = json.loads(next(handle))
            if model == 'qwen':
                counts = [t*h*w//4 for t,h,w in sample['image_grid_thw']]
                parts = rendered.split('<|image_pad|>')
                expanded = parts[0] + ''.join('<|image_pad|>'*n+tail for n,tail in zip(counts,parts[1:]))
            else:
                counts = [256*n for n in sample['num_patches_list']]
                parts = rendered.split('<image>')
                expanded = parts[0] + ''.join('<img>'+'<IMG_CONTEXT>'*n+'</img>'+tail for n,tail in zip(counts,parts[1:]))
            assert len(parts)-1 == len(counts)
            add('exp6j_' + '_'.join(key) + '_expanded', expanded,
                f'来源 {sample_path} 首条实际预测的图像 token 数 {counts}；'
                f'{B45}/exp6j_models.py；exp6j step2 {group} {model} {view} {packing}；'
                '图像占位已完整展开，图像数值单独输入。', parent)

    for variant, instruction in v2['INSTRUCTIONS'].items():
        text = v2['PROMPT'] + instruction
        add(f'exp6v2_{variant}', text, f'来源 {PROBE}/exp6v2_common.py PROMPT + INSTRUCTIONS[{variant}]；'
            'exp6v2 SECOND；a 仅答案，b 理由后答案，c 答案后理由（同文件的后续变体）。', 'exp3_sysu_second')
        add(f'exp6v2_{variant}_qwen_rendered', qwen_render(text, common['FRAMING'][:2],
            v2['MARKER'] if variant in ('a', 'c') else ''),
            f'来源 {PROBE}/exp6v2_common.py input_batch；Qwen3.5-4B/chat_template.jinja；'
            '图像标记为 processor 展开前形式；a/c 附加答案标记，b 不附加。', f'exp6v2_{variant}')
    notes = read(B49 / 'prompts/notes.json')
    for ds in ('sysu', 'second'):
        name = f'{ds}_sys_v1_en'
        add(name, (B49 / 'prompts' / f'{name}.txt').read_text().strip(),
            notes[name] + f' 来源 {B49}/prompts/{name}.txt；实验 exp6k→exp6l。',
            'exp6j_sysu_second_step2_near')
    return records


def register(db_path, records):
    store = module_at('exp6l_store', B49 / 'audit/verifier_ui/store.py')
    db = store.connect(db_path)
    ids = {}
    with db:
        for row in records:
            parent = ids.get(row['parent'])
            existing = db.execute('SELECT text FROM prompt_version WHERE name=?', (row['name'],)).fetchall()
            if any(r['text'] != row['text'] for r in existing):
                raise ValueError(f'Existing named version has different text: {row["name"]}')
            pid = store.add_prompt(db, row['name'], row['text'], parent_id=parent, note=row['note'])
            ids[row['name']] = pid
            old = db.execute('SELECT note FROM prompt_version WHERE id=?', (pid,)).fetchone()['note'] or ''
            note = old if row['note'] in old else '\n'.join(filter(None, [old, row['note']]))
            # add_prompt leaves existing rows unchanged; preserve text/name/creation time.
            db.execute('UPDATE prompt_version SET parent_id=?, note=? WHERE id=?', (parent, note, pid))
        for ds in ('sysu', 'second'):
            row = db.execute('SELECT p.text, q.text AS parent FROM prompt_version p '
                             'JOIN prompt_version q ON p.parent_id=q.id WHERE p.name=?',
                             (f'{ds}_sys_v1_en',)).fetchone()
            assert row is not None and row['parent'] == next(
                r['text'] for r in records if r['name'] == 'exp6j_sysu_second_step2_near')
    db.close()


def snapshot(path):
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: db.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--self-test', action='store_true', help='Create a fresh copy, register twice, and verify all tables.')
    p.add_argument('--source-db', type=Path, default=B49 / 'run/verifier.db')
    p.add_argument('--official', action='store_true', help='Write the shared verifier.db (Claude, after the copy self-test passed).')
    args = p.parse_args()
    target = args.db.resolve()
    if args.official:
        if target != args.source_db.resolve():
            p.error('--official writes only the shared verifier.db')
    elif not target.is_relative_to(HERE) or target == args.source_db.resolve():
        p.error('--db must be a copy inside batch51')
    records = collect()
    if args.self_test:
        if target.exists():
            p.error('Self-test requires a new target file')
        target.parent.mkdir(parents=True, exist_ok=True)
        before = snapshot(args.source_db)
        with sqlite3.connect(f'{args.source_db.resolve().as_uri()}?mode=ro', uri=True) as src:
            with sqlite3.connect(target) as dst:
                src.backup(dst)
    elif not target.is_file():
        p.error('--db must already exist; use --self-test to create the copy')
    register(target, records)
    if args.self_test:
        once = snapshot(target)
        register(target, records)
        assert once == snapshot(target), 'Registration is not idempotent'
        assert before == snapshot(args.source_db), 'Source database changed during verification'
        assert all(once[t] == rows for t, rows in before.items() if t != 'prompt_version')
        result = dict(source_unchanged=True, non_prompt_tables_unchanged=True,
                      repeat_unchanged=True, old_prompt_rows=len(before['prompt_version']),
                      new_prompt_rows=len(once['prompt_version']), registered_entries=len(records),
                      english_parent='exp6j_sysu_second_step2_near')
        (HERE / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False))
    (HERE / 'prompt_materials.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    print(f'Registered {len(records)} entries in {target.name}; materials: prompt_materials.json')


if __name__ == '__main__':
    main()
