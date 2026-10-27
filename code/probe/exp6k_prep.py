#!/usr/bin/env python3
"""exp6k：缓存表、ICL 候选、渲染、分层样本及审核库准备。"""
from exp6k_common import *
import argparse
import resource
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('table', 'shortlist', 'render', 'sheets', 'finalize', 'sample', 'writedb', 'summary'))
    p.add_argument('--dataset', choices=('second', 'sysu'), required=True)
    p.add_argument('--out', type=Path, default=HERE / 'run')
    p.add_argument('--limit', type=int, default=0, help='冒烟：每划分前 N 张图，shortlist 每类及 sample 每层最多 N 个；各阶段保持一致')
    p.add_argument('--split', choices=('train', 'val', 'test'), default='train')
    p.add_argument('--action', choices=('build', 'merge', 'all'), default='all')
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard', type=int, default=0)
    p.add_argument('--sets', nargs='+', choices=('shortlist', 'extra', 'demos', 'demos256', 'dev', 'test'))
    p.add_argument('--annotations', type=Path)
    p.add_argument('--font', type=Path, default=Path('/persist_data/home/guisongxia/.local/share/fonts/SimHei.ttf'))
    p.add_argument('--db', type=Path)
    p.add_argument('--prompts', type=Path, default=HERE / 'prompts')
    p.add_argument('--samples-only', action='store_true', help='只登记样本，不读尚未完成的提示词')
    a = p.parse_args()
    a.out = local(a.out)
    if a.limit < 0 or not 0 <= a.shard < a.shards:
        p.error('需要 limit >= 0 且 0 <= shard < shards')
    if a.limit and a.out == HERE / 'run':
        p.error('--limit 冒烟请使用独立 --out，不能写正式 run/')
    if not os.environ.get('SLURM_JOB_ID') and not a.limit:
        p.error('正式计算请通过 sbatch；登录节点仅允许 --limit 小冒烟')
    if a.stage == 'table' and a.action == 'all' and a.shards != 1:
        p.error('多分片 table 使用 build，完成后单独 merge')
    a.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if a.stage == 'table':
        from exp6k_table import build, merge
        if a.action in ('build', 'all'):
            build(a)
        if a.action in ('merge', 'all'):
            merge(a)
    elif a.stage in ('shortlist', 'sample', 'finalize'):
        import exp6k_select
        getattr(exp6k_select, a.stage)(a)
    elif a.stage in ('render', 'sheets'):
        import exp6k_render
        a.sets = a.sets or ['shortlist', 'extra', 'dev', 'test']
        getattr(exp6k_render, a.stage)(a)
    else:
        import exp6k_store
        a.sets = a.sets or ['demos', 'demos256', 'dev', 'test']
        getattr(exp6k_store, a.stage)(a)
    if 'torch' in sys.modules:
        raise RuntimeError('本任务禁止加载 torch')
    timing = dict(stage=a.stage, dataset=a.dataset, split=a.split, limit=a.limit,
                  seconds=time.monotonic()-started, peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                  torch_loaded=False)
    write(a.out / a.dataset / 'timings' / f'{a.stage}-{a.split}-{a.action}-{a.shard:03d}.json', timing)
    print(json.dumps(timing, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
