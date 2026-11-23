#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""银河奥特曼专用：在原有贴图配色基础上，强化眼睛和胸口计时器。

    python tools/ginga_accent.py --out dist/ginga.nbl

做法：
  1. 按面积采样 + UV 取色（和 obj_to_nbl 一致）
  2. 用"贴图颜色 + 归一化位置"筛出眼睛 / 胸口环
  3. 这些点换成更亮的颜色、更大的尺寸（NBL 的 size 是逐点的）
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from obj_to_nbl import (load_obj_uv, load_tex, sample_tex, write_nbl)  # noqa: E402

OBJ = os.path.join(ROOT, "模型", "ginga", "ginga_full.obj")
TEX_BODY = os.path.join(ROOT, "模型", "ginga", "tex_all", "Tex_YH_D.png")
TEX_HHQ = os.path.join(ROOT, "模型", "ginga", "tex_all", "Tex_HHQ_D.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "ginga.nbl"))
    ap.add_argument("--points", type=int, default=2000000)
    ap.add_argument("--height", type=float, default=120.0, help="按身高缩放")
    ap.add_argument("--size", type=int, default=16, help="基础粒子大小（1/100 格）")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--fps", type=int, default=1)
    ap.add_argument("--offset", default="0,60,-30")
    ap.add_argument("--yaw", type=float, default=0.0, help="绕 Y 轴旋转（度），180=正面朝向玩家")
    ap.add_argument("--mouth", action="store_true", help="是否给嘴巴上暗色（默认关）")
    ap.add_argument("--silver", default="176,190,210", help="白色粒子改成什么颜色，空=不改")
    ap.add_argument("--lattice", type=float, default=0.0,
                    help="体素化格距（格），0=不体素化。越大越稀、越整齐")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--seed", type=int, default=21)
    args = ap.parse_args()

    verts, uvs, tris, tri_uvs, mats = load_obj_uv(OBJ)
    print("模型: %d 顶点 / %d 三角面" % (len(verts), len(tris)))
    tex_body = load_tex(TEX_BODY)
    tex_hhq = load_tex(TEX_HHQ)

    # 按材质分配贴图（武器用 HHQ，其余用身体）
    mat_set = sorted(set(mats))
    is_weapon_tri = np.array([("hhq" in m.lower()) for m in mats])
    tri_is_w = is_weapon_tri

    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
    cum = area / area.sum()

    rng = np.random.default_rng(args.seed)
    P_all, C_all = [], []
    got = 0
    while got < args.points:
        need = args.points - got
        cand = max(need * 2, 8192)
        fi = rng.choice(len(tris), cand, p=cum)
        b1, b2 = rng.random(cand), rng.random(cand)
        m = b1 + b2 > 1
        b1[m], b2[m] = 1 - b1[m], 1 - b2[m]
        b0 = 1 - b1 - b2
        P = p0[fi] * b0[:, None] + p1[fi] * b1[:, None] + p2[fi] * b2[:, None]
        T = tri_uvs[fi]
        UV = uvs[T[:, 0]] * b0[:, None] + uvs[T[:, 1]] * b1[:, None] + uvs[T[:, 2]] * b2[:, None]
        C = np.ones((cand, 4), np.float32)
        sel_w = tri_is_w[fi]
        if sel_w.any():
            C[sel_w] = sample_tex(tex_hhq, UV[sel_w])
        if (~sel_w).any():
            C[~sel_w] = sample_tex(tex_body, UV[~sel_w])
        P_all.append(P); C_all.append(C); got += cand

    P = np.concatenate(P_all)[: args.points]
    C = np.concatenate(C_all)[: args.points]
    rgb = C[:, :3] * 255.0

    # 归一化坐标（用于区域判定）
    lo, hi = P.min(0), P.max(0)
    U = (P - lo) / np.maximum(hi - lo, 1e-9)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]

    size = np.full(len(P), args.size, np.uint16)

    # ---- 眼睛：黄色调 + 头部高度
    eye = (r > 130) & (g > 100) & (b < 120) & (r > b + 40) & (U[:, 1] > 0.80)
    # ---- 胸口计时器：蓝调 + 胸段高度 + 靠近正面
    chest = ((b > 110) & (b > r + 25) & (g > 70)
             & (U[:, 1] > 0.62) & (U[:, 1] < 0.76)
             & (np.abs(U[:, 0] - 0.5) < 0.13) & (U[:, 2] > 0.35))

    # ---- 银白调成冷钢灰（不然整张脸一片白，嘴巴看不出来）
    mx = rgb.max(axis=1); mn = rgb.min(axis=1)
    silver = (mx - mn < 45) & (mx > 135) & (~eye)
    if args.silver.strip():
        sv = np.array([float(v) for v in args.silver.split(",")], np.float32)
        # 保留一点明暗层次，但整体拉亮：目标色 × (0.82 ~ 1.0)
        k_s = np.clip(rgb[silver].mean(axis=1) / 255.0, 0, 1)[:, None]
        rgb[silver] = (sv * (0.82 + 0.18 * k_s)).astype(np.float32)

    # ---- 嘴巴：用两只眼睛构建头部坐标系，在眼睛正下方定位
    mouth = np.zeros(len(P), bool)
    if eye.sum() > 50:
        # 先算世界坐标（缩放 + 旋转），再在 3D 里定位，比 UV 空间稳
        span0 = hi - lo
        k0 = args.height / max(span0[1], 1e-9)
        Wp = (P - (lo + hi) / 2.0) * k0
        if abs(args.yaw) > 1e-6:
            a0 = np.radians(args.yaw)
            cs, sn = np.cos(a0), np.sin(a0)
            X, Z = Wp[:, 0].copy(), Wp[:, 2].copy()
            Wp[:, 0] = X * cs + Z * sn
            Wp[:, 2] = -X * sn + Z * cs

        ep = Wp[eye]
        rng2 = np.random.default_rng(0)
        cent = ep[rng2.choice(len(ep), 2, replace=False)]
        for _ in range(25):
            dd = ((ep[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
            lab = dd.argmin(1)
            for kk in range(2):
                if (lab == kk).any():
                    cent[kk] = ep[lab == kk].mean(0)
        mid = cent.mean(0)
        side = cent[1] - cent[0]
        side /= max(np.linalg.norm(side), 1e-9)
        up = np.array([0.0, 1.0, 0.0])
        fwd = np.cross(side, up)
        fwd /= max(np.linalg.norm(fwd), 1e-9)
        head_c = Wp[Wp[:, 1] > (Wp[:, 1].min() + args.height * 0.83)].mean(0)
        if np.dot(fwd, mid - head_c) < 0:
            fwd = -fwd
        mouth_c = mid - up * (args.height * 0.030) + fwd * (args.height * 0.006)
        dist = np.linalg.norm(Wp - mouth_c, axis=1)
        mouth = (dist < args.height * 0.022) & (~eye)
        print("嘴巴中心 (%.1f, %.1f, %.1f)  半径 %.1f 格  命中 %d 点"
              % (mouth_c[0], mouth_c[1], mouth_c[2], args.height * 0.022, int(mouth.sum())))

    rgb[eye] = np.array([255, 220, 60], dtype=np.float32)
    size[eye] = np.uint16(min(65535, int(args.size * 2.5)))
    rgb[chest] = np.array([95, 235, 255], dtype=np.float32)
    size[chest] = np.uint16(min(65535, int(args.size * 1.5)))
    if args.mouth and mouth.any():
        rgb[mouth] = np.array([30, 16, 20], np.float32)
        size[mouth] = np.uint16(min(65535, int(args.size * 1.4)))
    print("白色改色 %d 点 · 嘴巴 %s" % (int(silver.sum()), "开" if args.mouth else "关"))

    print("强化：眼睛 %d 点（尺寸 ×2.5）· 胸口环 %d 点（尺寸 ×1.5）"
          % (int(eye.sum()), int(chest.sum())))

    # ---- 体素化：压到规则网格，每格只留一颗，位置取格心
    # 让 accent 点优先占格，避免被同格的普通点覆盖
    if args.lattice > 0:
        # 格距按**最终世界单位**给，这里换算回模型原生单位
        span_v = P.max(0) - P.min(0)
        k_v = args.height / max(span_v[1], 1e-9)
        g = args.lattice / k_v
        prio = eye | chest
        order = np.concatenate([np.nonzero(prio)[0], np.nonzero(~prio)[0]])
        key = np.floor((P - P.min(0)) / g).astype(np.int64)[order]
        _, first = np.unique(key, axis=0, return_index=True)
        keep = order[first]
        lo2 = P.min(0)
        P = lo2 + (np.floor((P[keep] - lo2) / g) + 0.5) * g
        C = C[keep]
        rgb = rgb[keep]
        size = size[keep]
        eye = eye[keep]; chest = chest[keep]
        print("体素化 格距 %.2f 格 → %d 格（原 %d 点）" % (g, len(P), len(order)))

    # 按身高缩放（武器很长，不能按最长边）
    span = hi - lo
    k = args.height / max(span[1], 1e-9)
    Pw = (P - (lo + hi) / 2.0) * k
    if abs(args.yaw) > 1e-6:
        a = np.radians(args.yaw)
        c, s_ = np.cos(a), np.sin(a)
        X, Z = Pw[:, 0].copy(), Pw[:, 2].copy()
        Pw[:, 0] = X * c + Z * s_
        Pw[:, 2] = -X * s_ + Z * c
        print("绕 Y 旋转 %.0f 度" % args.yaw)
    ox, oy, oz = [float(v) for v in args.offset.split(",")]
    Pw = Pw + np.array([ox, oy, oz], np.float32)

    if args.dry_run:
        print("（dry-run，不写文件）最终点数 = %d" % len(Pw))
        return 0

    col8 = np.zeros((len(rgb), 4), np.uint8)
    col8[:, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    col8[:, 3] = 255
    tex_id = np.zeros(len(Pw), np.uint8)
    mb = write_nbl(args.out, Pw.astype(np.float32), col8, size, tex_id,
                   fps=args.fps, frames=args.frames)
    print("写出 %s  (%.1f MB)" % (args.out, mb / 1048576))
    print("粒子 %d  尺寸 %.2f~%.2f 格" % (len(Pw), size.min() / 100, size.max() / 100))
    print("包围盒 = %s" % np.round(Pw.max(0) - Pw.min(0), 1).tolist())
    return 0


if __name__ == "__main__":
    sys.exit(main())
