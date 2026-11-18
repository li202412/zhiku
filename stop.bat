@echo off
chcp 65001 >nul
rem NOTE: 本文件为 UTF-8 + CRLF，已在首行切到 65001 代码页以正确显示中文。
setlocal
cd /d "%~dp0"

echo ============================================================
echo   知库 ZhiKu Docker 停止
echo ============================================================
echo.

docker info >nul 2>nul
if errorlevel 1 (
    echo [错误] Docker 引擎未运行，无需停止。
    goto :fail
)

echo 正在停止全部容器（数据将保留，下次运行 start.bat 即可恢复）...
docker compose down
if errorlevel 1 (
    echo [错误] 停止失败，请查看上方日志排查。
    goto :fail
)

echo.
echo 已停止全部容器，数据已保留。
goto :end

:fail
echo.
pause
exit /b 1

:end
echo 按任意键关闭窗口...
pause >nul
exit /b 0
