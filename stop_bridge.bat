@echo off
chcp 65001 >nul
echo 正在停止本地执行桥接器...
wmic process where "commandline like '%%local_bridge.py%%' and name='python.exe'" delete >nul 2>&1
echo 已成功停止！
pause
