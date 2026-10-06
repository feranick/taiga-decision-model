"""voxel_iou.py — volumetric IoU of two closed triangle meshes on a voxel grid (numpy only).
Version: 2026.10.06.1

Used by eval_s80.py instead of OCC booleans: a boolean common/fuse of two nearly identical
solids (a Taiga-built part and its reference, e.g. two B-spline blades within 0.01 mm of each
other) can fail silently and return an empty result. Voxels have no such failure mode.
Each mesh is filled by ray parity along z through the centre of every voxel column. The grid
has n voxels along the longest side of the joint bounding box and at least `n_min` along every
side, so a thin plate (3 mm thick, 1.7 m wide) is not lost between voxel centres. Identical
meshes give IoU 1 exactly.

voxel_iou_checked() refines the grid until both voxel volumes are within 2 % of the solids' true
volumes, as far as memory allows; its result says whether it got there.
"""
from __future__ import annotations

import numpy as np


def voxelize(tris, x0: float, y0: float, hx: float, hy: float, nx: int, ny: int, zs) -> np.ndarray:
    """Occupancy (nx, ny, len(zs)) of the closed mesh `tris` (N, 3, 3); columns at
    x0 + (i + 0.5) hx, y0 + (j + 0.5) hy, cells centred on zs."""
    tris = np.asarray(tris, float)
    occ = np.zeros((nx * ny, len(zs) + 1), np.int32)
    if len(tris) == 0:
        return occ[:, :-1].reshape(nx, ny, len(zs)).astype(bool)
    A, B, C = tris[:, 0], tris[:, 1], tris[:, 2]
    xs, ys = tris[:, :, 0], tris[:, :, 1]
    # voxel columns whose centre lies in each triangle's xy bounding box
    i0 = np.ceil((xs.min(1) - x0) / hx - 0.5).astype(np.int64).clip(0, nx)
    i1 = np.floor((xs.max(1) - x0) / hx - 0.5).astype(np.int64).clip(-1, nx - 1)
    j0 = np.ceil((ys.min(1) - y0) / hy - 0.5).astype(np.int64).clip(0, ny)
    j1 = np.floor((ys.max(1) - y0) / hy - 0.5).astype(np.int64).clip(-1, ny - 1)
    wi, wj = (i1 - i0 + 1).clip(0), (j1 - j0 + 1).clip(0)
    cnt = wi * wj
    tot = int(cnt.sum())
    if tot:
        idx = np.repeat(np.arange(len(tris)), cnt)
        k = np.arange(tot) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        ii, jj = i0[idx] + k % wi[idx], j0[idx] + k // wi[idx]
        px, py = x0 + (ii + 0.5) * hx, y0 + (jj + 0.5) * hy
        a, b, c = A[idx], B[idx], C[idx]

        def edge(p, q):
            return (q[:, 0] - p[:, 0]) * (py - p[:, 1]) - (q[:, 1] - p[:, 1]) * (px - p[:, 0])

        w0, w1, w2 = edge(b, c), edge(c, a), edge(a, b)
        d = w0 + w1 + w2
        inside = (np.abs(d) > 1e-12) & (((w0 >= 0) & (w1 >= 0) & (w2 >= 0)) | ((w0 <= 0) & (w1 <= 0) & (w2 <= 0)))
        z = (w0 * a[:, 2] + w1 * b[:, 2] + w2 * c[:, 2]) / np.where(inside, d, 1.0)
        # each crossing toggles inside/outside for the voxels above it in its column
        np.add.at(occ, ((ii * ny + jj)[inside], np.searchsorted(zs, z[inside])), 1)
    occ = np.cumsum(occ, axis=1)[:, :-1] % 2
    return occ.reshape(nx, ny, len(zs)).astype(bool)


def _grid(pts: np.ndarray, n: int, n_min: int):
    lo, hi = pts.min(0), pts.max(0)
    ext = np.maximum(hi - lo, 1e-9)
    counts = np.maximum(np.ceil(n * ext / ext.max()), n_min)
    h = ext / counts
    lo = lo - h * np.array([0.5123, 0.5371, 0.4863])  # odd offsets: column centres off the mesh edges
    counts = counts.astype(int) + 2
    return lo, h, counts


def voxel_iou(tris_a, tris_b, n: int = 384, n_min: int = 48) -> dict:
    """IoU of two meshes on a common grid (n voxels along the longest side of their joint bounding
    box, at least n_min along every side). Returns iou, the two voxel volumes, their intersection,
    the largest voxel size h and the number of cells."""
    ta, tb = np.asarray(tris_a, float).reshape(-1, 3, 3), np.asarray(tris_b, float).reshape(-1, 3, 3)
    lo, h, (nx, ny, nz) = _grid(np.concatenate([ta.reshape(-1, 3), tb.reshape(-1, 3)]), n, n_min)
    zs = lo[2] + (np.arange(nz) + 0.5) * h[2]
    va = voxelize(ta, lo[0], lo[1], h[0], h[1], nx, ny, zs)
    vb = voxelize(tb, lo[0], lo[1], h[0], h[1], nx, ny, zs)
    inter, union = int(np.logical_and(va, vb).sum()), int(np.logical_or(va, vb).sum())
    v3 = float(np.prod(h))
    return {"iou": inter / union if union else 0.0, "vol_a": int(va.sum()) * v3, "vol_b": int(vb.sum()) * v3,
            "vol_inter": inter * v3, "h": float(h.max()), "cells": int(nx * ny * nz)}


def voxel_iou_checked(tris_a, tris_b, vol_a: float, vol_b: float, n: int = 384, tol: float = 0.02,
                      max_cells: float = 1.5e8) -> dict:
    """voxel_iou, with n doubled until the voxel volumes match vol_a / vol_b within `tol`, while
    the grid stays under max_cells; the result says which n it used and whether it converged."""
    ta, tb = np.asarray(tris_a, float).reshape(-1, 3, 3), np.asarray(tris_b, float).reshape(-1, 3, 3)
    pts = np.concatenate([ta.reshape(-1, 3), tb.reshape(-1, 3)])
    while True:
        r = voxel_iou(ta, tb, n)
        ok = all(v > 0 and abs(r[k] - v) <= tol * v for k, v in (("vol_a", vol_a), ("vol_b", vol_b)))
        nxt = n * 2
        if ok or float(np.prod(_grid(pts, nxt, 48)[2])) > max_cells:
            r["n"], r["converged"] = n, ok
            return r
        n = nxt
