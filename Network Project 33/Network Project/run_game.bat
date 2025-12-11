@echo off
chcp 65001 >nul
title Grid Clash Game Launcher
color 0A

echo ========================================
echo    Grid Clash Multiplayer Game Launcher
echo ========================================
echo.

echo [1/5] Starting server...
start "Grid Clash Server" /B pythonw.exe server.py
timeout /t 3 /nobreak >nul

echo [2/5] Starting Client 1 (Auto Bot - Kimo)...
start "Player 1 - Kimo (Bot)" python.exe client.py auto Kimo

echo [3/5] Starting Client 2 (Manual User - Hatem)...
start "Player 2 - Ahmed (User)" python.exe client.py Ahmed

echo [4/5] Starting Client 3 (Auto Bot - Lina)...
start "Player 3 - Lina (Bot)" python.exe client.py auto Lina

echo [5/5] Starting Client 4 (Auto Bot - Alex)...
start "Player 4 - Hatem (Bot)" python.exe client.py auto Alex

echo.
echo ========================================
echo ✓ All clients started successfully!
echo.
echo Check client windows for game interface.
echo Console windows show detailed logs.
echo.
echo Manual Player (Hatem) Controls:
echo   Arrow Keys = Move
echo   SPACEBAR   = Claim cell
echo   ESC        = Quit
echo.
echo ========================================
echo Game is running!
echo.
echo Press any key to STOP all game windows...
pause >nul

echo.
echo Stopping all game processes...
taskkill /F /FI "WINDOWTITLE eq Player *" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Grid Clash Server" >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1

echo.
echo Running analysis...
python process_logs.py

echo.
echo ========================================
echo Game session complete!
echo.
echo Log files created:
echo   • client_log.txt - Console logs
echo   • server_log.txt - Server logs
echo   • *.csv - Position and metric data
echo   • latency_analysis.png - Latency graph
echo.
echo Check the logs for detailed game activity!
echo ========================================
echo.
timeout /t 10