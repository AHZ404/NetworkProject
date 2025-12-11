import subprocess
import time
import os
import sys

# --- Configuration ---
NUM_CLIENTS = 4
CLIENT_RUN_MODE = 'auto'  # 'auto' for automated movement, '' for manual (requires input)
SERVER_SCRIPT = 'server.py'
CLIENT_SCRIPT = 'client.py'


def start_server():
    """Starts the server in a separate, non-blocking process."""
    print(f"--- Starting {SERVER_SCRIPT} ---")
    try:
        # Use Popen to start the server in the background
        # Note: stdout and stderr are redirected to the main script's console
        server_process = subprocess.Popen([sys.executable, SERVER_SCRIPT])
        print(f"Server started with PID: {server_process.pid}")
        # Give the server a moment to bind the port
        time.sleep(2)
        return server_process
    except FileNotFoundError:
        print(f"ERROR: Python executable not found or {SERVER_SCRIPT} is missing.")
        return None
    except Exception as e:
        print(f"ERROR starting server: {e}")
        return None


def start_client(client_id, mode):
    """Starts a client in a new, visible terminal window."""
    print(f"--- Starting {CLIENT_SCRIPT} (ID: {client_id}) in '{mode}' mode ---")

    # Platform-specific commands to open a new terminal window:
    if sys.platform.startswith('win'):
        # Windows: Use 'start cmd /k' to open a new console and keep it open
        command = [
            'start', 'cmd', '/k', sys.executable, CLIENT_SCRIPT
        ]
        if mode == 'auto':
            command.append('auto')

        # Use shell=True for the 'start' command on Windows
        return subprocess.Popen(command, shell=True)

    elif sys.platform.startswith('linux') or sys.platform == 'darwin':
        # Linux/macOS: Use 'xterm' (or 'gnome-terminal', 'konsole')
        # Using xterm for broad compatibility.
        command = ['xterm', '-title', f'Client {client_id}', '-e', sys.executable, CLIENT_SCRIPT]
        if mode == 'auto':
            command.append('auto')

        return subprocess.Popen(command)

    else:
        print(f"Warning: Unsupported OS platform ({sys.platform}). Cannot launch GUI client.")
        return None


def main():
    server_process = None
    client_processes = []

    try:
        server_process = start_server()
        if server_process is None:
            return

        # Start clients (up to 4 now)
        for i in range(1, NUM_CLIENTS + 1):
            client_proc = start_client(i, CLIENT_RUN_MODE)
            if client_proc:
                client_processes.append(client_proc)
            time.sleep(0.5)  # Stagger client connections

        print("\nAll processes started. Press Ctrl+C in this terminal to stop.")

        # Keep the main script alive until interrupted
        while True:
            # Check if server or any client died unexpectedly
            if server_process.poll() is not None:
                print("\nServer process terminated unexpectedly.")
                break
            for i, proc in enumerate(client_processes):
                if proc.poll() is not None:
                    print(f"\nClient {i + 1} terminated unexpectedly.")
                    break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping all processes...")
    finally:
        # Graceful shutdown
        if server_process:
            print(f"Terminating server (PID {server_process.pid})...")
            server_process.terminate()
        for proc in client_processes:
            if proc.poll() is None:
                print(f"Terminating client (PID {proc.pid})...")
                proc.terminate()

        # Wait a moment for processes to clean up
        time.sleep(1)
        print("Shutdown complete.")


if __name__ == '__main__':
    # Add an execution check to prevent subprocess recursion on some platforms
    if len(sys.argv) > 1 and sys.argv[1] == '__subprocess_guard__':
        print("Internal guard triggered. Exiting subprocess.")
        sys.exit(1)

    main()