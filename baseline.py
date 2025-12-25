# baseline.py - UPDATED FOR WINDOWS GUI EXECUTION
import subprocess
import time
import os
import sys
import threading

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
        # Redirect output to console for debugging
        server_process = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Start threads to print server output
        def print_stdout(process):
            for line in process.stdout:
                if line.strip():
                    print(f"[SERVER] {line.strip()}")

        def print_stderr(process):
            for line in process.stderr:
                if line.strip():
                    print(f"[SERVER-ERROR] {line.strip()}")

        threading.Thread(target=print_stdout, args=(server_process,), daemon=True).start()
        threading.Thread(target=print_stderr, args=(server_process,), daemon=True).start()

        print(f"Server started with PID: {server_process.pid}")
        # Give the server a moment to bind the port
        time.sleep(3)
        return server_process
    except FileNotFoundError:
        print(f"ERROR: Python executable not found or {SERVER_SCRIPT} is missing.")
        return None
    except Exception as e:
        print(f"ERROR starting server: {e}")
        return None


def start_client(client_id, mode):
    """Starts a client in a new GUI window on Windows."""
    print(f"--- Starting {CLIENT_SCRIPT} (ID: {client_id}) in '{mode}' mode ---")

    import sys
    import subprocess

    # Build the command
    command = [sys.executable, CLIENT_SCRIPT]
    if mode == 'auto':
        command.append('auto')

    try:
        # On Windows, use CREATE_NEW_CONSOLE to get a separate window
        # and DETACHED_PROCESS to prevent the window from closing immediately
        if sys.platform.startswith('win'):
            # Method 1: Create a new console window (most reliable for GUI)
            creation_flags = subprocess.CREATE_NEW_CONSOLE

            # Method 2: Alternatively, use this for detached process
            # creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

            process = subprocess.Popen(
                command,
                creationflags=creation_flags,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Start threads to monitor client output
            def monitor_output(process, client_id):
                # Read stdout
                if process.stdout:
                    for line in iter(process.stdout.readline, ''):
                        if line.strip():
                            print(f"[CLIENT {client_id}] {line.strip()}")

                # Read stderr
                if process.stderr:
                    for line in iter(process.stderr.readline, ''):
                        if line.strip():
                            print(f"[CLIENT {client_id}-ERROR] {line.strip()}")

            threading.Thread(target=monitor_output, args=(process, client_id), daemon=True).start()

            print(f"Client {client_id} started with PID: {process.pid}")
            return process

        else:
            # For non-Windows, use xterm or similar
            term_cmd = ['xterm', '-title', f'Client {client_id}', '-e'] + command
            return subprocess.Popen(term_cmd)

    except Exception as e:
        print(f"ERROR starting client {client_id}: {e}")
        return None


def check_health(server_process, client_processes):
    """Check if processes are still running."""
    if server_process and server_process.poll() is not None:
        return False, "Server process terminated"

    for i, proc in enumerate(client_processes):
        if proc and proc.poll() is not None:
            return False, f"Client {i + 1} terminated"

    return True, "All processes running"


def main():
    server_process = None
    client_processes = []

    print("=" * 50)
    print("Grid Clash - Multiplayer Game Launcher")
    print("=" * 50)

    try:
        # Step 1: Start server
        server_process = start_server()
        if server_process is None:
            print("Failed to start server. Exiting.")
            return

        print(f"\nServer initialized. Waiting for clients...")
        print(f"Server output will appear above with [SERVER] prefix.")

        # Step 2: Wait a bit for server to fully initialize
        time.sleep(3)

        # Step 3: Start clients
        print(f"\nStarting {NUM_CLIENTS} clients...")
        for i in range(1, NUM_CLIENTS + 1):
            print(f"\nLaunching Client {i}...")
            client_proc = start_client(i, CLIENT_RUN_MODE)
            if client_proc:
                client_processes.append(client_proc)
                print(f"Client {i} launched successfully.")

            # Stagger client connections (important!)
            time.sleep(2)  # Increased delay to prevent connection floods

        print("\n" + "=" * 50)
        print("ALL PROCESSES STARTED SUCCESSFULLY!")
        print("=" * 50)
        print("\nGame windows should now be visible.")
        print("Client output will appear above with [CLIENT X] prefix.")
        print("\nPress Ctrl+C in this terminal to stop all processes.")

        # Step 4: Monitor processes
        last_health_check = time.time()
        while True:
            # Check health every 5 seconds
            if time.time() - last_health_check > 5:
                healthy, msg = check_health(server_process, client_processes)
                if not healthy:
                    print(f"\nHEALTH CHECK FAILED: {msg}")
                    break
                last_health_check = time.time()

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nKeyboard interrupt received. Shutting down...")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
    finally:
        # Graceful shutdown
        print("\n" + "=" * 50)
        print("Initiating shutdown sequence...")
        print("=" * 50)

        # Terminate clients first
        for i, proc in enumerate(client_processes):
            if proc and proc.poll() is None:
                print(f"Terminating Client {i + 1} (PID {proc.pid})...")
                try:
                    proc.terminate()
                except:
                    pass

        # Wait a moment
        time.sleep(2)

        # Terminate server
        if server_process and server_process.poll() is None:
            print(f"Terminating Server (PID {server_process.pid})...")
            try:
                server_process.terminate()
            except:
                pass

        # Wait for processes to exit
        print("Waiting for processes to exit...")
        time.sleep(3)

        # Force kill if still running
        for i, proc in enumerate(client_processes):
            if proc and proc.poll() is None:
                print(f"Force killing Client {i + 1}...")
                try:
                    proc.kill()
                except:
                    pass

        if server_process and server_process.poll() is None:
            print("Force killing Server...")
            try:
                server_process.kill()
            except:
                pass

        print("\n" + "=" * 50)
        print("Shutdown complete.")
        print("=" * 50)


if __name__ == '__main__':
    main()