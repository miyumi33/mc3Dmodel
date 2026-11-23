#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OBJ + 贴图 → Nebula 的 .nbl（单帧静态点云，颜色取自模型原贴图）。

    python tools/obj_to_nbl.py models/candidates/ship_uv.obj \
        --out ship.nbl --points 1000000 --height 120 --size 15

为什么自己写而不是用 Blender 插件：
  插件是 modal operator（靠 timer 驱动），无头模式跑不完；
  但它的格式在 core/writer.py 里写得很清楚，照抄即可。

NBL 结构（反推自插件源码）：
  "NEBULAFX" | u16 ver=1 | u16 fps | u32 frames | u16 texNum | u16 3
  | 28B 保留(bbox 6×f32 + 4B) | 每个贴图: u16 len + utf8 + 2B(1,1)
  | 帧索引: frames × (u64 offset + u32 size) | u32 kfCount + frames×4B
  帧块 = zstd( u8 type + u32 N + payload )
  I 帧 payload = pos.x[N] pos.y[N] pos.z[N] | col.r/g/b/a[N] | size u16[N]
                 | tex u8[N] | seq u8[N] | pid i32[N]
"""

import argparse
import os
import struct
import sys

import numpy as np
import zstandard as zstd
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ---------------------------------------------------------------- OBJ 解析
def load_obj_uv(path):
    """读 v / vt / f / usemtl，返回顶点、UV、三角面（含材质名）。"""
    verts, uvs = [], []
    tris, tri_uvs = [], []
    tri_mat = []
    mat = ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                verts.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("vt "):
                p = line.split()
                uvs.append([float(p[1]), float(p[2]) if len(p) > 2 else 0.0])
            elif line.startswith("usemtl "):
                mat = line[7:].strip()
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    parts = tok.split("/")
                    v = int(parts[0])
                    t = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                    idx.append((v - 1 if v > 0 else len(verts) + v,
                                t - 1 if t > 0 else (len(uvs) + t if t else 0)))
                for k in range(1, len(idx) - 1):
                    a, b, c = idx[0], idx[k], idx[k + 1]
                    tris.append([a[0], b[0], c[0]])
                    tri_uvs.append([a[1], b[1], c[1]])
                    tri_mat.append(mat)
    return (np.array(verts, np.float64), np.array(uvs, np.float64),
            np.array(tris, np.int64), np.array(tri_uvs, np.int64), tri_mat)


def load_tex(path):
    im = Image.open(path).convert("RGBA")
    return np.asarray(im, np.float32) / 255.0


def pick_textures(mat_name, tex_dir):
    """挑 diffuse / alpha 贴图。

    优先按材质名和文件名里的关键词配对（hull/rigging/sails…）；
    如果配不上（比如单材质的雕像），就退回"目录里唯一那张 diffuse"。
    """
    files = [f for f in os.listdir(tex_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    # 漫反射贴图命名有两种常见写法：xxx_diff.png 和 xxx_D.png（奥特曼包就是后者）
    def is_diff(fn):
        low = fn.lower()
        return ("diff" in low) or low.endswith("_d.png") or low.endswith("_d.jpg")                or low.endswith("-d.png") or low.endswith("_d.jpeg") or low.endswith("_d.webp")

    def is_normal_or_other(fn):
        low = fn.lower()
        return low.endswith(("_n.png", "_n.jpg", "_e.png", "_m.png")) or "normal" in low

    diffs = [f for f in files if is_diff(f) and not is_normal_or_other(f)]
    alphas = [f for f in files if "alpha" in f.lower()]
    ml = mat_name.lower()

    diff = None
    for f in diffs:
        low = f.lower()
        keys = [k for k in ("hull", "rigging", "sails", "base") if k in low]
        if keys and any(k in ml for k in keys):
            diff = os.path.join(tex_dir, f)
            break
    if diff is None and diffs:
        # 单材质模型：只有一张 diffuse 就直接用
        if len(diffs) == 1:
            diff = os.path.join(tex_dir, diffs[0])
        else:
            # 多张时按材质名里出现的词兜底
            for f in diffs:
                if any(tok in f.lower() for tok in ml.split("_") if len(tok) > 3):
                    diff = os.path.join(tex_dir, f)
                    break

    alpha = None
    for f in alphas:
        low = f.lower()
        if "sails" in low and "sails" in ml:
            alpha = os.path.join(tex_dir, f)
    return diff, alpha


def sample_tex(tex, uv):
    h, w = tex.shape[:2]
    x = np.clip((uv[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(np.int32)
    y = np.clip((1.0 - (uv[:, 1] % 1.0)) * (h - 1), 0, h - 1).astype(np.int32)
    return tex[y, x]


# ---------------------------------------------------------------- NBL 写出
def write_nbl(path, pos, col, size, tex_id, fps=20, frames=1):
    n = len(pos)
    cctx = zstd.ZstdCompressor(level=3)
    with open(path, "wb") as f:
        f.write(b"NEBULAFX")
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", fps))
        f.write(struct.pack("<I", frames))       # total_frames
        f.write(struct.pack("<H", 1))            # 贴图数量
        f.write(struct.pack("<H", 3))
        bbox_pos = f.tell()
        f.write(b"\x00" * 28)
        tex_path = b"minecraft:textures/particle/glitter_7.png"
        f.write(struct.pack("<H", len(tex_path)))
        f.write(tex_path)
        f.write(struct.pack("<BB", 1, 1))
        index_pos = f.tell()
        f.write(b"\x00" * (12 * frames))   # 帧索引区必须按帧数预留
        kf_pos = f.tell()
        f.write(struct.pack("<I", 0))
        f.write(b"\x00" * 4)

        pid = np.arange(n, dtype="<i4")
        seq = np.zeros(n, np.uint8)

        # 第 0 帧：I 帧（全量）
        payload = bytearray()
        for k in range(3):
            payload.extend(pos[:, k].astype("<f4").tobytes())
        for k in range(4):
            payload.extend(col[:, k].astype("B").tobytes())
        payload.extend(size.astype("<H").tobytes())
        payload.extend(tex_id.astype("B").tobytes())
        payload.extend(seq.tobytes())
        payload.extend(pid.tobytes())

        index = []
        raw = struct.pack("<BI", 0, n) + bytes(payload)
        chunk = cctx.compress(raw)
        index.append((f.tell(), len(chunk)))
        f.write(chunk)

        # 第 1..frames-1 帧：P 帧，全零差分（静止画面）
        # 布局与 I 帧不同：d_pos i16×3, d_col i8×4, d_size i16, d_tex i8, d_seq i8, pid i32
        zeros16 = np.zeros(n, "<i2").tobytes()
        zeros8 = np.zeros(n, np.int8).tobytes()
        p_payload = (zeros16 * 3) + (zeros8 * 4) + zeros16 + zeros8 + zeros8 + pid.tobytes()
        p_raw = struct.pack("<BI", 1, n) + p_payload
        p_chunk = cctx.compress(p_raw)
        for _ in range(frames - 1):
            index.append((f.tell(), len(p_chunk)))
            f.write(p_chunk)

        f.seek(index_pos)
        for off, ln in index:
            f.write(struct.pack("<QI", off, ln))
        f.seek(kf_pos)
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<I", 0))

        bmin = pos.min(0)
        bmax = pos.max(0)
        f.seek(bbox_pos)
        f.write(struct.pack("<fff", *bmin))
        f.write(struct.pack("<fff", *bmax))
        f.write(b"\x00" * 4)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("obj")
    ap.add_argument("--out", default="ship.nbl")
    ap.add_argument("--points", type=int, default=1000000)
    ap.add_argument("--height", type=float, default=120.0, help="缩放后最高维度（格）")
    ap.add_argument("--size", type=int, default=15, help="粒子大小，1/100 格（15 = 0.15 格）")
    ap.add_argument("--tex-dir", default=None)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--frames", type=int, default=60, help="总帧数（静止动画靠 P 帧重复）")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--scale-axis", default="auto", choices=["auto", "x", "y", "z"],
                    help="按哪个轴归一化高度。auto=最长边；y=按身高（武器很长的模型用这个）")
    ap.add_argument("--offset", default="0,0,0",
                    help="整体平移（格），用来把模型摆到玩家面前。Minecraft 北=-Z")
    args = ap.parse_args()

    verts, uvs, tris, tri_uvs, tri_mat = load_obj_uv(args.obj)
    print("模型: %d 顶点 / %d UV / %d 三角面" % (len(verts), len(uvs), len(tris)))

    tex_dir = args.tex_dir or os.path.join(os.path.dirname(args.obj), "textures")
    mats = sorted(set(tri_mat))
    mat_id_of = {m: i for i, m in enumerate(mats)}
    tri_mat_id = np.array([mat_id_of[m] for m in tri_mat], np.int32)

    # 贴图分配：按名字相似度做贪心配对（每个材质一张，不重复用）
    # 用字符集合的 Jaccard 相似度，比拆词包含稳，能处理 Tex_HHQ_D / Tex_yinhe 这类命名
    import re as _re
    def _norm(x):
        return set(_re.sub(r"[^a-z0-9]", "", x.lower().rsplit(".", 1)[0]))
    def _score(m, f):
        a, b = _norm(m), _norm(f)
        return len(a & b) / max(len(a | b), 1)

    all_files = [f for f in os.listdir(tex_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    diffs = [f for f in all_files if ("diff" in f.lower() or f.lower().endswith(("_d.png", "_d.jpg")))
             and not f.lower().endswith(("_n.png", "_e.png", "_m.png"))]
    alphas = [f for f in all_files if "alpha" in f.lower()]

    pairs = sorted(((_score(m, f), m, f) for m in mats for f in diffs), reverse=True)
    used_m, used_f, assign = set(), set(), {}
    for sc, m, f in pairs:
        if m in used_m or f in used_f:
            continue
        assign[m] = f
        used_m.add(m); used_f.add(f)
    for m in mats:                      # 没配上的用剩余文件兜底
        if m not in assign and diffs:
            rest = [f for f in diffs if f not in used_f]
            if rest:
                assign[m] = rest[0]; used_f.add(rest[0])

    mat_tex = {}
    for m in mats:
        f = assign.get(m)
        a = next((x for x in alphas if "sails" in x.lower() and "sails" in m.lower()), None)
        mat_tex[m] = (load_tex(os.path.join(tex_dir, f)) if f else None,
                      load_tex(os.path.join(tex_dir, a)) if a else None)
        print("  材质 %-18s -> %-22s (相似度 %.2f)" % (m, f or "无贴图", _score(m, f) if f else 0.0))

    # 三角形面积 → 按面积采样
    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
    cum = np.cumsum(area)
    total = cum[-1]

    rng = np.random.default_rng(args.seed)
    keep_pos, keep_col = [], []
    got = 0
    target = args.points
    while got < target:
        need = target - got
        cand = max(need * 2, 4096)
        r = rng.random(cand) * total
        fi = np.searchsorted(cum, r)
        np.clip(fi, 0, len(tris) - 1, out=fi)
        b1 = rng.random(cand)
        b2 = rng.random(cand)
        m = b1 + b2 > 1.0
        b1[m], b2[m] = 1.0 - b1[m], 1.0 - b2[m]
        b0 = 1.0 - b1 - b2

        P = (p0[fi] * b0[:, None] + p1[fi] * b1[:, None] + p2[fi] * b2[:, None])
        T = tri_uvs[fi]
        UV = (uvs[T[:, 0]] * b0[:, None] + uvs[T[:, 1]] * b1[:, None] + uvs[T[:, 2]] * b2[:, None])

        C = np.ones((cand, 4), np.float32)
        mid = tri_mat_id[fi]
        for mat_name, (dtex, atex) in mat_tex.items():
            sel = mid == mat_id_of[mat_name]
            if not sel.any():
                continue
            if dtex is not None:
                C[sel] = sample_tex(dtex, UV[sel])
            if atex is not None:
                a = sample_tex(atex, UV[sel])[:, 3]
                C[sel, 3] = a
        # 透明处（帆的镂空）丢掉
        ok = C[:, 3] > 0.35
        keep_pos.append(P[ok])
        keep_col.append(C[ok])
        got += int(ok.sum())

    pos = np.concatenate(keep_pos)[:target]
    col = np.concatenate(keep_col)[:target]

    # 缩放 + 居中（Y 向上）
    lo, hi = pos.min(0), pos.max(0)
    span = hi - lo
    base = span.max() if args.scale_axis == "auto" else span[{"x":0,"y":1,"z":2}[args.scale_axis]]
    k = args.height / max(base, 1e-9)
    pos = (pos - (lo + hi) / 2.0) * k

    ox, oy, oz = [float(v) for v in args.offset.split(",")]
    pos = pos + np.array([ox, oy, oz], np.float32)
    c8 = np.clip(col * 255, 0, 255).astype(np.uint8)
    c8[:, 3] = 255
    size = np.full(len(pos), args.size, np.uint16)
    tex_id = np.zeros(len(pos), np.uint8)

    mb = write_nbl(args.out, pos.astype(np.float32), c8, size, tex_id, fps=args.fps, frames=args.frames)
    print("\n写出: %s  (%.1f MB)" % (args.out, mb / 1048576))
    print("粒子: %d  尺寸: %.2f 格  包围盒: %s" %
          (len(pos), args.size / 100.0,
           np.round(pos.max(0) - pos.min(0), 1).tolist()))
    print("颜色样本:", c8[:3, :3].tolist())
    return 0


if __name__ == "__main__":
    sys.exit(main())
