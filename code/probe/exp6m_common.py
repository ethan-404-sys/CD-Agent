"""实验 6m 的本地文件、只读上游接口和输入约定。"""
from __future__ import annotations
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
WORKSPACE = Path('/guisongxia01/pangchao/wangyihan')
B48, B49, B51 = [HERE.parent / f'batch{n}' for n in (48, 49, 51)]
MODEL = WORKSPACE / 'models/Qwen3.5-4B'
DB = B49 / 'run/verifier.db'
SEED, BOOT_SEED = 20261028, 20261026
STRATA = ('pos2', 'pos_other', 'hard_neg', 'rand_neg')
FUSE = {'sysu': 'fuse-img-a0.5-s1020', 'second': 'fuse-img-a0.75-s1020'}
# exp6l Z1 FORMAT 中的判定问题逐字保留；输出约束改成单词答案。
QUESTION = ('Judge whether at least half of the red-outlined candidate pixels are real change. '
            'Answer with exactly one word: yes or no.')
sys.dont_write_bytecode = True
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'):
    os.environ[key] = '1'
for key in ('HF_HOME', 'HF_MODULES_CACHE', 'MPLCONFIGDIR', 'XDG_CACHE_HOME'):
    os.environ[key] = str(HERE / '.cache' / key.lower())


def local(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError(f'输出必须在 {HERE} 内: {path}')
    return path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, obj):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def rows(path):
    path = Path(path)
    if path.suffix == '.json':
        result = read(path)
    else:
        result = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not isinstance(result, list):
        raise ValueError(f'清单必须为数组或 JSONL: {path}')
    return result


def write_rows(path, data):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        for row in data:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    temp.replace(path)


def append(path, row):
    with local(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def journal(path):
    """仅修复中断留下的末尾半行；完整记录中的格式错误直接失败。"""
    path = local(path)
    if not path.exists():
        return []
    with path.open('rb+') as stream:
        data = stream.read()
        if data and not data.endswith(b'\n'):
            end = data.rfind(b'\n') + 1
            try:
                json.loads(data[end:])
            except (json.JSONDecodeError, UnicodeDecodeError):
                stream.seek(end)
                stream.truncate()
            else:
                stream.write(b'\n')
    return rows(path)


@contextmanager
def lock(path, blocking=True):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def now():
    return datetime.now(timezone.utc).isoformat()


def readonly(path=DB):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def prompt(dataset):
    name = dataset + '_sys_v1_en'
    with closing(readonly()) as db:
        found = db.execute('SELECT text FROM prompt_version WHERE name=?', (name,)).fetchall()
    if len(found) != 1:
        raise ValueError(f'口径提示不唯一: {name}')
    return name, found[0]['text']


def upstream_data():
    sys.path.insert(0, str(B49))
    import exp6k_common
    return exp6k_common


def upstream_eval():
    sys.path.insert(0, str(B51))
    import exp6l_eval
    return exp6l_eval


def sample_key(row):
    return row['dataset'], row['split'], row['image'], int(row['unit_id'])


def validate_manifest(data, dataset=None, split=None, images=False):
    if not data or len({sample_key(r) for r in data}) != len(data):
        raise ValueError('清单为空或重复')
    for row in data:
        if dataset is not None and row['dataset'] != dataset:
            raise ValueError('清单数据集不符')
        if split is not None and row['split'] != split:
            raise ValueError('清单划分不符')
        if row['label'] not in (0, 1) or row.get('weight', 1) <= 0:
            raise ValueError('清单标签或权重无效')
        if images:
            for key in ('t1', 't2'):
                if not Path(row['inputs'][key]).is_file():
                    raise FileNotFoundError(row['inputs'][key])


def require_slurm():
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('训练、模型推理与完整自助法评估须在 Slurm 内执行')


def claim_final(config, out):
    path = HERE / 'run/final_registry.json'
    with lock(path.with_suffix('.lock')):
        registry = read(path) if path.exists() else {}
        entry = dict(out=str(local(out)), config=config)
        key = config['dataset']
        if key in registry and registry[key] != entry:
            raise ValueError('该数据集已登记最终测试，只能续跑原目录与原方案')
        registry[key] = entry
        write(path, registry)
