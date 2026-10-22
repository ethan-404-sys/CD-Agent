"""Compare official SYSU inference with cached test predictions without test GT."""
from __future__ import annotations

import argparse
from pathlib import Path

import exp8_runtime as rt

# Fixed before inference: eight images spread across the numeric test inventory.
IMAGES = [f'test_{i}.png' for i in (0, 571, 1142, 1713, 2285, 2856, 3427, 3999)]
CRITERIA = dict(probability_max_abs_error=1e-3, mask_different_pixels=0,
                probability_relative_tolerance=0)


def require_comparison(out):
    row = rt.read_json(Path(out) / 'compatibility/prediction_comparison.json')
    if (row['status'] != 'passed' or row['images'] != IMAGES
            or row['criteria'] != CRITERIA or row['test_gt_read']
            or row['detector'] != 'schanger_small'):
        raise ValueError('Run exp8_compare.py successfully before validation inference')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=rt.DEFAULT_OUT)
    args = parser.parse_args()
    import predict_schanger as original
    from PIL import Image

    root = args.out / 'compatibility'
    result_path = root / 'prediction_comparison.json'
    result_path.unlink(missing_ok=True)
    reference = rt.prediction_dir('sysu', args.out)
    for name in IMAGES:
        for path in (reference / 'prob' / (Path(name).stem + '.npy'), reference / 'mask' / name):
            if not path.is_file():
                raise FileNotFoundError(path)
    original.PRED = root / 'preds'
    original.names = lambda dataset: IMAGES
    # run() reads a reference only after saving each prediction. Give it the real
    # cached detector mask; its counts now measure agreement, never GT accuracy.
    original.load_label = lambda dataset, name: rt.prediction('sysu', name, args.out)

    def agreement_report(path, row):
        row['agreement_with_cached_mask'] = row.pop('metrics')
        row.pop('reference_f1_percent')
        row['test_gt_read'] = False
        row['evaluation_reference'] = str(reference / 'mask')
        rt.write_json(path, row)

    original.write_json = agreement_report
    original.run('sysu', 'small')
    generated = root / 'preds/sysu/schanger_small'
    rows = []
    for name in IMAGES:
        filename = Path(name).stem + '.npy'
        a, b = (rt.np.load(p / 'prob' / filename, allow_pickle=False).astype(rt.np.float32)
                for p in (reference, generated))
        with Image.open(reference / 'mask' / name) as image:
            mask_a = rt.np.asarray(image).copy()
        with Image.open(generated / 'mask' / name) as image:
            mask_b = rt.np.asarray(image).copy()
        if a.shape != b.shape or mask_a.shape != mask_b.shape:
            raise ValueError(f'{name}: prediction shape mismatch')
        if not rt.np.isfinite(a).all() or not rt.np.isfinite(b).all():
            raise ValueError(f'{name}: nonfinite probability')
        error = rt.np.abs(a-b)
        max_error = float(error.max())
        different = int(rt.np.count_nonzero(mask_a != mask_b))
        rows.append(dict(image=name, probability_exact=bool(rt.np.array_equal(a, b)),
                         probability_max_abs_error=max_error,
                         probability_mean_abs_error=float(error.mean()),
                         mask_different_pixels=different,
                         passed=max_error <= CRITERIA['probability_max_abs_error'] and different == 0))
    passed = all(row['passed'] for row in rows)
    rt.write_json(result_path, dict(status='passed' if passed else 'failed', images=IMAGES,
                                   detector='schanger_small', criteria=CRITERIA, per_image=rows,
                                   test_gt_read=False, reference=str(reference), generated=str(generated),
                                   checkpoint=str(Path(original.WEIGHTS) / 'schanger_small_sysu.pth')))
    if not passed:
        raise ValueError(f'Prediction compatibility failed; inspect {result_path}')
    print(result_path, flush=True)


if __name__ == '__main__':
    main()
