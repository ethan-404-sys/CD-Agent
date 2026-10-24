"""Lightweight syntax, imports and file inventory checks; no training or pixel computation."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
os.environ['MPLCONFIGDIR']=str(HERE/'.matplotlib')
sys.path.insert(0,str(HERE))
import exp8s3_common as c


def main():
    started=time.monotonic()
    syntax=[]
    for path in sorted(HERE.glob('exp8s3_*.py')):
        ast.parse(path.read_text(),filename=str(path));syntax.append(path.name)
    subprocess.run(['bash','-n',str(HERE/'run_all.sh'),str(HERE/'exp8s3_job.sh'),str(HERE/'smoke.sh')],check=True)
    import torch
    import sklearn
    import matplotlib
    inventories={}
    for dataset in ('second','sysu'):
        protocol=c.read_json(c.root(dataset)/'protocol.json')
        images={s:protocol[s+'_images'] for s in ('train','val')}
        images['test']=c.b40.image_list(dataset)
        if set(images['train']) & set(images['val']):
            raise ValueError('Train/val overlap')
        checkpoint=c.root(dataset)/'models/full/model.pt'
        if not checkpoint.is_file(): raise FileNotFoundError(checkpoint)
        checked=1
        missing=[]
        for split,items in images.items():
            raw=c.raw_dataset(dataset,split)
            for name in items:
                stem=Path(name).stem
                paths=[c.pool_dir(dataset,split)/'U5_x4'/(stem+'.npz'),
                       c.pool_dir(dataset,split)/'images'/(stem+'.json')]
                if split != 'test':
                    paths.append(c.root(dataset)/'features'/split/(stem+'.npz'))
                if split != 'train':
                    paths.extend([c.root(dataset)/'evaluation'/split/'scores'/(stem+'.npz'),
                                  c.b40.base_dir(raw)/stem/'pixels.npz',c.b40.base_dir(raw)/stem/'image.json',
                                  c.OUT/'exp8s_batch34/interim'/raw/(stem+'.npz')])
                else:
                    pred=c.OUT/('preds' if dataset=='second' else 'exp8_batch19/preds')
                    detector='mambascd_base' if dataset=='second' else 'schanger_small'
                    paths.extend([pred/raw/detector/'prob'/(stem+'.npy'),
                                  c.DATASETS[raw]['root']/c.DATASETS[raw]['dirs'][2]/name])
                if dataset=='second':
                    paths.extend([c.DATASETS[raw][key]/name for key in ('class1','class2')])
                for path in paths:
                    checked+=1
                    if not path.is_file(): missing.append(str(path))
        inventories[dataset]=dict(images={k:len(v) for k,v in images.items()},features=len(protocol['features']),
                                 checked_files=checked,missing=missing)
    # Reading parameter shapes confirms the original saved M0 architecture, without inference.
    checkpoints={}
    for dataset in ('second','sysu'):
        z=torch.load(c.root(dataset)/'models/full/model.pt',map_location='cpu',weights_only=False)
        checkpoints[dataset]=dict(input_dim=z['input_dim'],mean_shape=list(z['mean'].shape),
                                  parameter_shapes={k:list(v.shape) for k,v in z['state_dict'].items()})
    versions={m.__name__:m.__version__ for m in (torch,sklearn,matplotlib)}
    if any(name.startswith(('transformers','torchvision','timm','sam3')) for name in sys.modules):
        raise RuntimeError('Unexpected visual model import during cache diagnostics')
    result=dict(syntax_files=syntax,shell_syntax='passed',imports=versions,inventory=inventories,
                frozen_m0_metadata=checkpoints,seconds=time.monotonic()-started,
                numerical_smoke='not executed: Slurm socket Operation not permitted; no management-node computation',
                git='not a Git repository',visual_model_imports=False)
    c.write_json(HERE/'verification.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if any(v['missing'] for v in inventories.values()):
        raise FileNotFoundError('Required input files missing; see verification.json')

if __name__=='__main__':
    main()
