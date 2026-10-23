"""实验8补充第1、2步入口；所有实验计算在 CPU 作业内执行。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
# matplotlib 等依赖的缓存也放在指定输出内。


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'reference', 'calibrate', 'prepare', 'evaluate', 'all'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--legacy-exp8', action='store_true', help='只执行原 exp8 候选过滤和来源组合的一致性核对')
    parser.add_argument('--datasets', help='逗号分隔划分；默认全部六个，SYSU验证/测试须成对')
    parser.add_argument('--reference-from', type=Path, help='只读复用已全量通过的 reference.json')
    parser.add_argument('--shard', type=int, default=0, help='prepare分片编号，从0开始')
    parser.add_argument('--shards', type=int, default=1, help='prepare分片总数，默认1')
    parser.add_argument('--finalize', action='store_true', help='prepare只检查全部完成记录并汇总manifest')
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error('require --shards >= 1 and 0 <= --shard < --shards')
    if args.action != 'prepare' and (args.shard != 0 or args.shards != 1 or args.finalize):
        parser.error('--shard/--shards/--finalize are only supported for prepare')
    if args.finalize and args.shard != 0:
        parser.error('prepare --finalize checks all images; use --shard 0 (default)')
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    if args.legacy_exp8 and args.action not in ('reference', 'all'):
        parser.error('--legacy-exp8 requires reference or all')
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(args.out / '.matplotlib')
    from exp8s_common import VERSION, DATASETS, read_json, write_json
    if args.datasets is None:
        args.datasets = DATASETS
    else:
        requested = [name.strip() for name in args.datasets.split(',')]
        if not requested or any(name not in DATASETS for name in requested):
            parser.error('--datasets must contain only: ' + ','.join(DATASETS))
        if len(set(requested)) != len(requested):
            parser.error('--datasets must not contain duplicates')
        if ('sysu' in requested) != ('sysu_val' in requested):
            parser.error('--datasets requires sysu_val,sysu together')
        if 'second' in requested and 'second_cv' not in requested:
            parser.error('--datasets second requires second_cv for calibration and source order')
        args.datasets = tuple(ds for ds in DATASETS if ds in requested)
    if args.reference_from is not None:
        args.reference_from = args.reference_from.resolve()
    from exp8s_prepare import preflight, reference, calibrate, prepare, require_reference
    protocol = dict(version=VERSION, limit=args.limit,
                    oracle='strict added precision > current split micro F1/2',
                    exp8_oracle='added precision >= 0.5; legacy reference uses whole candidates',
                    dedup='unmodified direct_pairs, within source then across sources',
                    c2='validation q80; WHU transfers LEVIR val; missing LEVIR val SAM3 keeps all C2',
                    test='descriptive sweeps requested by TASK; no test selection')
    # 缺省运行保留旧协议结构，兼容正在运行的正式作业及其输出。
    if args.datasets != DATASETS:
        protocol['datasets'] = list(args.datasets)
    if args.reference_from is not None:
        protocol['reference_from'] = str(args.reference_from)
    path = args.out / 'protocol.json'
    if path.exists() and read_json(path) != protocol:
        raise ValueError('Output already contains a different protocol; choose a fresh directory')
    if args.action == 'prepare' and (args.shards != 1 or args.finalize):
        # 并行分片和finalize只读取串行preflight/calibrate建立的共享协议。
        if not path.is_file():
            raise FileNotFoundError(f'{path}: run preflight and calibrate before sharded prepare/finalize')
    else:
        write_json(path, protocol)
    if args.action == 'reference' or args.legacy_exp8:
        reference(args)
        return
    reports = preflight(args) if args.action in ('preflight', 'all') else read_json(args.out / 'preflight.json')
    if args.action == 'preflight':
        return
    if args.action == 'all' and args.reference_from is None:
        reference(args)
    require_reference(args)
    calibration = (calibrate(args, reports) if args.action in ('calibrate', 'all')
                   else read_json(args.out / 'calibration.json'))
    if args.action in ('prepare', 'all'):
        prepare(args, reports, calibration)
    if args.action in ('evaluate', 'all'):
        from exp8s_report import evaluate
        evaluate(args)


if __name__ == '__main__':
    main()
