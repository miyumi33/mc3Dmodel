# 关掉所有效果：清实例、清锚点、清计时器
scoreboard players set #gnbl fx_hnbl 0
scoreboard players set #gnblt fx_hnbl 0
scoreboard players set #hnbl fx_hnbl 0
scoreboard players set #hnblt fx_hnbl 0
scoreboard players set #snbl fx_hnbl 0
scoreboard players set #snblt fx_hnbl 0
nebula clear
kill @e[tag=fx_nbl_tiga]
kill @e[tag=fx_nbl_horse]
kill @e[tag=fx_nbl_ship]
tellraw @s {"text":"[三件套] 已全部停止","color":"gray"}
