# Multiplayer Game State Synchronization — Phase 1 (Local Run)
## Project 2 — CSE361: Computer Networks

---

what is this
This project demonstrates the **Phase 1 prototype** of the Multiplayer Game State Synchronization system.  
It focuses on verifying UDP-based communication between the **server** and **client(s)**.

The system currently exchanges:
- **INIT messages:** sent by the client to register/join.
- **DATA messages:** sent between server and clients for state updates.

At this stage, the game data is simulated using random (x, y) coordinates to test the network logic.  
The actual game (Grid Clash) will be linked in Phase 2.

---

### prerequisites 
- **Python 3.12 or later** (Python 3.13 compatible)
- **VS Code** or any terminal
- Windows Firewall must allow Python network access (only on first run)


---

### Quick Start (Recommended)
**Option 1 — Windows Batch File**
1. Double-click **run_baseline.bat**
2. Two windows will open:
   - One for the server
   - One for the client
3. You’ll see messages being exchanged automatically.

**Option 2 — Python Launcher**
Run this in VS Code terminal:
python run_baseline.py
This script automatically starts the server and one client for the local baseline test.

**Option 3-VS Code Terminals

**Step 1:** Start the server in the first terminal:
python server.py
Expected output:[SERVER] Listening on 127.0.0.1:9999

**Step 2:** Start the client in a new terminal:
python client.py --name karim (or any name )--cid 101
Expected output:
[CLIENT 101] Sent INIT seq=1 name=Hatem
[CLIENT 101] Received DATA seq=2: {"type":"welcome", ...}

**Step 3 (Optional):** Start more clients:
python client.py --name Sara --cid 202
python client.py --name Omar --cid 303


### Stopping the Programs
- In each terminal: press **Ctrl + C** to stop.
- In VS Code: click the **trash can 🗑️ icon** to kill a terminal.
- To restart cleanly: open a new terminal using the **+** button.

---

### Expected Output
- The **server** will display INIT and DATA logs from clients.
- The **clients** will send DATA and receive broadcasts from the server.

**Example Server Output:**
[SERVER] INIT from client_id=101 name=karim
[SERVER] DATA from client_id=101 seq=15: {'x': 9, 'y': 4, 'ts': 1730562464.13}
**Example Client Output:**
[CLIENT 101] Sent INIT seq=1 name=karim 
[CLIENT 101] <<< Broadcast DATA seq=45: {"type": "state", "from": 101, "payload": {"x": 9, "y": 4, ...}}

### Log Files
- server_output.log — records all messages sent/received by the server.
- client_output.log — records all client-side messages.
These logs were generated during the baseline local test.

