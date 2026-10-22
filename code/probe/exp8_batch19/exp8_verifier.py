"""Bind existing 6b Pool/Features interfaces to the SYSU pool; no training is run."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import exp8_runtime as rt
import exp8_data as data
import exp6b_common as sixb
import exp6_pool_io as pool_io


def verifier_split(out=data.DEFAULT_OUT):
    manifest = rt.read_json(Path(out) / 'exp6/sysu_val/manifest.json')
    if manifest != data.manifest():
        raise ValueError('SYSU manifest differs from the frozen original-image split')
    return manifest['verifier_train_images'], manifest['verifier_val_images']


def configure(out=data.DEFAULT_OUT):
    """Call before importing downstream 6b feature/training modules."""
    out = Path(out)
    verifier_split(out)
    sixb.ROOT = out / 'exp6'
    sixb.names = rt.common.names
    sixb.verifier_split = lambda: verifier_split(out)
    pool_io.POOL = out / 'exp6/pool'
    return sixb


def open_pool(out=data.DEFAULT_OUT):
    module = configure(out)
    summary = rt.read_json(Path(out) / 'exp6/pool/sysu_val/pieces_summary.json')
    if summary['config']['partial']:
        raise ValueError('Partial smoke pool is not a training pool')
    return module.Pool('sysu_val')


def training_module(out=data.DEFAULT_OUT):
    """Return the original 6b trainer bound to SYSU; the caller may later run main().

    The upstream main has a literal Pool('second_cv') call. Resolve that call to
    the real SYSU Pool here, retaining its original optimization and selection code.
    Loading this adapter does not start training or create checkpoints.
    """
    configure(out)
    spec = importlib.util.spec_from_file_location('_batch19_v1_training', data.PROBE / 'exp6b_train.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.Pool = lambda dataset: open_pool(out)
    module.verifier_split = lambda: verifier_split(out)
    return module
