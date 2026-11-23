#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_pack.py — 数据包静态检查（不启动游戏也能抓大部分低级错误）。

  python tools/check_pack.py [包目录]

检查项：
  1. 所有 `function ns:path` 引用的函数文件是否存在
  2. 所有 tag json 里的 values 是否都存在
  3. 每条 particle 命令的语法形状
  4. 注释行不吞命令、函数行数、命令总重
"""

import json
import os
import re
import sys

FUNC_REF = re.compile(r"(?<![\w:/])function\s+([a-z0-9_.\-]+:[a-z0-9_.\-/]+)")
COORD = re.compile(r"^[\^~]-?[\d.]*$|^-?[\d.]+$")
NUM = re.compile(r"^-?[\d.]+$")


def split_cmd(line):
    """按空格切词，但 {} 里的空格不切（SNBT 允许空格）。"""
    out, buf, depth = [], "", 0
    for ch in line:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == " " and depth == 0:
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def check_particle(line):
    t = split_cmd(line)
    if not t or t[0] != "particle":
        return "不是 particle 命令"
    if len(t) < 5:
        return "参数太少"
    rest = t[2:]                       # t[1] 是粒子 id（可带 {data}）
    if len(rest) < 3 or not all(COORD.match(x) for x in rest[:3]):
        return "坐标段不合法"
    tail = rest[3:]
    if not tail:
        return None
    if len(tail) < 3 or not all(COORD.match(x) for x in tail[:3]):
        return "delta 段不合法"
    tail = tail[3:]
    if not tail:
        return None
    if not NUM.match(tail[0]):
        return "speed 不合法"
    tail = tail[1:]
    if tail and not re.match(r"^\d+$", tail[0]):
        return "count 不合法"
    tail = tail[1:]
    if tail and tail[0] not in ("force", "normal"):
        return "mode 不合法"
    tail = tail[1:]
    if tail and not tail[0].startswith("@"):
        return "viewers 不合法"
    return None


def main(pack="mc粒子包"):
    func_root = os.path.join(pack, "data")
    problems, funcs = [], {}

    for dirpath, _, files in os.walk(func_root):
        for fn in files:
            if fn.endswith(".mcfunction"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, func_root).replace("\\", "/")
                # data/<ns>/function/<path>.mcfunction -> ns:path
                parts = rel.split("/")
                ns, path = parts[0], "/".join(parts[2:])[:-len(".mcfunction")]
                funcs[ns + ":" + path] = full

    # 1 + 3
    total_cmd = 0
    for name, full in sorted(funcs.items()):
        raw = open(full, "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):
            # BOM 会让第一行注释不再是注释，整行被当命令解析 → 函数加载失败
            problems.append("%s 带 UTF-8 BOM（第一行会被当命令解析，函数直接加载失败）" % name)
        if b"\r\n" in raw:
            problems.append("%s 用了 CRLF 换行" % name)
        with open(full, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
        total_cmd += len(lines)
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            for ref in FUNC_REF.findall(s):
                if ref not in funcs:
                    problems.append("%s:%d 引用了不存在的函数 %s" % (name, i, ref))
            if s.startswith("particle "):
                err = check_particle(s)
                if err:
                    problems.append("%s:%d %s → %s" % (name, i, err, s[:90]))

    # 2
    for dirpath, _, files in os.walk(func_root):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(dirpath, fn)
            with open(full, encoding="utf-8") as fh:
                data = json.load(fh)
            for ref in data.get("values", []):
                if isinstance(ref, dict):
                    ref = ref.get("id")
                if isinstance(ref, str) and ":" in ref and ref not in funcs:
                    problems.append("%s 的 tag 引用了不存在的函数 %s" % (fn, ref))

    # 统计
    models = sorted(n for n in funcs if n.startswith("fx:model/") and n.count("/") == 2
                    and n.endswith("/tick"))
    size = sum(os.path.getsize(f) for f in funcs.values())
    print("函数文件 %d 个，命令 %d 条，合计 %.0f KB" % (len(funcs), total_cmd, size / 1024))
    for m in models:
        name = m[9:-5]
        frames = sum(1 for n in funcs if n.startswith("fx:model/%s/f" % name))
        per = len(open(funcs["fx:model/%s/f00" % name], encoding="utf-8").read().splitlines()) \
            if "fx:model/%s/f00" % name in funcs else 0
        print("  模型 %-8s %3d 帧 × %3d 点" % (name, frames, per))

    if problems:
        print("\n发现 %d 个问题：" % len(problems))
        for p in problems[:40]:
            print("  ✗ " + p)
        return 1
    print("\n✓ 引用完整，命令形状正常")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
