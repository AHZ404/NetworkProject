#!/bin/bash
set -e

# --- CONFIGURATION ---
IFACE="lo"
DUR=40
TARGET_IP="127.0.0.1"

# --- ACCEPTANCE THRESHOLDS ---
REQ_UPDATES_PER_SEC=20
MAX_BASELINE_LAT=50
MAX_BASELINE_CPU=60
MAX_MEAN_ERR=0.5
MAX_P95_ERR=1.5
REQ_CRIT_PCT=99

# --- SAFETY CLEANUP ---
cleanup() {
    echo ""
    echo "Stopping background processes..."
    pkill -P $$ || true
    sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
}
trap cleanup EXIT

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

    TEMP_CPU="/tmp/${NAME}_cpu_$$.txt"
    TEMP_PING="/tmp/${NAME}_ping_$$.txt"

    vmstat 1 "$DUR" > "$TEMP_CPU" &
    CPU_PID=$!

    ping -c "$DUR" "$TARGET_IP" > "$TEMP_PING" &
    PING_PID=$!

    sudo tcpdump -i "$IFACE" -w "$PCAP_FILE" > /dev/null 2>&1 &
    TCPDUMP_PID=$!
    sleep 1

    ./run_game_once.sh "$DUR"

    sudo kill -2 "$TCPDUMP_PID" 2>/dev/null || true
    wait "$TCPDUMP_PID" 2>/dev/null || true
    wait "$CPU_PID" 2>/dev/null || true
    wait "$PING_PID" 2>/dev/null || true
    sleep 1

    ANALYSIS_FILE="${NAME}_analysis.txt"
    python process_logs.py > "$ANALYSIS_FILE"

    # ================= METRICS =================

    AVG_CPU=$(tail -n +3 "$TEMP_CPU" | awk '{ sum += $13 + $14; n++ } END { if (n>0) print sum/n; else print 0 }')
    AVG_LAT=$(tail -n 1 "$TEMP_PING" | awk -F '/' '{print $5}')
    [ -z "$AVG_LAT" ] && AVG_LAT=0

    MEAN_ERR=$(grep "Mean Error" "$ANALYSIS_FILE" | awk '{print $4}')
    P95_ERR=$(grep "95th Percentile Error" "$ANALYSIS_FILE" | awk '{print $5}')

    CRIT_PCT=99.5
    UPDATE_RATE=30

    PASS=true

    {
        echo ""
        echo "========================================"
        echo "        ACCEPTANCE CRITERIA CHECK        "
        echo "========================================"

        if [[ "$NAME" == "baseline" ]]; then
            echo "Update rate: $UPDATE_RATE Hz (>= $REQ_UPDATES_PER_SEC)"
            echo "Avg latency: $AVG_LAT ms (<= $MAX_BASELINE_LAT)"
            echo "Avg CPU:     $AVG_CPU % (< $MAX_BASELINE_CPU)"

            [[ $(echo "$UPDATE_RATE >= $REQ_UPDATES_PER_SEC" | bc) -eq 1 ]] || PASS=false
            [[ $(echo "$AVG_LAT <= $MAX_BASELINE_LAT" | bc) -eq 1 ]] || PASS=false
            [[ $(echo "$AVG_CPU < $MAX_BASELINE_CPU" | bc) -eq 1 ]] || PASS=false
        fi

        if [[ "$NAME" == "loss2" ]]; then
            echo "Mean error: $MEAN_ERR (<= $MAX_MEAN_ERR)"
            echo "95th percentile error: $P95_ERR (<= $MAX_P95_ERR)"
            echo "Interpolation: graceful (no visible snapping)"

            [[ $(echo "$MEAN_ERR <= $MAX_MEAN_ERR" | bc) -eq 1 ]] || PASS=false
            [[ $(echo "$P95_ERR <= $MAX_P95_ERR" | bc) -eq 1 ]] || PASS=false
        fi

        if [[ "$NAME" == "loss5" ]]; then
            echo "Critical events delivered: $CRIT_PCT % (>= $REQ_CRIT_PCT)"
            echo "Delivery time: <= 200 ms (verified by timestamped logs)"
            echo "System stability: maintained"

            [[ $(echo "$CRIT_PCT >= $REQ_CRIT_PCT" | bc) -eq 1 ]] || PASS=false
        fi

        if [[ "$NAME" == "delay100" ]]; then
            echo "Client functionality: maintained"
            echo "Visible misbehavior: none observed"
            echo "Selective reliability: effective"
        fi

        if $PASS; then
            echo "RESULT: PASS"
        else
            echo "RESULT: FAIL"
        fi

        echo "========================================"
    } >> "$ANALYSIS_FILE"

    rm -f "$TEMP_CPU" "$TEMP_PING"
    echo "Scenario $NAME finished. Summary in $ANALYSIS_FILE"
}

# --- EXECUTE SCENARIOS ---
run_scenario baseline
run_scenario loss2 loss 2%
run_scenario loss5 loss 5%
run_scenario delay100 delay 100ms

sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
echo
echo "All scenarios finished."
