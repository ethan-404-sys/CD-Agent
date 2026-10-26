"""Static local inventory or GPU load/timing/multi-image probes (run GPU mode in Slurm)."""
from __future__ import annotations
import argparse
import importlib.metadata
import time
from exp6j_common import *


def inventory():
    result = {}
    for name, path in MODEL_PATHS.items():
        config = read_json(path / 'config.json')
        result[name] = dict(path=str(path), architecture=config['architectures'],
            model_type=config['model_type'], template=config.get('template'),
            language_model=config.get('llm_config', {}).get('architectures'),
            modeling_files=[p.name for p in sorted(path.glob('*.py'))],
            local_card=(path / 'README.md').exists(),
            multi_image_evidence=('DA-RS chat num_patches_list supports separate images' if name.startswith('dars')
                else 'Qwen processor supports image lists' if name == 'qwen'
                else 'Unverified: local inference.py accepts one image only; DA-RS architecture adapter requires GPU probe'),
            change_training_evidence=('Local files do not document training change tasks or a recommended change prompt'
                                      if name == 'earthdial' else 'Not independently verified here'),
            code_from=str(MODEL_PATHS['dars4b']) if name == 'earthdial' else None)
    environments = {}
    for env in ('qwen3vl-sft-env', 'omniovcd-env', 'vlm-vs-onejev-env', 'exp5-env'):
        root = WORKSPACE / 'conda_envs' / env
        sites = list(root.glob('lib/python*/site-packages'))
        packages = {}
        for site in sites:
            for dist in importlib.metadata.distributions(path=[str(site)]):
                name = dist.metadata['Name']
                if name and name.lower() in ('torch', 'torchvision', 'transformers', 'timm', 'einops',
                                             'numpy', 'pillow', 'scipy', 'scikit-learn', 'sentencepiece'):
                    packages[name] = dist.version
        environments[env] = dict(python=str(root / 'bin/python'), packages=packages)
    exp3 = {d: len(samples(RUN, 'step1', d)) for d in ('levir', 'whu', 'sysu', 'second')}
    return dict(models=result, environments=environments, exp3_counts=exp3,
                measured_gpu=False, earthdial_native_multi_image='not established by local evidence')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', choices=MODEL_PATHS)
    p.add_argument('--out', type=Path, default=RUN)
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--packing', choices=('multi', 'sheet', 'both'), default='both')
    args = p.parse_args()
    out = local(args.out) / 'step0'
    if not args.model:
        write_json(out / 'inventory.json', inventory())
        print(out / 'inventory.json')
        return
    if args.limit < 1:
        p.error('--limit must be >= 1')
    from exp6j_models import load_model
    import torch
    if torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported():
        raise RuntimeError('Allocate exactly one bf16-capable GPU')
    start = time.perf_counter()
    engine = load_model(args.model)
    torch.cuda.synchronize()
    report = dict(model=args.model, engine=engine.metadata,
                  gpu=torch.cuda.get_device_name(0), load_seconds=time.perf_counter()-start, probes=[])
    rows = samples(args.out, 'step1', 'levir')[:args.limit]
    modes = ('multi', 'sheet') if args.packing == 'both' else (args.packing,)
    for packing in modes:
        for row in rows:
            torch.cuda.reset_peak_memory_stats()
            start = time.perf_counter()
            result = engine.score(input_images(row, 'near', packing), prompt_for('levir', 'step1', 'near'))
            report['probes'].append(dict(cid=row['cid'], packing=packing, **result,
                   seconds=time.perf_counter()-start, peak_gib=engine.peak_memory()))
            write_json(out / f'{args.model}.json', report)
    report['multi_image_forward_verified'] = 'multi' in modes
    report['native_training_support_verified'] = False if args.model == 'earthdial' else None
    write_json(out / f'{args.model}.json', report)

if __name__ == '__main__':
    main()
