@echo off
chcp 65001 >nul
title NovelRemoteHub 本地守护端状态检测

echo =============================================================
echo         NovelRemoteHub 本地执行守护端状态巡检
echo =============================================================
echo.

python "%~dp0launch_daemon.py" --status

echo.
echo =============================================================
pause
