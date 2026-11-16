@echo off
chcp 65001 >nul
title NovelRemoteHub 本地守护端状态检测

echo =============================================================
echo         NovelRemoteHub 本地执行守护端状态巡检
echo =============================================================
echo.

powershell -NoProfile -Command ^
    "$proc = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and $_.CommandLine.Contains('local_bridge.py') };" ^
    "if ($proc) {" ^
    "    Write-Host '🟢 [状态正常] 本地守护进程正在后台静默运行中！' -ForegroundColor Green;" ^
    "    Write-Host ('   • 进程 PID: ' + $proc.ProcessId);" ^
    "    Write-Host ('   • 启动程序: ' + $proc.Name);" ^
    "    Write-Host ('   • 启动时间: ' + $proc.CreationDate);" ^
    "    Write-Host '   • 云端通信: 正常待命（手机/网页端随时可调用本机 Antigravity/Codex）';" ^
    "} else {" ^
    "    Write-Host '⚪ [未运行] 当前未检测到本地守护进程。' -ForegroundColor Yellow;" ^
    "    Write-Host '   您可以双击 start_silent.vbs 静默启动，或运行 start_bridge.bat 查看控制台。';" ^
    "}"

echo.
echo =============================================================
pause
