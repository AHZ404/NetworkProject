# process_logs.py
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import interp1d
from game import GridClashGame
from common import UPDATE_HZ  # Import the current setting
import os
import csv

HISTORY_FILE = 'experiment_history.csv'


def save_experiment_result(mean_error):
    """Saves the current Hz and Error to a history file."""
    # Check if file exists to write header
    file_exists = os.path.isfile(HISTORY_FILE)

    # Read existing data to avoid duplicates for the same HZ
    existing_data = {}
    if file_exists:
        try:
            with open(HISTORY_FILE, 'r') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if row:
                        existing_data[int(row[0])] = float(row[1])
        except:
            pass

    # Update or add the current HZ result
    existing_data[UPDATE_HZ] = mean_error

    # Write everything back sorted by Hz
    with open(HISTORY_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['update_hz', 'mean_error'])
        for hz in sorted(existing_data.keys()):
            writer.writerow([hz, existing_data[hz]])

    print(f"\n[Experiment] Saved result: {UPDATE_HZ} Hz -> {mean_error:.4f} error")
    print(f"[Experiment] History saved to '{HISTORY_FILE}'")


def plot_experiment_trends():
    """Reads the history file and plots Error vs Update Rate."""
    if not os.path.exists(HISTORY_FILE):
        return

    try:
        df = pd.read_csv(HISTORY_FILE)
        if len(df) < 2:
            print("[Plotting] Need at least 2 data points for the Error vs Rate graph.")
            return

        plt.figure(figsize=(10, 6))
        plt.plot(df['update_hz'], df['mean_error'], marker='o', linestyle='-', color='b', linewidth=2)

        plt.title('Position Error vs Update Rate')
        plt.xlabel('Update Rate (Hz)')
        plt.ylabel('Mean Position Error (Cells)')
        plt.grid(True, linestyle='--', alpha=0.7)

        # Force integer ticks for Hz if possible
        plt.xticks(df['update_hz'].unique())

        output_file = 'error_vs_rate.png'
        plt.savefig(output_file)
        print(f"[Plotting] Graph updated: '{output_file}'")

    except Exception as e:
        print(f"Could not plot trends: {e}")


def analyze_position_errors():
    """Calculate position errors between server and client logs."""
    try:
        server_df = pd.read_csv('server_position_log.csv')
    except FileNotFoundError:
        print("Error: 'server_position_log.csv' not found. Run the server first!")
        return None

    errors_all = []

    for player_id in server_df['player_id'].unique():
        try:
            client_df = pd.read_csv(f'client_{player_id}_position_log.csv')
        except FileNotFoundError:
            continue
        except pd.errors.EmptyDataError:
            continue

        server_player = server_df[server_df['player_id'] == player_id]
        client_player = client_df[client_df['player_id'] == player_id]

        if len(client_player) < 2 or len(server_player) < 2:
            continue

        # Interpolate client positions to match server timestamps exactly
        interp_row = interp1d(client_player['time'], client_player['display_row'],
                              kind='linear', fill_value='extrapolate')
        interp_col = interp1d(client_player['time'], client_player['display_col'],
                              kind='linear', fill_value='extrapolate')

        for _, server_row in server_player.iterrows():
            if (server_row['time'] < client_player['time'].min() or
                    server_row['time'] > client_player['time'].max()):
                continue

            c_row = interp_row(server_row['time'])
            c_col = interp_col(server_row['time'])

            error = GridClashGame.calculate_position_error(
                (server_row['row'], server_row['col']),
                (c_row, c_col)
            )
            errors_all.append(error)

    if errors_all:
        mean_error = np.mean(errors_all)
        median_error = np.median(errors_all)
        p95_error = np.percentile(errors_all, 95)

        print(f"\n[Position Error Analysis]")
        print(f"  Mean Error:          {mean_error:.4f} cells")
        print(f"  Median Error:        {median_error:.4f} cells")
        print(f"  95th Percentile Error: {p95_error:.4f} cells")
        print(f"  Total Samples:       {len(errors_all)}")

        # Return the mean error so we can save it
        return mean_error
    else:
        print("No overlapping timestamp data found for position analysis.")
        return None


def analyze_metrics():
    """Analyze latency and jitter from client logs and plot results."""
    metrics_data = []

    for player_id in range(1, 5):
        fname = f'client_{player_id}_metrics.csv'
        if os.path.exists(fname):
            try:
                df = pd.read_csv(fname)
                df['player_id'] = player_id
                metrics_data.append(df)
            except Exception:
                pass

    if metrics_data:
        all_metrics = pd.concat(metrics_data, ignore_index=True)
        valid_latency = all_metrics[all_metrics['latency_ms'] > 0]['latency_ms']

        if not valid_latency.empty:
            print(f"\n[Latency Analysis]")
            print(f"  Mean:            {valid_latency.mean():.2f} ms")
            print(f"  Median:          {valid_latency.median():.2f} ms")
            print(f"  95th Percentile: {valid_latency.quantile(0.95):.2f} ms")
            print(f"  Max:             {valid_latency.max():.2f} ms")

            plt.figure(figsize=(10, 6))
            for pid in all_metrics['player_id'].unique():
                subset = all_metrics[(all_metrics['player_id'] == pid) & (all_metrics['latency_ms'] > 0)]
                plt.plot(subset['snapshot_id'], subset['latency_ms'], label=f'P{pid}')

            plt.xlabel('Snapshot ID')
            plt.ylabel('Latency (ms)')
            plt.title('Latency over Time')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.savefig('latency_analysis.png')
            print("  Saved 'latency_analysis.png'")

        if 'jitter_ms' in all_metrics.columns:
            valid_jitter = all_metrics['jitter_ms'].dropna()
            if not valid_jitter.empty:
                print(f"\n[Jitter Analysis]")
                print(f"  Mean:            {valid_jitter.mean():.2f} ms")
                print(f"  Median:          {valid_jitter.median():.2f} ms")
                print(f"  95th Percentile: {valid_jitter.quantile(0.95):.2f} ms")
                print(f"  Max:             {valid_jitter.max():.2f} ms")


if __name__ == '__main__':
    print("=== GUDP Performance Analysis ===")

    # 1. Calculate Error for CURRENT logs
    mean_error = analyze_position_errors()

    # 2. Analyze Latency for CURRENT logs
    analyze_metrics()

    # 3. If we successfully calculated error, save it to history and update graph
    if mean_error is not None:
        save_experiment_result(mean_error)
        plot_experiment_trends()