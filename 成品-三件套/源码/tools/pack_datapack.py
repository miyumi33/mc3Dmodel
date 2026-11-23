#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把数据包目录打成 zip —— 路径必须是正斜杠。

Windows 的 Compress-Archive 会写 `data\\fx\\...`，Java 的 zip 读取器按字面匹配
`data/fx/...`，于是整个数据包会被当成空的。这里用 zipfile + 强制 posix 路径，
打完立刻读回来校验。

    python tools/pack_datapack.py mc粒子包 dist/mc粒子包.zip
"""

import os
import sys
import zipfile


def pack(src_dir, out_zip):
    src_dir = os.path.abspath(src_dir)
    out_zip = os.path.abspath(out_zip)
    os.makedirs(os.path.dirname(out_zip) or ".", exist_ok=True)
    if os.path.exists(out_zip):
        os.remove(out_zip)
    n = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, src_dir).replace(os.sep, "/")
                z.write(full, arc)
                n += 1
    return n


def verify(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    bad = [x for x in names if "\\" in x]
    has_meta = "pack.mcmeta" in names
    has_data = any(x.startswith("data/") for x in names)
    return len(names), bad, has_meta, has_data


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    n = pack(sys.argv[1], sys.argv[2])
    total, bad, meta, data = verify(sys.argv[2])
    print("packed %d files -> %s (%.2f MB)"
          % (n, sys.argv[2], os.path.getsize(sys.argv[2]) / 1024 / 1024))
    print("entries=%d backslash=%d pack.mcmeta=%s data/=%s" % (total, len(bad), meta, data))
    if bad:
        print("!! 有反斜杠条目:", bad[:3])
        sys.exit(1)
    if not (meta and data):
        print("!! 结构不对")
        sys.exit(1)
    print("OK")
