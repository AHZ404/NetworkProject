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


def analyze_server_metrics():
    """Analyze server CPU usage and other metrics."""
    if not os.path.exists('server_metrics.csv'):
        print("[Server Analysis] 'server_metrics.csv' not found.")
        return

    try:
        server_df = pd.read_csv('server_metrics.csv')

        # Convert timestamp to relative time for easier plotting
        if 'timestamp' in server_df.columns:
            # Convert to numeric if it's string
            if server_df['timestamp'].dtype == 'object':
                server_df['timestamp'] = pd.to_numeric(server_df['timestamp'], errors='coerce')

            # Calculate relative time in seconds
            if not server_df['timestamp'].isnull().all():
                min_time = server_df['timestamp'].min()
                server_df['relative_time'] = server_df['timestamp'] - min_time

        print(f"\n[Server Metrics Analysis]")
        print(f"  Total samples: {len(server_df)}")

        # CPU Analysis
        if 'cpu_percent' in server_df.columns:
            cpu_data = pd.to_numeric(server_df['cpu_percent'], errors='coerce')
            valid_cpu = cpu_data.dropna()

            if not valid_cpu.empty:
                print(f"\n[CPU Usage Analysis]")
                print(f"  Mean CPU:           {valid_cpu.mean():.2f}%")
                print(f"  Median CPU:         {valid_cpu.median():.2f}%")
                print(f"  Max CPU:            {valid_cpu.max():.2f}%")
                print(f"  Min CPU:            {valid_cpu.min():.2f}%")
                print(f"  95th Percentile:    {valid_cpu.quantile(0.95):.2f}%")

                # Plot CPU over time
                plt.figure(figsize=(12, 6))

                if 'relative_time' in server_df.columns:
                    plt.plot(server_df['relative_time'], valid_cpu,
                             color='red', linewidth=1.5, alpha=0.8)
                    plt.xlabel('Time (seconds)')
                else:
                    plt.plot(valid_cpu.index, valid_cpu,
                             color='red', linewidth=1.5, alpha=0.8)
                    plt.xlabel('Sample Index')

                plt.ylabel('CPU Usage (%)')
                plt.title(f'Server CPU Usage Over Time (Update Rate: {UPDATE_HZ} Hz)')
                plt.grid(True, alpha=0.3)
                plt.ylim(bottom=0)

                # Add horizontal lines for mean and median
                plt.axhline(y=valid_cpu.mean(), color='blue', linestyle='--',
                            linewidth=1, alpha=0.7, label=f'Mean: {valid_cpu.mean():.1f}%')
                plt.axhline(y=valid_cpu.median(), color='green', linestyle=':',
                            linewidth=1, alpha=0.7, label=f'Median: {valid_cpu.median():.1f}%')

                plt.legend()
                plt.savefig('server_cpu_usage.png')
                print(f"  Saved 'server_cpu_usage.png'")

        # Bandwidth Analysis
        if 'bandwidth_kbps' in server_df.columns:
            bw_data = pd.to_numeric(server_df['bandwidth_kbps'], errors='coerce')
            valid_bw = bw_data.dropna()

            if not valid_bw.empty:
                print(f"\n[Bandwidth Analysis]")
                print(f"  Mean Bandwidth:     {valid_bw.mean():.2f} kbps")
                print(f"  Max Bandwidth:      {valid_bw.max():.2f} kbps")

                # Plot server bandwidth over time
                plt.figure(figsize=(12, 6))

                if 'relative_time' in server_df.columns:
                    plt.plot(server_df['relative_time'], valid_bw,
                             color='orange', linewidth=1.5, alpha=0.8)
                    plt.xlabel('Time (seconds)')
                else:
                    plt.plot(valid_bw.index, valid_bw,
                             color='orange', linewidth=1.5, alpha=0.8)
                    plt.xlabel('Sample Index')

                plt.ylabel('Bandwidth (kbps)')
                plt.title(f'Server Bandwidth Usage Over Time (Update Rate: {UPDATE_HZ} Hz)')
                plt.grid(True, alpha=0.3)
                plt.ylim(bottom=0)
                plt.savefig('server_bandwidth_usage.png')
                print(f"  Saved 'server_bandwidth_usage.png'")

        # Client Connections Analysis
        if 'clients_connected' in server_df.columns:
            clients_data = server_df['clients_connected']
            print(f"\n[Client Connections]")
            print(f"  Max Clients:        {clients_data.max()}")
            print(f"  Min Clients:        {clients_data.min()}")

    except Exception as e:
        print(f"Error analyzing server metrics: {e}")


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

        # Latency Analysis
        valid_latency = all_metrics[all_metrics['latency_ms'] > 0]['latency_ms']

        if not valid_latency.empty:
            print(f"\n[Latency Analysis]")
            print(f"  Mean:            {valid_latency.mean():.2f} ms")
            print(f"  Median:          {valid_latency.median():.2f} ms")
            print(f"  95th Percentile: {valid_latency.quantile(0.95):.2f} ms")
            print(f"  Max:             {valid_latency.max():.2f} ms")

            # Plot latency over time for each client
            plt.figure(figsize=(12, 6))
            colors = ['blue', 'green', 'red', 'purple']

            for idx, pid in enumerate(sorted(all_metrics['player_id'].unique())):
                subset = all_metrics[(all_metrics['player_id'] == pid) & (all_metrics['latency_ms'] > 0)]
                if not subset.empty:
                    color = colors[idx % len(colors)]
                    plt.plot(subset['snapshot_id'], subset['latency_ms'],
                             label=f'P{pid}', color=color, linewidth=1.5, alpha=0.8)

            plt.xlabel('Snapshot ID')
            plt.ylabel('Latency (ms)')
            plt.title(f'Latency over Time (Update Rate: {UPDATE_HZ} Hz)')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.ylim(bottom=0)
            plt.savefig('latency_analysis.png')
            print("  Saved 'latency_analysis.png'")

            # Create a bar chart of average latency per client
            plt.figure(figsize=(10, 6))
            avg_latency_per_client = []
            client_ids = []

            for pid in sorted(all_metrics['player_id'].unique()):
                client_latency = all_metrics[(all_metrics['player_id'] == pid) &
                                             (all_metrics['latency_ms'] > 0)]['latency_ms']
                if not client_latency.empty:
                    avg_latency_per_client.append(client_latency.mean())
                    client_ids.append(f'P{pid}')

            if avg_latency_per_client:
                bars = plt.bar(client_ids, avg_latency_per_client,
                               color=colors[:len(client_ids)])
                plt.xlabel('Client')
                plt.ylabel('Average Latency (ms)')
                plt.title(f'Average Client Latency (Update Rate: {UPDATE_HZ} Hz)')
                plt.grid(True, alpha=0.3, axis='y')

                # Add value labels on top of bars
                for bar in bars:
                    height = bar.get_height()
                    plt.text(bar.get_x() + bar.get_width() / 2., height,
                             f'{height:.1f}', ha='center', va='bottom')

                plt.savefig('client_latency_average.png')
                print("  Saved 'client_latency_average.png'")

        # Jitter Analysis
        if 'jitter_ms' in all_metrics.columns:
            # Convert jitter to numeric
            all_metrics['jitter_ms'] = pd.to_numeric(all_metrics['jitter_ms'], errors='coerce')
            valid_jitter = all_metrics[all_metrics['jitter_ms'] > 0]['jitter_ms']

            if not valid_jitter.empty:
                print(f"\n[Jitter Analysis]")
                print(f"  Overall Mean:            {valid_jitter.mean():.2f} ms")
                print(f"  Overall Median:          {valid_jitter.median():.2f} ms")
                print(f"  Overall 95th Percentile: {valid_jitter.quantile(0.95):.2f} ms")
                print(f"  Overall Max:             {valid_jitter.max():.2f} ms")

                # Calculate jitter statistics for each client
                for pid in all_metrics['player_id'].unique():
                    client_jitter = all_metrics[(all_metrics['player_id'] == pid) &
                                                (all_metrics['jitter_ms'] > 0)]['jitter_ms']
                    if not client_jitter.empty:
                        print(f"\n  Player {pid}:")
                        print(f"    Mean:            {client_jitter.mean():.2f} ms")
                        print(f"    Median:          {client_jitter.median():.2f} ms")
                        print(f"    Max:             {client_jitter.max():.2f} ms")
                        print(f"    Samples:         {len(client_jitter)}")

                # Plot jitter over time for each client
                plt.figure(figsize=(12, 6))
                colors = ['blue', 'green', 'red', 'purple']

                for idx, pid in enumerate(sorted(all_metrics['player_id'].unique())):
                    subset = all_metrics[(all_metrics['player_id'] == pid) &
                                         (all_metrics['jitter_ms'] > 0)]
                    if not subset.empty:
                        color = colors[idx % len(colors)]
                        plt.plot(subset['snapshot_id'], subset['jitter_ms'],
                                 label=f'P{pid}', color=color, linewidth=1.5, alpha=0.8)

                plt.xlabel('Snapshot ID')
                plt.ylabel('Jitter (ms)')
                plt.title(f'Jitter over Time (Update Rate: {UPDATE_HZ} Hz)')
                plt.legend()
                plt.grid(True, alpha=0.3)
                plt.ylim(bottom=0)
                plt.savefig('jitter_analysis.png')
                print("  Saved 'jitter_analysis.png'")

                # Create a bar chart of average jitter per client
                plt.figure(figsize=(10, 6))
                avg_jitter_per_client = []
                client_ids = []

                for pid in sorted(all_metrics['player_id'].unique()):
                    client_jitter = all_metrics[(all_metrics['player_id'] == pid) &
                                                (all_metrics['jitter_ms'] > 0)]['jitter_ms']
                    if not client_jitter.empty:
                        avg_jitter_per_client.append(client_jitter.mean())
                        client_ids.append(f'P{pid}')

                if avg_jitter_per_client:
                    bars = plt.bar(client_ids, avg_jitter_per_client,
                                   color=colors[:len(client_ids)])
                    plt.xlabel('Client')
                    plt.ylabel('Average Jitter (ms)')
                    plt.title(f'Average Client Jitter (Update Rate: {UPDATE_HZ} Hz)')
                    plt.grid(True, alpha=0.3, axis='y')

                    # Add value labels on top of bars
                    for bar in bars:
                        height = bar.get_height()
                        plt.text(bar.get_x() + bar.get_width() / 2., height,
                                 f'{height:.2f}', ha='center', va='bottom')

                    plt.savefig('client_jitter_average.png')
                    print("  Saved 'client_jitter_average.png'")
        else:
            print("\n[Jitter Analysis]")
            print("  Note: 'jitter_ms' column not found in client metrics.")
            print("  Make sure clients are calculating and logging jitter.")

        # Bandwidth Analysis for each client
        if 'bandwidth_per_client_kbps' in all_metrics.columns:
            # Remove any non-numeric values
            all_metrics['bandwidth_per_client_kbps'] = pd.to_numeric(
                all_metrics['bandwidth_per_client_kbps'], errors='coerce'
            )
            valid_bw = all_metrics[all_metrics['bandwidth_per_client_kbps'] > 0]

            if not valid_bw.empty:
                print(f"\n[Client Bandwidth Analysis]")

                # Calculate statistics for each client
                for pid in valid_bw['player_id'].unique():
                    client_bw = valid_bw[valid_bw['player_id'] == pid]['bandwidth_per_client_kbps']
                    if not client_bw.empty:
                        print(f"  Player {pid}:")
                        print(f"    Mean:     {client_bw.mean():.2f} kbps")
                        print(f"    Median:   {client_bw.median():.2f} kbps")
                        print(f"    Max:      {client_bw.max():.2f} kbps")
                        print(f"    Samples:  {len(client_bw)}")

                # Plot bandwidth over time for each client
                plt.figure(figsize=(12, 6))
                colors = ['blue', 'green', 'red', 'purple']

                for idx, pid in enumerate(sorted(valid_bw['player_id'].unique())):
                    subset = valid_bw[valid_bw['player_id'] == pid]
                    if not subset.empty:
                        color = colors[idx % len(colors)]
                        plt.plot(subset['snapshot_id'], subset['bandwidth_per_client_kbps'],
                                 label=f'P{pid}', color=color, linewidth=1.5, alpha=0.8)

                plt.xlabel('Snapshot ID')
                plt.ylabel('Bandwidth (kbps)')
                plt.title(f'Client Bandwidth Usage Over Time (Update Rate: {UPDATE_HZ} Hz)')
                plt.legend()
                plt.grid(True, alpha=0.3)
                plt.ylim(bottom=0)
                plt.savefig('client_bandwidth_usage.png')
                print("  Saved 'client_bandwidth_usage.png'")

                # Create a bar chart of average bandwidth per client
                plt.figure(figsize=(10, 6))
                avg_bw_per_client = []
                client_ids = []

                for pid in sorted(valid_bw['player_id'].unique()):
                    client_bw = valid_bw[valid_bw['player_id'] == pid]['bandwidth_per_client_kbps']
                    if not client_bw.empty:
                        avg_bw_per_client.append(client_bw.mean())
                        client_ids.append(f'P{pid}')

                if avg_bw_per_client:
                    bars = plt.bar(client_ids, avg_bw_per_client, color=colors[:len(client_ids)])
                    plt.xlabel('Client')
                    plt.ylabel('Average Bandwidth (kbps)')
                    plt.title(f'Average Client Bandwidth Usage (Update Rate: {UPDATE_HZ} Hz)')
                    plt.grid(True, alpha=0.3, axis='y')

                    # Add value labels on top of bars
                    for bar in bars:
                        height = bar.get_height()
                        plt.text(bar.get_x() + bar.get_width() / 2., height,
                                 f'{height:.1f}', ha='center', va='bottom')

                    plt.savefig('client_bandwidth_average.png')
                    print("  Saved 'client_bandwidth_average.png'")
            else:
                print("\n[Client Bandwidth Analysis]")
                print("  No bandwidth data found (all values are 0).")
                print("  This is normal for localhost testing.")
        else:
            print("\n[Client Bandwidth Analysis]")
            print("  Note: 'bandwidth_per_client_kbps' column not found in client metrics.")
            print("  Make sure clients are logging bandwidth data.")


if __name__ == '__main__':
    print("=== GUDP Performance Analysis ===")

    # 1. Calculate Error for CURRENT logs
    mean_error = analyze_position_errors()

    # 2. Analyze Latency for CURRENT logs
    analyze_metrics()

    # 3. Analyze Server Metrics (CPU, Bandwidth, etc.)
    analyze_server_metrics()

    # 4. If we successfully calculated error, save it to history and update graph
    if mean_error is not None:
        save_experiment_result(mean_error)
        plot_experiment_trends()
