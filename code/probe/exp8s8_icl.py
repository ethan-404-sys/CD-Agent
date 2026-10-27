"""ICL：table/select/render/sheets/finalize/sample/writedb/summary。"""
from exp8s8_icl_common import *
import time


def main():
    p = parser(__doc__, ('table', 'select', 'render', 'sheets', 'finalize', 'sample', 'writedb', 'summary'))
    p.add_argument('--action', choices=('build', 'merge', 'all'), default='all')
    p.add_argument('--sets', nargs='+', choices=('shortlist', 'demos', 'dev', 'test'))
    p.add_argument('--annotations', type=Path)
    p.add_argument('--font', type=Path, default=Path('/persist_data/home/guisongxia/.local/share/fonts/SimHei.ttf'))
    p.add_argument('--db', type=Path)
    p.add_argument('--prompts', type=Path, default=HERE / 'prompts')
    p.add_argument('--samples-only', action='store_true')
    a = setup(p)
    started = time.monotonic()
    if a.stage == 'table':
        from exp8s8_icl_table import build, merge
        if a.action=='all' and a.shards != 1:
            p.error('table多分片使用build再merge')
        if a.action in ('build', 'all'): build(a)
        if a.action in ('merge', 'all'): merge(a)
    elif a.stage in ('select', 'finalize', 'sample'):
        import exp8s8_icl_select as module
        getattr(module, a.stage)(a)
    elif a.stage in ('render', 'sheets'):
        import exp8s8_icl_render as module
        a.sets = a.sets or ['shortlist']
        getattr(module, a.stage)(a)
    else:
        import exp8s8_icl_store as module
        a.sets = a.sets or (['demos', 'dev', 'test'] if a.dataset=='levir' else ['demos', 'test'])
        getattr(module, a.stage)(a)
    finish(a, 'icl-'+a.stage+'-'+a.action, started)


if __name__ == '__main__':
    main()
