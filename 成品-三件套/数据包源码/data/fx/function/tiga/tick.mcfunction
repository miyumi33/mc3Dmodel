# 动画 6 秒；每 80 tick（4 秒）重播，在播完前拉起
execute if score #gnbl fx_hnbl matches 1 run scoreboard players add #gnblt fx_hnbl 1
execute if score #gnbl fx_hnbl matches 1 if score #gnblt fx_hnbl matches 80.. run function fx:tiga/replay
