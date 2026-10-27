"""登记本地审核库并输出可直接供共享库读取的清单；不改前端。"""
from exp8s8_icl_common import *
from exp6k_store import composition
from contextlib import closing


def writedb(a):
    sys.path.insert(0, str(HERE.parent / 'batch49/audit/verifier_ui'))
    import store
    collections = {kind: read(manifest_path(a, kind)) for kind in a.sets}
    if not set(collections) <= {'demos', 'dev', 'test'}:
        raise ValueError('只可登记 demos/dev/test')
    validate_sets(collections, files=True)
    splitdoc = read(a.out / a.dataset / 'icl_split.json')
    if not a.limit and not splitdoc['finalized']:
        raise ValueError('正式登记前需完成负例标注')
    if set(splitdoc['demo_images']) != {r['image'] for r in read(manifest_path(a, 'demos'))}:
        raise ValueError('示例图清单不一致')
    for kind in ('dev', 'test'):
        if kind in collections:
            sample_doc = read(a.out / a.dataset / f'{kind}_sampling.json')
            if sample_doc['demo_images'] != splitdoc['demo_images'] or sample_doc['limit'] != a.limit:
                raise ValueError('抽样清单已过期，需在最终示例确定后重跑sample')
    prompts = []
    if not a.samples_only:
        notes = read(a.prompts / 'notes.json')
        for lang in ('zh', 'en'):
            name = f'{a.dataset}_sys_v1_{lang}'
            content = (a.prompts / f'{name}.txt').read_text().strip()
            if not content:
                raise ValueError(f'提示词为空: {name}')
            note = notes[name]
            prompts.append((name, content, note if isinstance(note, str) else json.dumps(note, ensure_ascii=False)))
    if a.samples_only and not a.limit:
        raise ValueError('正式写库必须读入提示词，samples-only仅限冒烟')
    shared = (HERE.parent / 'batch49/run/verifier.db').resolve()
    path = (a.db or (a.out / 'verifier.db' if a.limit else shared)).resolve()
    if path == shared:
        if a.limit:
            raise ValueError('冒烟不能写共享审核库')
    else:
        path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(store.connect(path)) as db:
        with db:
            # 对已存在的本实验集合做跨角色互斥核查；同名更新会替换成员。
            existing = db.execute("SELECT ss.name, ss.purpose, s.image FROM sample_set ss JOIN sample_set_item i "
                                  "ON ss.id=i.set_id JOIN sample s ON s.id=i.sample_id WHERE ss.dataset=? AND ss.name LIKE 'exp8s8_%'",
                                  (a.dataset,)).fetchall()
            incoming_names = {f'exp8s8_{k}_{a.dataset}' for k in collections}
            for kind, rows in collections.items():
                purpose = 'demo_pool' if kind=='demos' else kind
                images = {r['image'] for r in rows}
                if any(r['name'] not in incoming_names and r['purpose'] != purpose and r['image'] in images for r in existing):
                    raise ValueError('库中已有其他角色集合与本次图像重叠')
                name = f'exp8s8_{kind}_{a.dataset}'
                set_id = store.add_sample_set(db, name, purpose, a.dataset, rows, note=demo_note(a.dataset))
                desired = {str(r['cid']) for r in rows}
                members = db.execute('SELECT s.cid,s.id FROM sample_set_item i JOIN sample s ON s.id=i.sample_id WHERE i.set_id=?', (set_id,)).fetchall()
                db.executemany('DELETE FROM sample_set_item WHERE set_id=? AND sample_id=?',
                               [(set_id, r['id']) for r in members if r['cid'] not in desired])
            # 使用可读版本ID，保留既有版本和审核记录；兼容store的TEXT主键。
            for name, content, note in prompts:
                old = db.execute('SELECT id FROM prompt_version WHERE name=? AND text=?', (name, content)).fetchone()
                if old is None:
                    revision = db.execute('SELECT COUNT(*) FROM prompt_version WHERE name=?', (name,)).fetchone()[0]+1
                    pid = f'exp8s8_{name}_r{revision}'
                    db.execute('INSERT INTO prompt_version(id,name,text,parent_id,note,created_at) VALUES(?,?,?,?,?,?)',
                               (pid, name, content, None, note, store.now()))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_cache'").fetchone():
                db.execute('DELETE FROM metric_cache')
        actual = {f'exp8s8_{k}_{a.dataset}': db.execute('SELECT COUNT(*) FROM sample_set_item WHERE set_id=(SELECT id FROM sample_set WHERE name=?)',
                  (f'exp8s8_{k}_{a.dataset}',)).fetchone()[0] for k in collections}
        if any(actual[f'exp8s8_{k}_{a.dataset}'] != len(v) for k, v in collections.items()):
            raise ValueError('写库后集合数量不一致')
    write(a.out / a.dataset / 'database_receipt.json', dict(path=str(path), shared_database=path==shared, limit=a.limit,
          sets=actual, prompts=len(prompts), finalized=splitdoc['finalized'],
          warning='前端wrong_split告警预期存在；示例保留真实val/test来源，例外说明在集合note及样本meta'))
    print(f'{path}: {actual}, 提示词 {len(prompts)}', flush=True)


def summary(a):
    stats = {}
    for ds in ('levir', 'whu'):
        for kind in ('shortlist', 'demos', 'dev', 'test'):
            path = a.out / f'exp8s8_{kind}_{ds}.json'
            if path.exists():
                stats[f'exp8s8_{kind}_{ds}'] = composition(read(path))
    write(a.out / 'icl_stats.json', dict(limit=a.limit, partial=bool(a.limit), sets=stats))
    lines = ['# ICL 构成', '', '冒烟数据' if a.limit else '正式候选材料；负例审核状态见selection.json。', '',
             '| 集合 | 总数 | 正 | 负 | 图数 | 每图最多 | 难负 |', '|---|---:|---:|---:|---:|---:|---:|']
    for name, row in stats.items():
        lines.append(f"| {name} | {row['n']} | {row['label'].get('1',0)} | {row['label'].get('0',0)} | {row['images']} | {row['max_per_image']} | {row['hard_demo']} |")
    local(a.out / 'icl_stats.md').write_text('\n'.join(lines)+'\n')
