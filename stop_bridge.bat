@echo off
chcp 65001 >nul
echo 正在停止本项目的本地执行桥接器...
powershell -NoProfile -Command "$bridgeRoot = 'D:\RuanJian\NovelRemoteHub\'; $processes = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and $_.CommandLine.Contains($bridgeRoot) }; $processes | Where-Object { $_.CommandLine.Contains('launch_daemon.py') } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }; $processes | Where-Object { $_.CommandLine.Contains('local_bridge.py') } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }"
echo 桥接器停止命令已执行。
pause
