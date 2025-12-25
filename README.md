# **GUDP (Gaming UDP Protocol) - A Networked Multiplayer Game Simulation Framework**

## **Overview**

GUDP is a sophisticated network gaming simulation framework that implements a custom UDP-based protocol for multiplayer games. This project demonstrates **real-time networked game architecture** with focus on network optimization, prediction, reconciliation, and quantitative performance analysis. It simulates a competitive "Grid Clash" game where 4 players compete to claim territory on a 20×20 grid while collecting power-ups.

-----

## **Demo Video Link**

![Grid Clash Demo](https://drive.google.com/file/d/1Kugvzer4QX9O3yfkdaz2hlo2g_nUn8qi/view?usp=sharing)

-----

## **Core Architecture**

### **Protocol Design**
- **Custom Protocol Header**: Binary header with protocol ID (`GUDP`), versioning, message types, sequencing, timestamps, and CRC32 checksums
- **14 Message Types**: From connection handshake to game state synchronization and events
- **Reliable UDP Implementation**: Sequence numbers, acknowledgments, and retransmission logic without TCP overhead
- **Compression Support**: Automatic zlib compression for large payloads (snapshots > 100 bytes)

### **Key Technical Innovations**
1. **Delta Encoding**: Transmits only changed game state between snapshots
2. **Client-side Prediction**: Local movement prediction with server reconciliation
3. **Position Interpolation**: Smooth visual updates between server snapshots
4. **Event System**: Server-spawned power-ups (STAR events) that enable territory stealing
5. **Comprehensive Metrics**: Real-time logging of latency, jitter, position error, and bandwidth

## **Components**

### **1. Server (`server.py`)**
- **UDP Server**: Non-blocking socket with configurable buffer sizes
- **Game State Manager**: Maintains authoritative game state for all clients
- **Client Management**: Handles up to 4 concurrent players with heartbeat detection
- **Snapshot System**: 30Hz update rate with history buffer (150 snapshots, ~5 seconds)
- **Logging Pipeline**:
  - `server_log.txt`: Text logs of all game events
  - `server_position_log.csv`: **Ground truth** player positions (for error analysis)
  - `server_metrics.csv`: Server performance metrics (CPU, clients, bandwidth)

### **2. Client (`client.py`)**
- **Prediction & Reconciliation**: Local movement prediction with server correction
- **Visual Rendering**: PyGame-based visualization with smooth interpolation
- **Dual Logging**:
  - `client_{ID}_position_log.csv`: Client-side perceived positions
  - `client_{ID}_metrics.csv`: Network metrics (latency, jitter, position error)
- **Auto Mode**: Programmatic player for automated testing

### **3. Game Logic (`game.py`)**
- **Grid-based Game**: 20×20 cell grid with player movement and territory claiming
- **Score System**: Players earn points by claiming cells (goal: 200 points)
- **Event System**:
  - **STAR Events**: Temporarily enable stealing enemy territory (3-second duration)
  - **Event Spawning**: Random events every 3 seconds (max 5 active)
- **Optimization Features**:
  - Movement validation caching
  - State delta calculation for efficient updates
  - Automatic compression for large state changes

### **4. Common Library (`common.py`)**
- **Protocol Constants**: Message types, header format, network settings
- **Sequence Manager**: Reliable UDP implementation with sliding window (32 packets)
- **Checksum Computation**: CRC32 for data integrity verification
- **Network Configuration**: Default 30Hz update rate, 1200 byte MTU

### **5. Automation & Analysis Tools**
- **`baseline.py`**: Automated test launcher (starts server + 4 clients)
- **`process_logs.py`**: Advanced data analysis and visualization
- **Experiment Tracking**: Historical performance across different update rates

-----

## **Network Protocol Details**

### **Packet Header Format**
```
Offset  Size  Type      Description
------  ----  --------  ---------------
0       4     char[4]   Protocol ID: 'GUDP'
4       1     uint8     Protocol Version: 2
5       1     uint8     Message Type (see MSG_TYPES)
6       4     uint32    Snapshot ID (server increment)
10      4     uint32    Sequence Number (client increment)
14      8     uint64    Timestamp (ms since epoch)
22      2     uint16    Payload Length (0-65535)
24      4     uint32    CRC32 Checksum
Total: 28 bytes
```

### **Message Types**
```
0:  CONNECT          - Client connection request
1:  WELCOME          - Server assigns player ID
2:  SNAPSHOT         - Game state delta update
3:  MOVE             - Player movement input
4:  CLAIM            - Territory claim attempt
5:  ACK              - Claim success
6:  NACK             - Claim failure
7:  GAME_OVER        - Game conclusion with stats
8:  EVENT_SPAWN      - New event on grid
9:  EVENT_COLLECT    - Event collected by player
10: HEARTBEAT        - Connection keep-alive
11: ACK_SNAPSHOT     - Acknowledge snapshot receipt
12: RESEND_REQUEST   - Request missing data
13: COMPRESSED       - Compressed payload flag
14: FULL_SNAPSHOT    - Complete state for new clients
```

-----

## **Performance Analysis Framework**

### **Quantitative Metrics Collected**

#### **Position Error Analysis**
- **Definition**: Euclidean distance between server ground truth and client display position
- **Data Sources**: `server_position_log.csv` vs `client_{ID}_position_log.csv`
- **Calculation**: Linear interpolation to align timestamps, then error computation
- **Output**: Mean, median, 95th percentile error (in grid cells)

#### **Network Metrics**
- **Latency**: Round-trip time (server timestamp to client receipt)
- **Jitter**: Inter-arrival time variation
- **Packet Loss**: Derived from sequence number gaps
- **Bandwidth**: Estimated from packet sizes and rates

#### **Experiment Automation**
- **Update Rate Testing**: Modify `UPDATE_HZ` in `common.py` (10, 30, 60 Hz)
- **Historical Tracking**: `experiment_history.csv` stores results across runs
- **Trend Visualization**: `error_vs_rate.png` shows error vs update frequency

### **Analysis Outputs**
1. **`error_vs_rate.png`**: Position error vs update rate graph
2. **`latency_analysis.png`**: Latency trends over time per player
3. **Console Statistics**: Comprehensive performance summary

-----

## **Setup and Usage**

### **Prerequisites**
```bash
pip install pygame pandas matplotlib scipy psutil
```

### **Running the System**

#### **1. Quick Start (Automated)**
```bash
python baseline.py
```
- Starts server + 4 auto-mode clients
- Clients automatically move and claim territory
- Generates all log files automatically

#### **2. Manual Testing**
```bash
# Terminal 1: Start server
python server.py

# Terminal 2-5: Start clients (each in separate terminal)
python client.py          # Manual control with arrow keys + spacebar
python client.py auto    # Automated client
```

#### **3. Performance Analysis**
```bash
# After running a test session:
python process_logs.py
```
This analyzes logs and generates:
- Position error statistics
- Latency/jitter analysis
- Updated performance graphs
- Historical experiment tracking

-----

## **Key Features in Detail**

### **1. Reliable UDP Implementation**
- **Sequence Numbers**: 32-bit wrapping sequence for packet ordering
- **Sliding Window**: 32-packet window for loss detection
- **Selective Retransmission**: Only retransmit lost packets
- **Acknowledgments**: Explicit ACKs for critical messages (claims, snapshots)

### **2. State Synchronization**
- **Server Authority**: Single source of truth for game state
- **Client Prediction**: Local movement prediction for responsive controls
- **Reconciliation**: Server corrections applied to predicted state
- **Interpolation**: Smooth visual movement between snapshots

### **3. Compression Strategy**
- **Threshold-based**: Compress payloads >100 bytes
- **Two-stage**: Try compression, send compressed if <80% of original size
- **Delta Encoding**: Send only changed grid cells and positions
- **Full Snapshots**: Complete state for new clients or resynchronization

### **4. Game Mechanics**
- **Territory Claiming**: Stand on unclaimed cell, press SPACE to claim
- **Movement**: Arrow keys (up/down/left/right)
- **STAR Power-up**: Collect gold star, temporarily steal enemy cells by moving over them
- **Win Conditions**: First to 200 points OR all cells claimed

## Data Flow Diagram

```mermaid
graph TD
    subgraph Client Node
        Input[User Input] -->|Immediate| P_State[Predicted State]
        Input -->|UDP Packet| Net_Out[Network Output]
        Net_In[Network Input] -->|Snapshot| R_State[Reconciliation Logic]
        R_State --> P_State
        P_State --> Render[PyGame Renderer]
    end

    subgraph Server Node
        S_Net_In[Network Input] -->|Move Command| G_Logic[Game Logic]
        G_Logic -->|Update Grid| Auth_State[Authoritative State]
        Auth_State -->|Delta Compression| Snap[Snapshot Generator]
        Snap -->|UDP Broadcast| S_Net_Out[Network Output]
    end

    Net_Out -.->|Latency/Jitter| S_Net_In
    S_Net_Out -.->|Packet Loss| Net_In
```

-----

## **Performance Optimization Techniques**

### **Network Optimization**
1. **Delta Encoding**: Transmit only changed game state
2. **Payload Compression**: zlib for large game states
3. **Snapshot Batching**: 30Hz updates with history buffer
4. **Selective Reliability**: Only critical messages use ACKs

### **Client-side Optimizations**
1. **Prediction**: Immediate response to player input
2. **Interpolation**: Smooth visual updates between snapshots
3. **Event Buffering**: Client-side event queue for network spikes
4. **Movement Caching**: Cache valid movement patterns

### **Server-side Optimizations**
1. **Non-blocking I/O**: Efficient socket handling
2. **State Hashing**: Quick change detection for delta encoding
3. **Client State Tracking**: Per-client snapshot acknowledgment
4. **Memory Management**: Limited history buffer (5 seconds)

-----

## **Experimental Framework**

### **Controlled Variables**
- **Update Rate**: Configurable in `common.py` (default: 30Hz)
- **Network Conditions**: Can be emulated with NetEm (not included)
- **Player Count**: Fixed at 4 players (configurable)
- **Game Duration**: Until victory condition (200 points or full grid)

### **Dependent Variables**
1. **Position Error**: Primary metric for synchronization quality
2. **Network Latency**: End-to-end packet delivery time
3. **Jitter**: Variation in packet inter-arrival times
4. **CPU Utilization**: Server load under different conditions
5. **Bandwidth Usage**: Network traffic volume

### **Analysis Methodology**
1. **Temporal Alignment**: Linear interpolation to match server/client timestamps
2. **Error Calculation**: Euclidean distance in grid units
3. **Statistical Analysis**: Mean, median, percentile calculations
4. **Trend Identification**: Error vs update rate correlation

-----

## Project Structure

```text
/
├── baseline.py          # Orchestrator for running experiments
├── client.py            # Game client (PyGame, Prediction)
├── common.py            # Config, Constants, Protocol headers
├── game.py              # Core Logic (Movement, Collision, Events)
├── process_logs.py      # Data Analysis & Plotting
├── server.py            # UDP Server & State Authority
├── server_log.txt       # Runtime logs
├── *.csv                # Generated metric data
└── *.png                # Generated analysis graphs
```
-----

## **Troubleshooting**

### **Common Issues**

1. **Port Already in Use**: Change `PORT` in `common.py` or kill existing process
2. **Missing Logs**: Ensure server runs before clients
3. **High Position Error**: Check network conditions, reduce update rate
4. **Client Disconnections**: Adjust heartbeat timeout in server

### **Debug Mode**
Set verbose logging in each component:
- Server: Add `self.log(f"Debug: {msg}")` calls
- Client: Enable detailed packet logging in `handle_packet()`
- Game: Monitor prediction/reconciliation events

-----

**Project Maintainer**: Networked Systems Research Group  
**Last Updated**: December 2025  
**Version**: 2.0 (GUDP Protocol v2)



