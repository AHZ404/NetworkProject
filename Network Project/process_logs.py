# process_logs.py
import pandas as pd
import matplotlib.pyplot as plt
from numpy import percentile, median, mean
from scipy.interpolate import interp1d
from game import GridClashGame
import os


def analyze_position_errors():
    """Calculate position errors between server and client logs."""
    try:
        server_df = pd.read_csv('server_position_log.csv')
    except FileNotFoundError:
        print("Error: 'server_position_log.csv' not found. Run the updated server.py first!")
        return

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
            # Check if server time is within client time range to avoid wild extrapolation
            if (server_row['time'] < client_player['time'].min() or
                    server_row['time'] > client_player['time'].max()):
                continue

            c_row = interp_row(server_row['time'])
            c_col = interp_col(server_row['time'])

            # Calculate Euclidean distance (Position Error)
            error = GridClashGame.calculate_position_error(
                (server_row['row'], server_row['col']),
                (c_row, c_col)
            )
            errors_all.append(error)

    if errors_all:
        mean_error = mean(errors_all)
        median_error = median(errors_all)
        p95_error = percentile(errors_all, 95)

        print(f"\n[Position Error Analysis]")
        print(f"  Mean Error:          {mean_error:.4f} cells")
        print(f"  Median Error:        {median_error:.4f} cells")
        print(f"  95th Percentile Error: {p95_error:.4f} cells")
        print(f"  Total Samples:       {len(errors_all)}")
    else:
        print("No overlapping timestamp data found for position analysis.")


def analyze_metrics():
    """Analyze latency and jitter from client logs and plot results."""
    metrics_data = []

    # Try to read logs for all 4 possible players
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

        # --- Latency Analysis ---
        valid_latency = all_metrics[all_metrics['latency_ms'] > 0]['latency_ms']

        if not valid_latency.empty:
            print(f"\n[Latency Analysis]")
            print(f"  Mean:            {valid_latency.mean():.2f} ms")
            print(f"  Median:          {valid_latency.median():.2f} ms")
            print(f"  95th Percentile: {valid_latency.quantile(0.95):.2f} ms")
            print(f"  Max:             {valid_latency.max():.2f} ms")

            # PLOT 1: Latency over Time
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
        else:
            print("No valid latency data found.")

        # --- Jitter Analysis ---
        # Jitter is stored in 'jitter_ms' column
        if 'jitter_ms' in all_metrics.columns:
            valid_jitter = all_metrics['jitter_ms'].dropna()

            if not valid_jitter.empty:
                print(f"\n[Jitter Analysis]")
                print(f"  Mean:            {valid_jitter.mean():.2f} ms")
                print(f"  Median:          {valid_jitter.median():.2f} ms")
                print(f"  95th Percentile: {valid_jitter.quantile(0.95):.2f} ms")
                print(f"  Max:             {valid_jitter.max():.2f} ms")
            else:
                print("No valid jitter data found.")
        else:
            print("Column 'jitter_ms' not found in metrics logs.")


if __name__ == '__main__':
    print("=== GUDP Performance Analysis ===")
    analyze_position_errors()
    analyze_metrics()