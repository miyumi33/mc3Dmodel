# mc3Dmodel · 3D 粒子效果

用 **NebulaFX 的 GPU 实例化**把 3D 模型渲染成粒子雕塑，CPU 每 tick 只花约 0.02 条命令。

## 三套效果

| 效果 | 粒子数 | 命令 |
|---|---|---|
| 奥特曼（银河 + 火花枪） | 413,526 | `/function fx:tiga/start` |
| 马（大理石雕像） | 1,500,000 | `/function fx:horse/nbl` |
| 帆船（荷兰帆船） | 1,000,000 | `/function fx:ship/start` |
| 全部停止 | — | `/function fx:stop` |

## 目录

```
成品-三件套/
├─ 说明.md        安装步骤 / 命令 / 参数 / 重新生成 / 排错  ← 先看这个
├─ 运行/          丢进游戏就能用（数据包 / 资源包）
├─ 源码/          生成工具（Python）+ 模型 + 贴图
└─ 数据包源码/    15 个 mcfunction + tick/load 标签
```

## 需要装的模组

NebulaFX 1.0.8+ · **ThreatenGL**（必需）· YACL · Fabric API，环境 Minecraft 1.21.1 + Fabric。

## 动画文件

三个 `.nbl`（共 86.7 MB）不在仓库里——它们可以由 `源码/` 里的模型和工具重新生成，
具体命令见 [成品-三件套/说明.md](成品-三件套/说明.md) 第五节。

## 实现要点

- 自己实现了 `.nbl` 二进制导出器（`源码/tools/obj_to_nbl.py`），格式为 zstd 压缩的 SoA 帧
- 颜色全部**从模型原始贴图按 UV 采样**，不是估色
- 静止画面用「定时重播」实现循环（Nebula 没有 loop 选项）
- 粒子贴图换成 16×16 硬边实心圆盘，正面看不透
