@echo off
echo ============================================
echo Starting Baseline Local Test (Server + Client)
echo ============================================

:: Start server in a new window
start cmd /k "python server.py"

:: Wait 2 seconds for the server to start
timeout /t 2 >nul

:: Start each client in a new window
start cmd /k "python client.py --name Kimo --cid 101"
start cmd /k "python client.py --name Hatem --cid 102"
start cmd /k "python client.py --name Lina --cid 103"

echo Baseline test started successfully!
echo You can now see messages in the opened windows.
pause
