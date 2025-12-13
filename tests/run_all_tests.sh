#!/bin/bash
set -e

IFACE="eth0"
DUR=40
TARGET_IP="127.0.0.1" # Change this if your server IP is different

run_scenario () {
    NAME="$1"
    shift

    echo
    echo "=============================="
    echo "Running scenario: $NAME"
    echo "=============================="

    cd "$(dirname "$0")/.."

    sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true

    if [ "$#" -gt 0 ]; then
        echo "Applying netem: $@"
        sudo tc qdisc add dev "$IFACE" root netem "$@"
    else
        echo "No netem (baseline)."
    fi

    mkdir -p pcaps
    PCAP_FILE="pcaps/${NAME}.pcapng"

    # --- START MONITORS (Temp files only) ---
    # 1. CPU Monitor: Captures usage to a temp file
    TEMP_CPU="/tmp/${NAME}_cpu_$$.txt"
    vmstat 1 "$DUR" > "$TEMP_CPU" &
    CPU_PID=$!

    # 2. Latency Monitor: Captures ping to a temp file
    TEMP_PING="/tmp/${NAME}_ping_$$.txt"
    ping -c "$DUR" "$TARGET_IP" > "$TEMP_PING" &
    PING_PID=$!

    echo "Starting tcpdump capture to $PCAP_FILE ..."
    sudo tcpdump -i any -w "$PCAP_FILE" > /dev/null 2>&1 &
    TCPDUMP_PID=$!
    sleep 1

    ./run_game_once.sh "$DUR"

    echo "Stopping tcpdump ..."
    sudo kill -2 "$TCPDUMP_PID" 2>/dev/null || true
    wait "$TCPDUMP_PID" 2>/dev/null || true

    # Wait for monitors to finish naturally (since they run for $DUR)
    wait "$CPU_PID" 2>/dev/null || true
    wait "$PING_PID" 2>/dev/null || true
    sleep 1

    echo "Running analysis with process_logs.py ..."
    # This generates the main file
    ANALYSIS_FILE="${NAME}_analysis.txt"
    python process_logs.py > "$ANALYSIS_FILE"

    # --- CALCULATE & APPEND STATS TO THE ANALYSIS FILE ---

    # 1. Calculate Average CPU (User + System columns 13 & 14 from vmstat)
    AVG_CPU=$(tail -n +3 "$TEMP_CPU" | awk '{ sum += $13 + $14; n++ } END { if (n > 0) print sum / n; else print "0"; }')

    # 2. Extract Average Latency (from ping summary tail)
    AVG_LAT=$(tail -n 1 "$TEMP_PING" | awk -F '/' '{print $5}')
    if [ -z "$AVG_LAT" ]; then AVG_LAT="N/A"; fi

    # 3. Find Critical Event % (Grep from the python output we just generated)
    # Looks for "Critical" followed eventually by a percentage
    CRIT_PCT=$(grep -i "Critical" "$ANALYSIS_FILE" | grep -oE "[0-9]+(\.[0-9]+)?%" | head -n 1)
    if [ -z "$CRIT_PCT" ]; then CRIT_PCT="Not found in logs"; fi

    # Append the consolidated block to the bottom of the analysis text file
    {
        echo ""
        echo "========================================"
        echo "       ADDITIONAL SYSTEM METRICS        "
        echo "========================================"
        echo "Average CPU Usage:       ${AVG_CPU}%"
        echo "Average Network Latency: ${AVG_LAT} ms"
        echo "========================================"
    } >> "$ANALYSIS_FILE"

    # Clean up temp files so only the single result file remains
    rm -f "$TEMP_CPU" "$TEMP_PING"

    echo "Scenario $NAME finished. Summary in $ANALYSIS_FILE"
}

run_scenario baseline
run_scenario loss2 loss 2%
run_scenario loss5 loss 5%
run_scenario delay100 delay 100ms

cd "$(dirname "$0")/.."
sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true

echo
echo "All scenarios finished."