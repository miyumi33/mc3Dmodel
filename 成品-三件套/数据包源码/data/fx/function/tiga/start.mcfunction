# 奥特曼：41.4 万粒子（体素化）。锚点固定，自动循环
nebula clear
kill @e[tag=fx_nbl_tiga]
kill @e[tag=fx_nbl_horse]
kill @e[tag=fx_nbl_ship]
summon minecraft:marker ~ ~ ~ {Tags:["fx_nbl_tiga"]}
execute at @e[tag=fx_nbl_tiga] run nebula play ginga
scoreboard players set #gnbl fx_hnbl 1
scoreboard players set #gnblt fx_hnbl 0
tellraw @s {"text":"[tiga] 已启动（41.4 万粒子 · GPU · 自动循环）","color":"aqua"}
