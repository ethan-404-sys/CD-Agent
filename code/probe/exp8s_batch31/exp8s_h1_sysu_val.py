"""Run exp5 AnyChange H1 (unchanged settings) on the 800 SYSU pool_val images (exp8 supplement).

Wraps probe/exp5_anychange.py: registers sysu_val as in batch19 and restricts the image list
to the frozen pool_val images. Output: exp5/sysu_val/anychange/<image>.npz (H1 only).
"""
import sys

sys.dont_write_bytecode = True
sys.path[:0] = ['/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/batch19',
                '/guisongxia01/pangchao/wangyihan/Chang-Agent/probe']
import common  # noqa: E402
import exp8_data  # noqa: E402
import exp5_anychange as h  # noqa: E402

exp8_data.register(common)
VAL = exp8_data.image_names(scope='pool_val')
assert len(VAL) == 800
h.DETECTOR['sysu_val'] = 'schanger_small'
h.names = lambda dataset: VAL if dataset == 'sysu_val' else common.names(dataset)
if '--dataset' not in sys.argv:
    sys.argv += ['--dataset', 'sysu_val']
if '--h1_only' not in sys.argv:
    sys.argv.append('--h1_only')
h.main()
