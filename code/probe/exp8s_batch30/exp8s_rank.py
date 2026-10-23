"""第 3、4 步共用的 r1 排序；不读取标签或分数。"""
from __future__ import annotations


def r1_key(obj):
    """升序排序键：独立来源数降序，新增面积升序；完全平局保持输入顺序。"""
    sources = {'C' if s in ('C1', 'C2') else 'G' if s.startswith('G') else s
               for s in obj['sources']}
    return -len(sources), obj['area']


def rank_r1(objects, k=None):
    if k is not None and k < 0:
        raise ValueError('k must be nonnegative or None')
    ranked = sorted(objects, key=r1_key)
    return ranked if k is None else ranked[:k]
