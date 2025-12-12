#!/bin/bash
set -e

IFACE="eth0"
DUR=40

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

    echo "Starting tcpdump capture to $PCAP_FILE ..."
    sudo tcpdump -i any -w "$PCAP_FILE" > /dev/null 2>&1 &
    TCPDUMP_PID=$!
    sleep 1

    ./run_game_once.sh "$DUR"

    echo "Stopping tcpdump ..."
    sudo kill -2 "$TCPDUMP_PID" 2>/dev/null || true
    wait "$TCPDUMP_PID" 2>/dev/null || true
    sleep 1

    echo "Running analysis with process_logs.py ..."
    python process_logs.py > "${NAME}_analysis.txt"

    echo "Scenario $NAME finished. Summary in ${NAME}_analysis.txt"
}

run_scenario baseline
run_scenario loss2 loss 2%
run_scenario loss5 loss 5%
run_scenario delay100 delay 100ms

cd "$(dirname "$0")/.."
sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true

echo
echo "All scenarios finished."
