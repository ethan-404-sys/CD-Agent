"""Final Slurm-only notification, including successful report paths or failed job states."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();out=args.out.resolve();here=Path(__file__).resolve().parent
    if not out.is_relative_to(here) or out == here:
        raise ValueError('Output must be within batch43')
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Notification must run in the Slurm finalizer')
    with (out/'logs/accounting.txt').open() as h:
        rows=list(csv.DictReader(h,delimiter='|'))
    failures=[r for r in rows if r['State'] != 'COMPLETED' and '.' not in r['JobID']]
    paths=[out/d/'tables.md' for d in ('second','sysu')]
    complete=all(p.exists() for p in paths)
    if complete:
        (out/'tables.md').write_text('# 实验 8 补充 3\n\n' + '\n\n'.join(
            f'## {ds.upper()}\n\n' + (out/ds/'tables.md').read_text() for ds in ('second','sysu')))
    success=complete and not failures
    status='success' if success else 'failed'
    details=[]
    for ds in ('second','sysu'):
        path=out/ds/'summary.json'
        if path.exists():
            data=json.loads(path.read_text())
            point=data['evaluation']['test']['methods']['M1']['0.5']
            details.append(f'{ds}: M1 test deltaF1={point["delta_f1"]}, cell2 recall={point["recall2"]}')
    if failures:
        details.append('failed jobs: '+', '.join(f'{r["JobID"]} {r["State"]} elapsed={r["Elapsed"]}' for r in failures))
    elapsed=[r['Elapsed'] for r in rows if '.' not in r['JobID']]
    body=f'status: {status}; task: batch43 exp8s3; '+ '; '.join(details)+f'; job elapsed times: {", ".join(elapsed)}; output: {out}; logs: {out / "logs"}'
    env=dict(os.environ,QQ_SUB=f'batch43 exp8s3 {status}',QQ_BODY=body)
    subprocess.run(['/guisongxia01/pangchao/wangyihan/.local/bin/wangyihan-send-qq'],env=env,check=True)
    if not success:
        raise RuntimeError('One or more pipeline jobs failed; see logs/accounting.txt')

if __name__ == '__main__':
    main()
