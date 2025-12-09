import subprocess
import time

print("=== Starting Baseline Local Test ===")

# Start server
server = subprocess.Popen(["python", "server.py"])
time.sleep(2)  # give server time to start

# Start clients
client1 = subprocess.Popen(["python", "client.py", "--name", "Kimo", "--cid", "101"])
# client2 = subprocess.Popen(["python", "client.py", "--name", "Hatem", "--cid", "102"])
# client3 = subprocess.Popen(["python", "client.py", "--name", "Lina", "--cid", "103"])

print("Baseline local test running...")
print("Press Ctrl+C to stop.")

try:
    server.wait()
    client1.wait()
except KeyboardInterrupt:
    server.terminate()
    client1.terminate()
    print("All processes stopped.")
