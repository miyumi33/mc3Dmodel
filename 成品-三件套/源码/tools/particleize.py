#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
particleize.py — 把 3D 模型采样成 Minecraft 粒子点云，编译成数据包函数。

原版命令读不了 3D 文件，所以是"离线烘焙"：
    网格/隐式曲面 --撒点--> 点云 --旋转/打光/上色--> 每帧一个 .mcfunction
数据包运行时只负责按 tick 播放这些帧。

用法：
    python particleize.py heart
    python particleize.py sword --points 400 --frames 24 --height 2.4
    python particleize.py models/dragon.obj --name dragon --points 600 --mode lit --color 0.3,0.8,1.0
    python particleize.py heart --preview-only          # 只出 PNG 预览，不写数据包

内置模型：heart（隐式曲面）/ sword（方块拼装）/ torus（参数曲面）
外部模型：.obj（三角形或四边形面，支持负索引；不读贴图，颜色靠模式决定）
"""

import argparse
import math
import os
import re
import sys

import numpy as np

# ---------------------------------------------------------------- 工具


def sanitize(name):
    out = "".join(c if (c.isascii() and (c.isalnum() or c == "_")) else "_"
                  for c in str(name).lower())
    return re.sub(r"_+", "_", out).strip("_") or "model"


def normalize_geometry(pts, height):
    """居中（XZ 居中，Y 也居中）+ 缩放到指定高度。"""
    pts = pts - pts.mean(axis=0)
    span = float(pts[:, 1].max() - pts[:, 1].min())
    if span <= 1e-9:
        span = 1.0
    return pts * (height / span)


def rotate_y(pts, nrm, ang):
    c, s = math.cos(ang), math.sin(ang)
    R = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    return pts @ R.T, nrm @ R.T


# ---------------------------------------------------------------- OBJ


def load_obj(path):
    verts, faces = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if not s or s[0] == "#":
                continue
            if s.startswith("v "):
                p = s.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif s.startswith("f "):
                idx = []
                for tok in s.split()[1:]:
                    i = int(tok.split("/")[0])
                    # OBJ 是 1-based，负索引表示从末尾倒数
                    idx.append(i - 1 if i > 0 else len(verts) + i)
                for k in range(1, len(idx) - 1):                  # 扇形三角化
                    faces.append((idx[0], idx[k], idx[k + 1]))
    if not verts or not faces:
        raise ValueError("%s 里没读到顶点或面（只支持 v / f 行）" % path)
    return np.asarray(verts, float), np.asarray(faces, int)


def sample_mesh(verts, faces, n, seed=0):
    """按面积均匀撒点（三角形重心坐标）。"""
    rng = np.random.default_rng(seed)
    tri = verts[faces]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    cross = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cross, axis=1)
    total = float(area.sum())
    if total <= 1e-12:
        raise ValueError("模型表面积为 0，无法撒点")
    pick = rng.choice(len(faces), size=n, p=area / total)
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    pts = a[pick] * (1 - u - v)[:, None] + b[pick] * u[:, None] + c[pick] * v[:, None]
    nrm = cross[pick] / (np.linalg.norm(cross[pick], axis=1, keepdims=True) + 1e-12)
    return pts, nrm, pick


# ---------------------------------------------------------------- 方块拼装


def box(cx, cy, cz, sx, sy, sz):
    """轴对齐长方体 -> 顶点 + 12 个三角面（自动朝外）。"""
    x0, x1 = cx - sx / 2, cx + sx / 2
    y0, y1 = cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    f = np.array([[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7],
                  [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
                  [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]], int)
    tri = v[f]
    cen = tri.mean(axis=1)
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    out = np.einsum("ij,ij->i", nrm, cen - np.array([cx, cy, cz])) < 0
    f[out] = f[out][:, [0, 2, 1]]                     # 反绕，保证法线朝外
    return v, f


def merge(parts):
    """parts: [(verts, faces, rgb), ...] -> 顶点 / 面 / 每面颜色。"""
    vs, fs, cs, off = [], [], [], 0
    for v, f, rgb in parts:
        vs.append(v)
        fs.append(f + off)
        cs.append(np.tile(np.asarray(rgb, float), (len(f), 1)))
        off += len(v)
    return np.concatenate(vs), np.concatenate(fs), np.concatenate(cs)


# ---------------------------------------------------------------- 内置模型


def heart_implicit(p):
    """(x²+9/4y²+z²-1)³ - x²z³ - 9/80·y²z³，z 轴朝上。"""
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    a = x * x + 2.25 * y * y + z * z - 1.0
    return a ** 3 - x * x * z ** 3 - 0.1125 * y * y * z ** 3


def model_heart(n, seed=0, band=0.045):
    """隐式曲面 + 等距近似采样：|f|/|∇f| < band 即"离表面够近"。"""
    rng = np.random.default_rng(seed)
    got_p, got_g, guard = [], [], 0
    h = 2e-4

    def f_at(x, y, z):
        a = x * x + 2.25 * y * y + z * z - 1.0
        return a ** 3 - x * x * z ** 3 - 0.1125 * y * y * z ** 3

    while sum(len(g) for g in got_p) < n and guard < 400:
        guard += 1
        cand = rng.uniform(-1.3, 1.3, size=(250000, 3))
        x, y, z = cand[:, 0], cand[:, 1], cand[:, 2]
        f = heart_implicit(cand)
        gx = (f_at(x + h, y, z) - f_at(x - h, y, z)) / (2 * h)
        gy = (f_at(x, y + h, z) - f_at(x, y - h, z)) / (2 * h)
        gz = (f_at(x, y, z + h) - f_at(x, y, z - h)) / (2 * h)
        g = np.sqrt(gx * gx + gy * gy + gz * gz) + 1e-12
        keep = np.abs(f) <= band * g
        if keep.any():
            got_p.append(cand[keep])
            got_g.append(np.stack([gx[keep], gy[keep], gz[keep]], 1))
    pts = np.concatenate(got_p)[:n]
    grad = np.concatenate(got_g)[:n]
    nrm = grad / (np.linalg.norm(grad, axis=1, keepdims=True) + 1e-12)
    # 公式里 z 朝上，换到 Minecraft 的 Y 朝上
    return (np.stack([pts[:, 0], pts[:, 2], pts[:, 1]], 1),
            np.stack([nrm[:, 0], nrm[:, 2], nrm[:, 1]], 1), None)


def model_sword(n, seed=0):
    """像素单位（1 格 = 16px）：剑柄 / 护手 / 剑刃 / 剑尖。"""
    steel = (0.30, 0.92, 0.86)
    wood = (0.42, 0.26, 0.14)
    dark = (0.28, 0.30, 0.33)
    parts = [box(0, 4, 0, 2, 8, 2, ) + (wood,),
             box(0, 9, 0, 8, 2, 2) + (dark,),
             box(0, 20, 0, 3, 20, 2) + (steel,),
             box(0, 31, 0, 1, 2, 2) + (steel,)]
    v, f, c = merge(parts)
    pts, nrm, pick = sample_mesh(v, f, n, seed)
    return pts, nrm, c[pick]


def model_torus(n, seed=0, R=1.0, r=0.38):
    """参数曲面：圆环（用来检查采样均匀度）。"""
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 2 * math.pi, n)
    v = rng.uniform(0, 2 * math.pi, n)
    # 面积加权：外圈点更多（雅可比 R + r cos v）
    acc = rng.random(n) * (R + r) * 2
    v = np.arccos(np.clip(1 - acc / r, -1, 1))      # 按 cos 分布采样
    pts = np.stack([(R + r * np.cos(v)) * np.cos(u),
                    r * np.sin(v),
                    (R + r * np.cos(v)) * np.sin(u)], 1)
    nrm = np.stack([np.cos(v) * np.cos(u), np.sin(v), np.cos(v) * np.sin(u)], 1)
    return pts, nrm, None


BUILTINS = {"heart": model_heart, "sword": model_sword, "torus": model_torus}


# ---------------------------------------------------------------- 上色


def colorize(pts, nrm, base_rgb, mode, c1, c2, jitter=0.035, seed=0):
    if base_rgb is None:
        if mode == "height":
            t = (pts[:, 1] - pts[:, 1].min()) / max(1e-9, float(pts[:, 1].max() - pts[:, 1].min()))
            base = c1[None, :] * (1 - t)[:, None] + c2[None, :] * t[:, None]
        else:
            base = np.tile(np.asarray(c1, float), (len(pts), 1))
    else:
        base = base_rgb.copy()

    L = np.array([0.40, 0.78, 0.48])
    L = L / np.linalg.norm(L)
    if mode == "flat":
        shade = np.ones(len(pts))
    else:
        shade = 0.32 + 0.68 * np.clip(nrm @ L, 0.0, 1.0)   # 假漫反射，制造体积感

    rng = np.random.default_rng(seed + 7)
    rgb = base * shade[:, None] + rng.normal(0, jitter, (len(pts), 3))
    return np.clip(rgb, 0.0, 1.0)


# ---------------------------------------------------------------- 生成数据包


def dust_line(px, py, pz, rgb, scale):
    r, g, b = rgb
    return ("particle dust{color:[%.2f,%.2f,%.2f],scale:%.2f} "
            "~%.2f ~%.2f ~%.2f 0 0 0 0 1 force" % (r, g, b, scale, px, py, pz))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def build_frames(pts, nrm, base_rgb, cfg):
    frames = []
    for k in range(cfg["frames"]):
        ang = 2 * math.pi * k / cfg["frames"] if cfg["frames"] > 1 else 0.0
        p, n = rotate_y(pts, nrm, ang)
        rgb = colorize(p, n, base_rgb, cfg["mode"], cfg["c1"], cfg["c2"], seed=k)
        if cfg["snap"] > 0:
            p = np.round(p / cfg["snap"]) * cfg["snap"]
        frames.append([dust_line(x, y, z, rgb[i], cfg["scale"])
                       for i, (x, y, z) in enumerate(p)])
    return frames


def parse_obj(hex_or_csv):
    s = str(hex_or_csv).strip().lstrip("#")
    if "," in s:
        rgb = [float(x) for x in s.split(",")]
    else:
        rgb = [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return np.asarray(rgb, float)


def main(argv=None):
    ap = argparse.ArgumentParser(description="3D 模型 -> Minecraft 粒子点云数据包")
    ap.add_argument("source", help="内置模型名（heart/sword/torus）或 .obj 路径")
    ap.add_argument("--name", default=None, help="函数/目录名，默认取文件名")
    ap.add_argument("--points", type=int, default=320, help="点数（粒子数/帧），默认 320")
    ap.add_argument("--frames", type=int, default=20, help="旋转帧数，默认 20；1 = 静态")
    ap.add_argument("--height", type=float, default=2.4, help="模型高度（格），默认 2.4")
    ap.add_argument("--fps", type=int, default=10, help="出粒子频率（每秒帧数），默认 10")
    ap.add_argument("--scale", type=float, default=0.8, help="dust 粒子大小，默认 0.8")
    ap.add_argument("--mode", choices=["lit", "height", "flat"], default="lit",
                    help="上色模式：lit=假漫反射 / height=高度渐变 / flat=单色")
    ap.add_argument("--color", default="#d81e3a", help="主色，HEX 或 r,g,b")
    ap.add_argument("--color2", default="#ff7aa2", help="height 模式的顶色")
    ap.add_argument("--snap", type=float, default=0.0, help="坐标吸附网格（0.0625 = 1/16 格）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pack", default="mc粒子包", help="数据包根目录")
    ap.add_argument("--preview-only", action="store_true", help="只渲染 PNG 预览")
    cfg_args = ap.parse_args(argv)

    name = sanitize(cfg_args.name or os.path.splitext(os.path.basename(cfg_args.source))[0])

    # ---- 取几何
    if cfg_args.source in BUILTINS:
        pts, nrm, base_rgb = BUILTINS[cfg_args.source](cfg_args.points, cfg_args.seed)
    else:
        v, f = load_obj(cfg_args.source)
        pts, nrm, pick = sample_mesh(v, f, cfg_args.points, cfg_args.seed)
        base_rgb = None

    pts = normalize_geometry(pts, cfg_args.height)

    cfg = {
        "points": int(len(pts)),
        "frames": max(1, cfg_args.frames),
        "scale": cfg_args.scale,
        "mode": cfg_args.mode,
        "c1": parse_obj(cfg_args.color),
        "c2": parse_obj(cfg_args.color2),
        "snap": cfg_args.snap,
    }

    # ---- 预览
    from PIL import Image, ImageDraw
    preview_frames = []
    for k in range(4):
        ang = 2 * math.pi * k / 4
        p, n = rotate_y(pts, nrm, ang)
        preview_frames.append((p, colorize(p, n, base_rgb, cfg["mode"], cfg["c1"], cfg["c2"])))
    out_png = "preview_%s.png" % name
    render_preview(preview_frames, out_png, cfg_args.height)
    print("预览图: %s" % out_png)

    if cfg_args.preview_only:
        return 0

    # ---- 写函数
    frames = build_frames(pts, nrm, base_rgb, cfg)
    base_dir = os.path.join(cfg_args.pack, "data", "fx", "function", "model", name)
    for i, lines in enumerate(frames):
        write(os.path.join(base_dir, "f%02d.mcfunction" % i),
              "\n".join(lines) + "\n")

    nf = len(frames)
    hold = max(1, 20 // max(1, cfg_args.fps))

    emit = ["# 由 particleize.py 生成：推进帧号并调用对应帧",
            "scoreboard players add @s fx_frame 1",
            "execute if score @s fx_frame matches %d.. run scoreboard players set @s fx_frame 0" % nf]
    emit += ["execute if score @s fx_frame matches %d run function fx:model/%s/f%02d" % (i, name, i)
             for i in range(nf)]
    write(os.path.join(base_dir, "emit.mcfunction"), "\n".join(emit) + "\n")

    write(os.path.join(base_dir, "tick.mcfunction"),
          "# 由 particleize.py 生成：%d tick 一次（%d fps）\n"
          "execute if score #fx_clock fx_tick matches 0 run function fx:model/%s/emit\n"
          % (hold, cfg_args.fps, name))

    write(os.path.join(base_dir, "start.mcfunction"),
          "# 由 particleize.py 生成：%s（%d 点 × %d 帧）\n"
          "kill @e[tag=fx_mdl_%s]\n"
          "summon minecraft:marker ~ ~%.2f ~ {Tags:[\"fx_model\",\"fx_mdl_%s\"]}\n"
          "scoreboard players set @e[tag=fx_mdl_%s] fx_frame 0\n"
          "tellraw @s {\"text\":\"[fx] 粒子模型 %s 已开 · /function fx:model/stop 关闭\",\"color\":\"aqua\"}\n"
          % (name, cfg["points"], nf, name, cfg_args.height / 2, name, name, name))

    write(os.path.join(base_dir, "stop.mcfunction"),
          "kill @e[tag=fx_mdl_%s]\n" % name)

    # ---- 汇总 model/tick.mcfunction（每种装了线的模型一行）
    model_root = os.path.join(cfg_args.pack, "data", "fx", "function", "model")
    names = sorted(d for d in os.listdir(model_root)
                   if os.path.isdir(os.path.join(model_root, d))
                   and os.path.exists(os.path.join(model_root, d, "tick.mcfunction")))
    lines = ["# 由 particleize.py 汇总，勿手改：每种模型一行"]
    lines += ["execute as @e[tag=fx_mdl_%s] at @s run function fx:model/%s/tick" % (d, d)
              for d in names]
    write(os.path.join(model_root, "tick.mcfunction"), "\n".join(lines) + "\n")

    write(os.path.join(model_root, "stop.mcfunction"),
          "\n".join(["# 关闭所有粒子模型"] + ["kill @e[tag=fx_mdl_%s]" % d for d in names]
                    + ['tellraw @s {"text":"[fx] 粒子模型已全部关闭","color":"gray"}']) + "\n")

    total = sum(len(f) for f in frames)
    size = sum(len("\n".join(f)) + 1 for f in frames)
    print("模型 %-10s 点数 %4d 帧数 %2d 每帧命令 %d" % (name, cfg["points"], nf, cfg["points"]))
    print("写入 %d 个 mcfuntion，%d 条 particle，约 %.0f KB" % (nf + 3, total, size / 1024))
    print("游戏内: /reload  然后  /function fx:model/%s/start" % name)
    return 0


def render_preview(frames, out_path, height, size=520):
    """四视角正交投影 + 画家算法，用来在不开游戏的情况下看形状。"""
    from PIL import Image, ImageDraw

    W = size * len(frames)
    img = Image.new("RGB", (W, size), (10, 11, 14))
    yaw = math.radians(-38)      # 默认给个 3/4 侧视角
    pitch = math.radians(16)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cx, sx = math.cos(pitch), math.sin(pitch)

    for i, (pts, rgb) in enumerate(frames):
        X = pts[:, 0] * cy - pts[:, 2] * sy
        Z = pts[:, 0] * sy + pts[:, 2] * cy
        Y = pts[:, 1]
        Y2 = Y * cx - Z * sx
        Z2 = Y * sx + Z * cx

        span = max(abs(X).max(), abs(Y2).max(), 1e-6) * 1.25
        k = (size * 0.42) / span
        px = X * k + size / 2
        py = -Y2 * k + size / 2 * 1.02

        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        order = np.argsort(-Z2)                     # 远的先画
        for j in order:
            r, g, b = (rgb[j] * 255).astype(int)
            rad = 1.9 + 1.2 * (Z2[j] - Z2.min()) / (float(Z2.max() - Z2.min()) + 1e-6)
            x0, y0 = px[j], py[j]
            d.ellipse([x0 - rad, y0 - rad, x0 + rad, y0 + rad], fill=(r, g, b, 210))
        img.paste(layer, (i * size, 0), layer)

        d2 = ImageDraw.Draw(img)
        d2.text((i * size + 14, size - 22), "frame %d / %.0f deg" % (i, math.degrees(math.pi * 2 * i / 4)),
                fill=(120, 130, 150))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


if __name__ == "__main__":
    sys.exit(main())
