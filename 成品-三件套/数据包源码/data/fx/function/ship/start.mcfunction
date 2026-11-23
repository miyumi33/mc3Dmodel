# 帆船：100 万粒子（取自荷兰帆船原贴图）
nebula clear
kill @e[tag=fx_nbl_tiga]
kill @e[tag=fx_nbl_horse]
kill @e[tag=fx_nbl_ship]
summon minecraft:marker ~ ~ ~ {Tags:["fx_nbl_ship"]}
execute at @e[tag=fx_nbl_ship] run nebula play ship
scoreboard players set #snbl fx_hnbl 1
scoreboard players set #snblt fx_hnbl 0
tellraw @s {"text":"[ship] 已启动（100 万粒子 · GPU · 自动循环）","color":"aqua"}
