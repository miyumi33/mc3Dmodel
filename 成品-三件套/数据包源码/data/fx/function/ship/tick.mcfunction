# 动画 10 秒；每 160 tick（8 秒）重播
execute if score #snbl fx_hnbl matches 1 run scoreboard players add #snblt fx_hnbl 1
execute if score #snbl fx_hnbl matches 1 if score #snblt fx_hnbl matches 160.. run function fx:ship/replay
