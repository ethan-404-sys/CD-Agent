"""The 2-D ndimage operations used by GPU refinement, with a numpy backend.

Use scipy when installed. A broken scipy installation still fails at import.
The numpy API covers unit-spaced EDT, 3x3 connectivity and one dilation step.
"""
from __future__ import annotations

from importlib.util import find_spec
from types import SimpleNamespace

import numpy as np

CONNECTIVITY = np.ones((3, 3), bool)


def label(mask, structure=None):
    mask = np.asarray(mask, bool)
    structure = (np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
                 if structure is None else np.asarray(structure, bool))
    if mask.ndim != 2 or structure.shape != (3, 3) or not np.array_equal(structure, structure[::-1, ::-1]):
        raise ValueError('Expected a 2-D mask and symmetric 3x3 connectivity')
    offsets = [(int(y)-1, int(x)-1) for y, x in np.argwhere(structure) if (y, x) != (1, 1)]
    labels = np.zeros(mask.shape, np.int32)
    height, width = mask.shape
    count = 0
    for y, x in np.argwhere(mask):
        y, x = int(y), int(x)
        if labels[y, x]:
            continue
        count += 1
        labels[y, x] = count
        stack = [(y, x)]
        while stack:
            yy, xx = stack.pop()
            for dy, dx in offsets:
                ny, nx = yy + dy, xx + dx
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = count
                    stack.append((ny, nx))
    return labels, count


def find_objects(labels, max_label=0):
    labels = np.asarray(labels)
    if labels.ndim != 2:
        raise ValueError('Expected 2-D labels')
    if max_label < 1:
        max_label = int(labels.max()) if labels.size else 0
    ymin = np.full(max_label + 1, labels.shape[0], np.intp)
    xmin = np.full(max_label + 1, labels.shape[1], np.intp)
    ymax = np.full(max_label + 1, -1, np.intp)
    xmax = np.full(max_label + 1, -1, np.intp)
    y, x = np.nonzero((labels > 0) & (labels <= max_label))
    ids = labels[y, x].astype(np.intp)
    np.minimum.at(ymin, ids, y)
    np.minimum.at(xmin, ids, x)
    np.maximum.at(ymax, ids, y)
    np.maximum.at(xmax, ids, x)
    return [None if ymax[i] < 0 else (slice(int(ymin[i]), int(ymax[i])+1),
                                    slice(int(xmin[i]), int(xmax[i])+1))
            for i in range(1, max_label + 1)]


def _squared_edt_1d(values):
    # Lower envelope of parabolas (x-q)^2 + values[q], linear in line length.
    sites = np.flatnonzero(np.isfinite(values))
    if not len(sites):
        return values.copy()
    vertices = np.empty(len(sites), np.intp)
    bounds = np.empty(len(sites) + 1, np.float64)
    vertices[0], bounds[0], bounds[1] = sites[0], -np.inf, np.inf
    k = 0
    for q in sites[1:]:
        p = vertices[k]
        cross = ((values[q] + q*q) - (values[p] + p*p)) / (2 * (q-p))
        while cross <= bounds[k]:
            k -= 1
            p = vertices[k]
            cross = ((values[q] + q*q) - (values[p] + p*p)) / (2 * (q-p))
        k += 1
        vertices[k], bounds[k], bounds[k+1] = q, cross, np.inf
    coordinates = np.arange(len(values))
    nearest = vertices[np.searchsorted(bounds[1:k+1], coordinates, side='left')]
    return (coordinates - nearest)**2 + values[nearest]


def distance_transform_edt(mask):
    mask = np.asarray(mask, bool)
    if mask.ndim != 2:
        raise ValueError('Expected a 2-D mask')
    if mask.all():
        # scipy's all-foreground convention: a virtual zero at (-1, 0).
        y, x = np.indices(mask.shape, dtype=np.float64)
        return np.sqrt((y + 1)**2 + x*x)
    squared = np.where(mask, np.inf, 0.0)
    for y in range(mask.shape[0]):
        squared[y] = _squared_edt_1d(squared[y])
    for x in range(mask.shape[1]):
        squared[:, x] = _squared_edt_1d(squared[:, x])
    return np.sqrt(squared)


def binary_dilation(mask, structure=None):
    mask = np.asarray(mask, bool)
    structure = (np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
                 if structure is None else np.asarray(structure, bool))
    if mask.ndim != 2 or structure.ndim != 2 or any(s % 2 == 0 for s in structure.shape):
        raise ValueError('Expected a 2-D mask and odd-sized structure')
    height, width = mask.shape
    cy, cx = np.array(structure.shape) // 2
    out = np.zeros_like(mask)
    for y, x in np.argwhere(structure):
        dy, dx = int(y-cy), int(x-cx)
        y0, y1 = max(0, dy), min(height, height+dy)
        x0, x1 = max(0, dx), min(width, width+dx)
        if y0 < y1 and x0 < x1:
            out[y0:y1, x0:x1] |= mask[y0-dy:y1-dy, x0-dx:x1-dx]
    return out


numpy_ndimage = SimpleNamespace(label=label, find_objects=find_objects,
                               distance_transform_edt=distance_transform_edt,
                               binary_dilation=binary_dilation)
if find_spec('scipy') is None:
    ndimage = numpy_ndimage
else:
    from scipy import ndimage
