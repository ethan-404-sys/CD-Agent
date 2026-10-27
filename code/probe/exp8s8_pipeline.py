"""单CPU阶段编排：prepare材料；finish在真实标注完成后定稿；smoke仅小范围。"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import runpy

HERE = Path(__file__).resolve().parent
CPU = Path('/guisongxia01/pangchao/wangyihan/conda_envs/exp5-env/bin/python')
STORE = Path('/guisongxia01/pangchao/wangyihan/conda_envs/exp6-v2-env/bin/python')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('prepare','finish','smoke'))
    p.add_argument('--out', type=Path, default=HERE / 'run')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--db', type=Path)
    a = p.parse_args()
    if a.phase=='smoke' and not 1 <= a.limit <= 3:
        p.error('smoke需显式 --limit 1至3')
    if not os.environ.get('SLURM_JOB_ID') and a.phase!='smoke':
        p.error('正式阶段通过sbatch提交')
    if a.phase!='smoke' and a.limit:
        p.error('prepare/finish是完整阶段；小范围使用smoke')
    a.out = a.out.resolve()
    if not a.out.is_relative_to(HERE) or (a.limit and a.out==HERE/'run'):
        p.error('输出须在batch50内；冒烟需独立目录')
    base = ['--out', str(a.out), '--limit', str(a.limit)]
    def run(module, stage, ds='levir', split='val', *extra, store=False):
        cmd = [str(STORE if store else CPU), '-B', str(HERE/f'exp8s8_{module}.py'), stage,
               '--dataset', ds, '--split', split, *base, *extra]
        print('RUN', ' '.join(cmd), flush=True)
        if store:
            subprocess.run(cmd, cwd=HERE, check=True)
        else:
            # 同一CPU解释器复用已加载依赖，减少共享文件系统上的重复导入。
            previous = sys.argv
            sys.argv = cmd[2:]
            try:
                runpy.run_path(cmd[2], run_name='__main__')
            finally:
                sys.argv = previous
    scopes = [('levir','val'),('levir','test'),('whu','test')]
    if a.phase in ('prepare','smoke'):
        for ds, split in scopes:
            run('cells','all',ds,split)
            run('filter','all',ds,split)
            if (ds,split)==('levir','val'):
                run('filter','select',ds,split)
            run('units','all',ds,split)
            run('icl','table',ds,split)
        for ds, split in [('levir','val'),('whu','test')]:
            run('icl','select',ds,split)
            run('icl','render',ds,split,'--sets','shortlist')
            run('icl','sheets',ds,split)
    if a.phase in ('finish','smoke'):
        for ds, split in [('levir','val'),('whu','test')]:
            if a.phase=='finish':
                run('icl','finalize',ds,split)
            run('icl','sample',ds,split)
            sets = ['demos','dev','test'] if ds=='levir' else ['demos','test']
            run('icl','render',ds,split,'--sets',*sets)
        for ds, split in scopes:
            target = a.out/ds/f'rank_{split}.json'
            if not target.exists():
                run('rank','run',ds,split,'--bootstrap','50' if a.limit else '2000')
        for ds, split in [('levir','val'),('whu','test')]:
            extra = ['--db',str(a.db)] if a.db else []
            run('icl','writedb',ds,split,*extra,store=True)
        run('verify','run')
    run('icl','summary')
    run('report','run')
    if a.phase=='prepare':
        print('材料已生成，annotations_levir.csv / annotations_whu.csv 待真实看图标注；之后运行finish。',flush=True)


if __name__=='__main__':
    main()
