"""审核库登记和中文构成报告；不修改前端接口。"""
from exp6k_common import *
from collections import Counter
from contextlib import closing


def check_sets(collections, check_files=False):
    image_splits = {}
    for kind, rows in collections.items():
        expected = 'train' if kind in ('demos', 'demos256', 'shortlist', 'extra') else 'val' if kind == 'dev' else 'test'
        identities = set()
        for row in rows:
            identity = row['dataset'], row['split'], row['cid']
            if row['split'] != expected or identity in identities:
                raise ValueError(f'{kind}: 划分不匹配或候选重复')
            identities.add(identity)
            image = row['dataset'], row['image']
            if image in image_splits and image_splits[image] != expected:
                raise ValueError(f'{image}: train/val/test 按图重叠')
            image_splits[image] = expected
            if expected != 'train' and (row.get('semantic_text') or row.get('change_kind') or
                                        {'mask', 'sem1', 'sem2'} & row['inputs'].keys()):
                raise ValueError('查询携带真值语义或掩码')
            if check_files:
                required = {'t1', 't2', 'full_t1', 'full_t2', 'thumb'}
                if expected == 'train':
                    required.add('mask')
                    if row['dataset'] == 'second':
                        required.update(('sem1', 'sem2'))
                if not required <= row['inputs'].keys():
                    raise ValueError('渲染路径缺项')
                for path in row['inputs'].values():
                    if not Path(path).is_file():
                        raise FileNotFoundError(path)
        if expected == 'train' and max(Counter(r['image'] for r in rows).values(), default=0) > 2:
            raise ValueError(f'{kind}: 每图超过两个示例')


def writedb(a):
    sys.path.insert(0, str(HERE / 'audit/verifier_ui'))
    import store
    collections = {kind: read(manifest_path(a, kind)) for kind in a.sets}
    if not set(collections) <= {'demos', 'demos256', 'dev', 'test'}:
        raise ValueError('只可登记 demos、demos256、dev、test')
    check_sets(collections, check_files=True)
    prompts = []
    if not a.samples_only:
        notes = read(a.prompts / 'notes.json')
        for lang in ('zh', 'en'):
            name = f'{a.dataset}_sys_v1_{lang}'
            content = (a.prompts / f'{name}.txt').read_text(encoding='utf-8').strip()
            if not content:
                raise ValueError(f'提示词为空: {name}')
            note = notes[name]
            prompts.append((name, content, note if isinstance(note, str) else json.dumps(note, ensure_ascii=False)))
    path = local(a.db or a.out / 'verifier.db')
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(store.connect(path)) as db:
        with db:
            # 先检查现有集合，避免分次登记产生跨划分重叠。
            for kind, rows in collections.items():
                for row in rows:
                    existing = db.execute('SELECT DISTINCT split FROM sample WHERE dataset=? AND image=?',
                                          (row['dataset'], row['image'])).fetchall()
                    if any(x['split'] != row['split'] for x in existing):
                        raise ValueError(f"数据库已有同图其他划分: {row['image']}")
                name = f'exp6k_{kind}_{a.dataset}'
                purpose = 'demo_pool' if kind.startswith('demos') else kind
                set_id = store.add_sample_set(db, name, purpose, a.dataset, rows)
                # store 保留审核；同时删除本次集合已移除的成员，防止旧候选累积。
                desired = {str(r['cid']) for r in rows}
                members = db.execute('SELECT s.cid, s.id FROM sample_set_item i JOIN sample s ON s.id=i.sample_id WHERE i.set_id=?', (set_id,)).fetchall()
                db.executemany('DELETE FROM sample_set_item WHERE set_id=? AND sample_id=?',
                               [(set_id, r['id']) for r in members if r['cid'] not in desired])
            for prompt in prompts:
                store.add_prompt(db, prompt[0], prompt[1], note=prompt[2])
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metric_cache'").fetchone():
                db.execute('DELETE FROM metric_cache')
    print(f'{path}: {len(collections)} 集合, {len(prompts)} 提示词', flush=True)


def composition(rows):
    fields = ['label', 'stratum', 'majority', 'area_bin', 'source', 'change_kind', 'pseudo_type']
    result = {field: dict(Counter(str(r.get(field) or '未填') if field not in ('label', 'majority') else str(r[field]) for r in rows)) for field in fields}
    result['sources'] = dict(Counter(s for r in rows for s in r['sources']))
    result.update(n=len(rows), images=len({r['image'] for r in rows}),
                  max_per_image=max(Counter(r['image'] for r in rows).values(), default=0),
                  hard_demo=sum(r.get('hard_demo', False) for r in rows),
                  weighted_n=sum(r['weight'] for r in rows))
    return result


def summary(a):
    stats = {}
    for ds in ('second', 'sysu'):
        collections = {}
        for kind in ('shortlist', 'extra', 'demos', 'demos256', 'dev', 'test'):
            path = a.out / f'exp6k_{kind}_{ds}.json'
            if path.exists():
                collections[kind] = read(path)
        check_sets(collections)
        for kind, rows in collections.items():
            stats[f'exp6k_{kind}_{ds}'] = composition(rows)
    if not stats:
        raise ValueError('没有可统计的集合')
    write(a.out / 'exp6k_stats.json', stats)
    lines = ['# exp6k 样本构成', '', '| 集合 | 总数 | 正 | 负 | 图数 | 每图最多 | 难负（示例定义） |', '|---|---:|---:|---:|---:|---:|---:|']
    for name, data in stats.items():
        lines.append(f"| {name} | {data['n']} | {data['label'].get('1', 0)} | {data['label'].get('0', 0)} | {data['images']} | {data['max_per_image']} | {data['hard_demo']} |")
    for name, data in stats.items():
        lines += ['', f'## {name}', '', '| 维度 | 类别 | 数量 |', '|---|---|---:|']
        for field in ('stratum', 'majority', 'area_bin', 'sources', 'change_kind', 'pseudo_type'):
            for category, n in sorted(data[field].items()):
                lines.append(f'| {field} | {category} | {n} |')
    lines += ['', '缺少的集合尚未生成，不计为已完成。SECOND 第 6 类为运动场（playground），非植被地表包括不透水面与裸地；0 表示无变化、未提供地类。', '']
    local(a.out / 'exp6k_stats.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'{a.out}/exp6k_stats.json + .md', flush=True)
