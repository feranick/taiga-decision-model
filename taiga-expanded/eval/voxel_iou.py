"""voxel_iou.py — volumetric IoU of two closed triangle meshes on a voxel grid (numpy only).
Version: 2026.10.05.1

Used by eval_s80.py instead of OCC booleans: a boolean common/fuse of two nearly identical
solids (a Taiga-built part and its reference, e.g. two B-spline blades within 0.01 mm of each
other) can fail silently and return an empty result. Voxels have no such failure mode.
Each mesh is filled by ray parity along z through the centre of every voxel column; with
`n` voxels along the longest side the volume error is about (surface area x h / 2) at random,
and identical meshes give IoU 1 exactly.
"""
from __future__ import annotations

import numpy as np


def voxelize(tris, x0: float, y0: float, h: float, nx: int, ny: int, zs) -> np.ndarray:
    """Occupancy (nx, ny, len(zs)) of the closed mesh `tris` (N, 3, 3)."""
    tris = np.asarray(tris, float)
    occ = np.zeros((nx * ny, len(zs) + 1), np.int32)
    if len(tris) == 0:
        return occ[:, :-1].reshape(nx, ny, len(zs)).astype(bool)
    A, B, C = tris[:, 0], tris[:, 1], tris[:, 2]
    xs, ys = tris[:, :, 0], tris[:, :, 1]
    # voxel columns whose centre lies in each triangle's xy bounding box
    i0 = np.ceil((xs.min(1) - x0) / h - 0.5).astype(np.int64).clip(0, nx)
    i1 = np.floor((xs.max(1) - x0) / h - 0.5).astype(np.int64).clip(-1, nx - 1)
    j0 = np.ceil((ys.min(1) - y0) / h - 0.5).astype(np.int64).clip(0, ny)
    j1 = np.floor((ys.max(1) - y0) / h - 0.5).astype(np.int64).clip(-1, ny - 1)
    wi, wj = (i1 - i0 + 1).clip(0), (j1 - j0 + 1).clip(0)
    cnt = wi * wj
    tot = int(cnt.sum())
    if tot:
        idx = np.repeat(np.arange(len(tris)), cnt)
        k = np.arange(tot) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        ii, jj = i0[idx] + k % wi[idx], j0[idx] + k // wi[idx]
        px, py = x0 + (ii + 0.5) * h, y0 + (jj + 0.5) * h
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


def voxel_iou(tris_a, tris_b, n: int = 384) -> dict:
    """IoU of two meshes on a common grid with n voxels along the longest side of their joint
    bounding box. Returns iou, the two voxel volumes, their intersection and the voxel size h."""
    ta, tb = np.asarray(tris_a, float).reshape(-1, 3, 3), np.asarray(tris_b, float).reshape(-1, 3, 3)
    pts = np.concatenate([ta.reshape(-1, 3), tb.reshape(-1, 3)])
    lo, hi = pts.min(0), pts.max(0)
    h = float((hi - lo).max()) / n
    lo = lo - h * np.array([0.5123, 0.5371, 0.0])  # odd offsets: column centres off the mesh edges
    hi = hi + h
    nx, ny = int(np.ceil((hi[0] - lo[0]) / h)), int(np.ceil((hi[1] - lo[1]) / h))
    zs = lo[2] - 0.5 * h + (np.arange(int(np.ceil((hi[2] - lo[2]) / h)) + 1) + 0.0137) * h
    va, vb = voxelize(ta, lo[0], lo[1], h, nx, ny, zs), voxelize(tb, lo[0], lo[1], h, nx, ny, zs)
    inter, union = int(np.logical_and(va, vb).sum()), int(np.logical_or(va, vb).sum())
    v3 = h ** 3
    return {"iou": inter / union if union else 0.0, "vol_a": int(va.sum()) * v3, "vol_b": int(vb.sum()) * v3,
            "vol_inter": inter * v3, "h": h}
