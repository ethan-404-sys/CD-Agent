"""Load current batch22 sources with small, checked SYSU interface patches."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

STAGE = Path(__file__).resolve().parent

# Each replacement is an interface adaptation, not a local algorithm copy.
PATCHES = {
    'exp8_features': [
        ('(len(objects), 8)', '(len(objects), cfg.EXTRA_DIM)', 1),
        ('(count, 8)', '(count, cfg.EXTRA_DIM)', 1),
    ],
    'exp8_fit': [
        ("choices=('full', 'clean')", 'choices=cfg.TRAINED', 1),
    ],
    'exp8_evaluate': [
        ("for variant in ('full', 'clean'):", 'for variant in cfg.TRAINED:', 2),
        ("for key in ('full', 'clean'):", 'for key in cfg.TRAINED:', 1),
        ("{'detector': pool.meta[:, 0].copy()}", 'cfg.detector_scores(pool.meta)', 1),
        ('dict(detector=meta[:, 0].copy())', 'cfg.detector_scores(meta)', 1),
        ("    if not args.limit:\n        summary['legacy'] = legacy_reference(split)\n", '', 1),
        ("# SECOND 对象池 V1", '# SYSU 对象池 V1 与 U0 对照', 1),
        ("then full/clean/detector", "then configured scorer order", 1),
        ("    if split == 'test':\n        validation =", "    if split == 'test':\n        cfg.test_gate(args)\n        validation =", 1),
        ("unit_id=np.arange(len(labels)),", 'unit_id=cfg.unit_ids(split, name, args, len(labels)),', 1),
    ],
}


def patched_source(stem):
    path = STAGE.parent / 'batch22' / (stem + '.py')
    source = path.read_text(encoding='utf-8')
    for before, after, expected in PATCHES[stem]:
        if source.count(before) != expected:
            raise ValueError(f'{path}: upstream interface changed at {before!r}; review batch24 patch')
        source = source.replace(before, after)
    return path, source


def load(stem, name=None):
    path, source = patched_source(stem)
    name = name or stem
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module
