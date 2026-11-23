# 马：150 万粒子（取自 Poly Haven 大理石马贴图）
nebula clear
kill @e[tag=fx_nbl_tiga]
kill @e[tag=fx_nbl_horse]
kill @e[tag=fx_nbl_ship]
summon minecraft:marker ~ ~ ~ {Tags:["fx_nbl_horse"]}
execute at @e[tag=fx_nbl_horse] run nebula play horse
scoreboard players set #hnbl fx_hnbl 1
scoreboard players set #hnblt fx_hnbl 0
tellraw @s {"text":"[horse] 已启动（150 万粒子 · GPU · 自动循环）","color":"aqua"}
