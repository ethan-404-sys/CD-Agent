"""ICL 清单格式复用 exp6k，保留真实 val/test 划分。"""
from exp8s8_common import *
import exp6k_common as k49
from collections import Counter

k49.local = local
k49.HERE = HERE
write_csv, read_csv = k49.write_csv, k49.read_csv
area_bin, load_crop_functions = k49.area_bin, k49.load_crop_functions


def manifest_path(a, kind):
    return a.out / f'exp8s8_{kind}_{a.dataset}.json'


def table_path(a, split=None):
    return a.out / a.dataset / f'table_{split or a.split}.csv'


def table_rows(a, split=None):
    path = table_path(a, split)
    if read(path.with_suffix('.json'))['limit'] != a.limit:
        raise ValueError('table 范围不匹配')
    return read_csv(path)


def rgb_pair(ds, split, name):
    return [load_rgb(raw_dataset(ds, split), name, view) for view in ('t1', 't2')]


def inputs_for(out, row):
    folder = local(out / row['dataset'] / 'render' / row['split'])
    stem = f"{Path(row['image']).stem}_{row['unit_id']}"
    keys = ['t1', 't2', 'thumb']
    if row['role'] == 'demo':
        keys += ['full_t1', 'full_t2', 'mask']
    return {k: str(folder / f'{stem}_{k}.jpg') for k in keys}


def sample_row(out, row, role='query'):
    result = dict(row, role=role, rationale='', pseudo_type='', weight=1.0)
    if role != 'demo':
        for key in list(result):
            if key.startswith('semantic_') or key in ('change_kind', 'reason_evidence'):
                del result[key]
        result.update(change_kind='', semantic_text='')
    result['inputs'] = inputs_for(out, result)
    return result


def key(row):
    return row['image'], row['cid']


def demo_note(ds):
    return ('经 Ethan 批准，WHU 示例取自测试集，示例图已从测试评估剔除；可能有相邻切块泄漏。'
            if ds == 'whu' else '经任务明确授权，LEVIR 示例取自验证集，示例图已从开发集剔除。')


def validate_sets(collections, files=False):
    owners = {}
    for kind, rows in collections.items():
        seen = set()
        for row in rows:
            expected = ('val' if row['dataset'] == 'levir' else 'test') if kind == 'demos' else 'val' if kind == 'dev' else 'test'
            identity = row['dataset'], row['split'], row['cid']
            if identity in seen or row['split'] != expected:
                raise ValueError(f'{kind}: 候选重复或原始划分错误')
            seen.add(identity)
            image = row['dataset'], row['image']
            if image in owners and owners[image] != kind:
                raise ValueError(f'{image}: 示例/开发/测试图重叠')
            owners[image] = kind
            required = {'t1', 't2', 'thumb'}
            if kind == 'demos':
                required |= {'full_t1', 'full_t2', 'mask'}
            elif row.get('semantic_text') or row.get('change_kind') or {'mask', 'sem1', 'sem2'} & row['inputs'].keys():
                raise ValueError('查询清单携带真值材料')
            if not required <= row['inputs'].keys():
                raise ValueError('渲染路径缺项')
            if files:
                for path in row['inputs'].values():
                    if not Path(path).is_file():
                        raise FileNotFoundError(path)
        if kind == 'demos':
            cap = 4 if rows and rows[0]['dataset'] == 'levir' else 2
            if max(Counter(r['image'] for r in rows).values(), default=0) > cap:
                raise ValueError('示例超过同图上限')
