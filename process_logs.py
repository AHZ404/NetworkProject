# process_logs.py
import matplotlib

from game import GridClashGame

# Force non-interactive backend to avoid display errors if no window is available
matplotlib.use('Agg')
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import interp1d
# We import UPDATE_HZ to log what setting was used for *this* specific run
from common import UPDATE_HZ
import os
import csv
import time

# Directory Paths
LOGS_DIR = 'Logs'
METRICS_DIR = 'Metrics'
PLOTS_DIR = 'Plots'

# Output History File (Stores results from multiple runs)
HISTORY_FILE = os.path.join(METRICS_DIR, 'experiment_history.csv')

# Ensure directories exist
for d in [LOGS_DIR, METRICS_DIR, PLOTS_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)


def save_experiment_result(mean_error):
    """Saves the current run results to the history file."""
    file_exists = os.path.isfile(HISTORY_FILE)

    # Determine the next Run ID
    next_id = 1
    if file_exists:
        try:
            df = pd.read_csv(HISTORY_FILE)
            if not df.empty and 'run_id' in df.columns:
                next_id = df['run_id'].max() + 1
        except Exception:
            pass

    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

    try:
        with open(HISTORY_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            # Write header if file is new
            if not file_exists:
                writer.writerow(['run_id', 'timestamp', 'update_hz', 'mean_error'])

            writer.writerow([next_id, timestamp_str, UPDATE_HZ, mean_error])

        print(f"\n[History] Saved Run #{next_id}: {UPDATE_HZ} Hz -> {mean_error:.4f} error")
    except Exception as e:
        print(f"[History] Error saving to {HISTORY_FILE}: {e}")


def plot_experiment_trends():
    """Reads the history file and generates trend plots."""
    if not os.path.exists(HISTORY_FILE):
        print(f"[Plotting] No history file found at {HISTORY_FILE}. Run the simulation first.")
        return

    try:
        df = pd.read_csv(HISTORY_FILE)
        if df.empty:
            print("[Plotting] History file is empty.")
            return

        print(f"[Plotting] Found {len(df)} historical runs. Generating plots...")

        # --- PLOT 1: Position Error History (Chronological) ---
        plt.figure(figsize=(10, 6))

        # Plot mean error per run
        plt.plot(df['run_id'], df['mean_error'], marker='o', linestyle='-', color='b', linewidth=2, label='Mean Error')

        # Annotate points with their Hz value
        for i in range(len(df)):
            hz_val = df['update_hz'].iloc[i]
            y_val = df['mean_error'].iloc[i]
            x_val = df['run_id'].iloc[i]
            plt.annotate(f"{hz_val}Hz", (x_val, y_val),
                         xytext=(0, 10), textcoords='offset points', ha='center', fontsize=9)

        plt.title('Experiment History: Position Error per Run')
        plt.xlabel('Run ID')
        plt.ylabel('Mean Position Error (Cells)')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()

        # Force integer ticks for Run ID
        if len(df) < 20:
            plt.xticks(df['run_id'])

        output_file_1 = os.path.join(PLOTS_DIR, 'error_history.png')
        plt.savefig(output_file_1)
        plt.close()
        print(f"   -> Saved: '{output_file_1}'")

        # --- PLOT 2: Error vs Update Rate (Grouped by Hz) ---
        # Group by Hz to handle multiple runs of the same Hz
        grouped = df.groupby('update_hz')['mean_error'].mean().reset_index()

        plt.figure(figsize=(10, 6))
        # Plot individual runs as small red dots
        plt.scatter(df['update_hz'], df['mean_error'], color='red', alpha=0.5, label='Individual Runs')
        # Plot the average line as blue
        plt.plot(grouped['update_hz'], grouped['mean_error'], 'b-o', linewidth=2, label='Average Trend')

        plt.title('Trend: Error vs Update Rate')
        plt.xlabel('Update Rate (Hz)')
        plt.ylabel('Mean Position Error (Cells)')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()

        output_file_2 = os.path.join(PLOTS_DIR, 'error_vs_hz.png')
        plt.savefig(output_file_2)
        plt.close()
        print(f"   -> Saved: '{output_file_2}'")

    except Exception as e:
        print(f"[Plotting] CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()


def analyze_position_errors():
    """Calculate position errors between server and client logs."""
    server_log_path = os.path.join(LOGS_DIR, 'server_position_log.csv')

    # 1. Validation Checks
    if not os.path.exists(server_log_path):
        print(f"[Analysis] Server log not found at {server_log_path}")
        return None

    try:
        server_df = pd.read_csv(server_log_path)
    except pd.errors.EmptyDataError:
        print(f"[Analysis] Server log is empty.")
        return None

    if server_df.empty:
        print("[Analysis] Server log contains no data.")
        return None

    errors_all = []
    players_found = 0

    # 2. Process each player
    unique_players = server_df['player_id'].unique()
    print(f"[Analysis] Analyzing logs for {len(unique_players)} players...")

    for player_id in unique_players:
        client_log_path = os.path.join(LOGS_DIR, f'client_{player_id}_position_log.csv')

        if not os.path.exists(client_log_path):
            continue

        try:
            client_df = pd.read_csv(client_log_path)
        except (pd.errors.EmptyDataError, Exception):
            continue

        if client_df.empty:
            continue

        server_player = server_df[server_df['player_id'] == player_id]
        client_player = client_df[client_df['player_id'] == player_id]

        if len(client_player) < 2 or len(server_player) < 2:
            print(f"   -> Player {player_id}: Not enough data points to interpolate.")
            continue

        players_found += 1

        # 3. Interpolation and Error Calculation
        try:
            # We interpolate Client positions to match exact Server timestamps
            interp_row = interp1d(client_player['time'], client_player['display_row'],
                                  kind='linear', fill_value='extrapolate')
            interp_col = interp1d(client_player['time'], client_player['display_col'],
                                  kind='linear', fill_value='extrapolate')

            # Only compare timestamps that actually overlap (plus a small buffer)
            start_time = max(server_player['time'].min(), client_player['time'].min())
            end_time = min(server_player['time'].max(), client_player['time'].max())

            valid_server_rows = server_player[
                (server_player['time'] >= start_time) &
                (server_player['time'] <= end_time)
                ]

            if valid_server_rows.empty:
                print(f"   -> Player {player_id}: No overlapping timestamps found.")
                continue

            for _, server_row in valid_server_rows.iterrows():
                c_row = interp_row(server_row['time'])
                c_col = interp_col(server_row['time'])

                error = GridClashGame.calculate_position_error(
                    (server_row['row'], server_row['col']),
                    (c_row, c_col)
                )
                errors_all.append(error)

        except Exception as e:
            print(f"[Analysis] Error processing player {player_id}: {e}")
            continue

    # 4. Final Aggregation
    if errors_all:
        mean_error = np.mean(errors_all)
        print(f"[Analysis] SUCCESS. Calculated mean error: {mean_error:.4f} (Samples: {len(errors_all)})")
        return mean_error
    else:
        print("[Analysis] FAILED. No valid overlapping data points found across all players.")
        return None


def analyze_metrics():
    """Analyze latency and jitter from client logs and plot results."""
    metrics_data = []

    for player_id in range(1, 5):
        fname = os.path.join(METRICS_DIR, f'client_{player_id}_metrics.csv')
        if os.path.exists(fname):
            try:
                df = pd.read_csv(fname)
                if not df.empty:
                    df['player_id'] = player_id
                    metrics_data.append(df)
            except Exception:
                pass

    if metrics_data:
        try:
            all_metrics = pd.concat(metrics_data, ignore_index=True)
            valid_latency = all_metrics[all_metrics['latency_ms'] > 0]['latency_ms']

            if not valid_latency.empty:
                print(f"[Latency] Mean: {valid_latency.mean():.2f} ms")

                plt.figure(figsize=(10, 6))
                for pid in all_metrics['player_id'].unique():
                    subset = all_metrics[(all_metrics['player_id'] == pid) & (all_metrics['latency_ms'] > 0)]
                    if not subset.empty:
                        plt.plot(subset['snapshot_id'], subset['latency_ms'], label=f'P{pid}')

                plt.xlabel('Snapshot ID')
                plt.ylabel('Latency (ms)')
                plt.title('Latency over Time (Current Run)')
                plt.legend()
                plt.grid(True, alpha=0.3)

                output_plot = os.path.join(PLOTS_DIR, 'latency_analysis.png')
                plt.savefig(output_plot)
                plt.close()
                print(f"   -> Saved: '{output_plot}'")

        except Exception as e:
            print(f"[Metrics] Error analyzing metrics: {e}")
    else:
        print("[Metrics] No client metrics files found.")


if __name__ == '__main__':
    print("=== GUDP Performance Analysis ===")
    print(f"Current Config: UPDATE_HZ = {UPDATE_HZ}")

    # 1. Analyze Latency (Current Run)
    analyze_metrics()

    # 2. Calculate Position Error (Current Run)
    mean_error = analyze_position_errors()

    # 3. Save result if valid
    if mean_error is not None:
        save_experiment_result(mean_error)
    else:
        print("\n[Warning] Current run analysis failed (no data?). Skipping history save.")
        print("          Generating plots from EXISTING history only.")

    # 4. Generate History Plots (Always try this!)
    plot_experiment_trends()

    print("\n=== Analysis Complete ===")