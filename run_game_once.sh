#!/bin/bash
# Usage: ./run_game_once.sh [duration_seconds]
DUR=${1:-40}    # default 40 seconds

rm -f server_log.txt server_position_log.csv server_metrics.csv
rm -f client_*_metrics.csv client_*_position_log.csv

python server.py > server_terminal.log 2>&1 &
SERVER_PID=$!

sleep 1

CLIENT_PIDS=""
for i in 1 2 3 4; do
    python client.py auto > client_${i}_terminal.log 2>&1 &
    CLIENT_PIDS="$CLIENT_PIDS $!"
    sleep 0.5
done

echo "Game running for $DUR seconds..."
sleep "$DUR"

echo "Stopping clients and server..."
kill $CLIENT_PIDS 2>/dev/null
kill $SERVER_PID 2>/dev/null

sleep 2
echo "Run complete. CSV logs should be created."


