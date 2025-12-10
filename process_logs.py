# process_logs.py
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import interp1d
from game import GridClashGame


def analyze_position_errors():
    """Calculate position errors between server and client logs."""
    try:
        server_df = pd.read_csv('server_position_log.csv')
    except FileNotFoundError:
        print("Warning: Server position log ('server_position_log.csv') not found.")
        return

    errors_all = []

    for player_id in server_df['player_id'].unique():
        try:
            # Note: The client log format in the prompt was 'time,snapshot_id,row,col,latency'
            # The updated format should be consistent with client.py's changes.
            client_df = pd.read_csv(f'client_{player_id}_position_log.csv')
        except FileNotFoundError:
            print(f"Warning: No client position log for player {player_id}")
            continue
        except pd.errors.EmptyDataError:
            print(f"Warning: Client position log for player {player_id} is empty.")
            continue

        # Filter data for this player
        server_player = server_df[server_df['player_id'] == player_id]
        client_player = client_df[client_df['player_id'] == player_id]

        if len(client_player) < 2 or len(server_player) < 2:
            continue

        # Interpolate client positions at server timestamps
        interp_row = interp1d(client_player['time'], client_player['display_row'],
                              kind='linear', fill_value='extrapolate')
        interp_col = interp1d(client_player['time'], client_player['display_col'],
                              kind='linear', fill_value='extrapolate')

        # Calculate errors
        for _, server_row in server_player.iterrows():
            client_row = interp_row(server_row['time'])
            client_col = interp_col(server_row['time'])

            error = GridClashGame.calculate_position_error(
                (server_row['row'], server_row['col']),
                (client_row, client_col)
            )
            errors_all.append(error)

    if errors_all:
        mean_error = np.mean(errors_all)
        median_error = np.median(errors_all)
        p95_error = np.percentile(errors_all, 95)

        print(f"Position Error Analysis:")
        print(f"  Mean: {mean_error:.4f} cells")
        print(f"  Median: {median_error:.4f} cells")
        print(f"  95th percentile: {p95_error:.4f} cells")
        print(f"  Samples: {len(errors_all)}")
    else:
        print("No position error data available.")


def analyze_latency():
    """Analyze latency and jitter metrics from client logs."""
    metrics_data = []
    LATENCY_COL = 'latency_ms'  # Use the new, correct column name
    JITTER_COL = 'jitter_ms'    # Use the new, correct column name

    for player_id in range(1, 5):
        try:
            df = pd.read_csv(f'client_{player_id}_metrics.csv')
            df['player_id'] = player_id
            metrics_data.append(df)
        except FileNotFoundError:
            continue
        except pd.errors.EmptyDataError:
            continue

    if metrics_data:
        all_metrics = pd.concat(metrics_data, ignore_index=True)

        # Filter out invalid values (e.g., initial 0s or placeholder 0s)
        valid_latency = all_metrics[all_metrics[LATENCY_COL] > 0][LATENCY_COL].values
        valid_jitter = all_metrics[all_metrics[JITTER_COL] > 0][JITTER_COL].values

        if len(valid_latency) > 0:
            print(f"\nLatency Analysis (Total Samples: {len(valid_latency)}):")
            print(f"  Mean latency: {np.mean(valid_latency):.2f} ms")
            print(f"  Median latency: {np.median(valid_latency):.2f} ms")
            print(f"  95th percentile: {np.percentile(valid_latency, 95):.2f} ms")
        else:
            print("\nLatency data is invalid or empty.")

        if len(valid_jitter) > 0:
            print(f"\nJitter Analysis (Total Samples: {len(valid_jitter)}):")
            print(f"  Mean jitter: {np.mean(valid_jitter):.2f} ms")
            print(f"  Median jitter: {np.median(valid_jitter):.2f} ms")
            print(f"  95th percentile: {np.percentile(valid_jitter, 95):.2f} ms")
        else:
            print("\nJitter data is invalid or empty.")


        # Plot latency over time
        plt.figure(figsize=(10, 6))
        for pid in all_metrics['player_id'].unique():
            player_data = all_metrics[all_metrics['player_id'] == pid]
            # Use snapshot_id for x-axis
            plt.plot(player_data['snapshot_id'], player_data[LATENCY_COL],
                     label=f'Player {pid}', alpha=0.7)

        plt.xlabel('Snapshot ID')
        plt.ylabel('Latency (ms)')
        plt.title('Latency over Time')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig('latency_analysis.png')
        print("Saved latency plot to 'latency_analysis.png'")

    else:
        print("No metrics data available.")


if __name__ == '__main__':
    print("Grid Clash Performance Analysis")
    print("=" * 40)

    analyze_position_errors()
    analyze_latency()

    print("\nAnalysis complete!")