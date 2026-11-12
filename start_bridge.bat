@echo off
chcp 65001 >nul
title 大秦科技修仙·本地执行守护端

echo [1/3] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 python 命令，请确认环境变量已配置！
    pause
    exit /b
)

echo [2/3] 配置防休眠与常驻策略...
powercfg -change -standby-timeout-ac 0 >nul 2>&1
powercfg -change -hibernate-timeout-ac 0 >nul 2>&1

echo [3/3] 启动本地执行桥接器 (Local Agent Bridge)...
echo -------------------------------------------------------------
echo  本程序正在运行中，已连接云服务器 124.220.54.64
echo  手机端访问: https://scientificexploration.art/novel/ (口令: daqin666)
echo  请勿关闭此窗口（最小化即可，下班电脑锁屏不影响后台运行）
echo -------------------------------------------------------------
python "D:\RuanJian\NovelRemoteHub\local_bridge.py"

pause
