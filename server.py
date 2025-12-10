# server.py
import socket
import time
import struct
import threading
import psutil
import zlib
from datetime import datetime
from collections import deque, defaultdict
from typing import Dict, Tuple, Optional, List, Set
import traceback

# Import from common and game modules
from common import *
from game import GridClashGame


class ClientInfo:
    """Information about a connected client"""

    def __init__(self, addr, player_id):
        self.addr = addr
        self.player_id = player_id
        self.connected_time = time.time()
        self.last_heartbeat = time.time()
        self.last_snapshot_seq = 0
        self.last_snapshot_time = 0
        self.avg_latency = 0
        self.packet_loss = 0
        self.quality_score = 100
        self.needs_full_update = True
        self.seq_manager = SequenceManager()
        self.pending_acks = {}

    def update_quality(self, latency, loss_rate):
        """Update connection quality metrics"""
        self.avg_latency = self.avg_latency * 0.7 + latency * 0.3
        self.packet_loss = self.packet_loss * 0.7 + loss_rate * 0.3

        # Calculate quality score (0-100)
        latency_penalty = min(50, self.avg_latency * 0.5)
        loss_penalty = min(50, self.packet_loss * 100)
        self.quality_score = max(10, 100 - latency_penalty - loss_penalty)

    def should_reduce_updates(self):
        """Determine if this client needs reduced update rate"""
        return self.quality_score < 50 or self.avg_latency > 200


class Server:
    def __init__(self):
        # Socket setup with large buffers
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        self.sock.bind(('0.0.0.0', PORT))
        self.sock.settimeout(0.1)  # Non-blocking with small timeout

        # Game instance
        self.game = GridClashGame()

        # Client management
        self.clients: Dict[tuple, ClientInfo] = {}  # addr -> ClientInfo
        self.player_to_addr: Dict[int, tuple] = {}  # player_id -> addr

        # Server state
        self.snapshot_id = 0
        self.seq_num = 0
        self.running = True
        self.lock = threading.Lock()
        self.snapshot_history = deque(maxlen=20)  # Keep last 20 snapshots

        # Timing
        self.start_time = time.time()
        self.last_metrics_time = time.time()
        self.last_cleanup_time = time.time()
        self.last_event_spawn_time = time.time()

        # Statistics
        self.stats = {
            'packets_sent': 0,
            'packets_received': 0,
            'bytes_sent': 0,
            'bytes_received': 0,
            'compression_savings': 0,
        }

        # Adaptive broadcast
        self.broadcast_intervals = {}  # addr -> interval
        self.min_interval = UPDATE_INTERVAL
        self.max_interval = UPDATE_INTERVAL * 3

        # Logging
        self.log_file = open('server_log.txt', 'w')
        self.position_log = open('server_position_log.csv', 'w')
        self.position_log.write('timestamp,snapshot_id,player_id,row,col\n')
        self.metrics_log = open('server_metrics.csv', 'w')
        self.metrics_log.write('timestamp,clients_connected,cpu_percent,memory_mb,bandwidth_kbps,avg_latency\n')

        print(f"Server initialized on port {PORT}")

    def _get_timestamp(self):
        """Get current timestamp"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[SERVER]"):
        """Enhanced logging with timestamp"""
        timestamp = self._get_timestamp()
        full_msg = f"{timestamp} - {prefix} {msg}"
        print(full_msg)
        self.log_file.write(full_msg + "\n")
        self.log_file.flush()

    def send_message(self, addr, msg_type, snapshot_id, seq_num, payload=b'', reliable=False):
        """Send message with optional reliability"""
        try:
            if reliable:
                # Store for potential retransmission
                client_info = self.clients.get(addr)
                if client_info:
                    client_info.pending_acks[seq_num] = (payload, time.time())
                    client_info.seq_manager.packet_sent(seq_num)

            # Check if we should compress
            is_compressed = False
            if len(payload) > 100 and msg_type not in [MSG_TYPES['HEARTBEAT'], MSG_TYPES['CONNECT'],
                                                       MSG_TYPES['WELCOME']]:
                compressed = zlib.compress(payload, level=1)
                if len(compressed) < len(payload):
                    payload = compressed
                    is_compressed = True

            if is_compressed:
                msg_type = MSG_TYPES['COMPRESSED']

            timestamp = int(time.time() * 1000)
            payload_len = len(payload)
            checksum = compute_checksum(payload)
            header = create_header(msg_type, snapshot_id, seq_num, payload_len, checksum)
            data = header + payload

            bytes_sent = self.sock.sendto(data, addr)

            # Update stats
            self.stats['packets_sent'] += 1
            self.stats['bytes_sent'] += len(data)

            # Log significant events
            if msg_type == MSG_TYPES['WELCOME'] and len(payload) >= 2:
                assigned_id, _ = struct.unpack('BB', payload[:2])
                self.log(f"Welcome sent to Player{assigned_id} at {addr[0]}:{addr[1]}")

        except Exception as e:
            self.log(f"Send error to {addr}: {e}")

    def broadcast_to_all(self, msg_type, payload=b'', reliable=False, exclude_addr=None):
        """Broadcast message to all connected clients"""
        with self.lock:
            for addr, client_info in list(self.clients.items()):
                if addr == exclude_addr:
                    continue
                self.send_message(addr, msg_type, 0, self.seq_num, payload, reliable)
                self.seq_num += 1

    def broadcast_snapshot(self):
        """Broadcast game state to all clients with adaptive rate"""
        with self.lock:
            current_time = time.time()
            self.snapshot_id += 1

            # Update game events
            self.game.update_events()

            # Get compressed snapshot
            is_compressed, snapshot_data = self.game.get_compressed_snapshot(0)

            # Store in history for resend requests
            self.snapshot_history.append({
                'seq': self.snapshot_id,
                'data': snapshot_data,
                'time': current_time,
                'compressed': is_compressed
            })

            # Check for event spawn
            if current_time - self.last_event_spawn_time >= EVENT_SPAWN_INTERVAL:
                event = self.game.spawn_event()
                if event:
                    self.log(f"Spawned Star event at ({event.row},{event.col})")
                    # Broadcast event spawn
                    event_payload = struct.pack('BBBB', event.event_id, event.event_type,
                                                event.row, event.col)
                    self.broadcast_to_all(MSG_TYPES['EVENT_SPAWN'], event_payload)
                self.last_event_spawn_time = current_time

            # Send to each client with adaptive timing
            clients_sent = 0
            for addr, client_info in list(self.clients.items()):
                # Check if it's time to send to this client
                interval = self.broadcast_intervals.get(addr, UPDATE_INTERVAL)
                if current_time - client_info.last_snapshot_time < interval:
                    continue

                # Adjust interval based on client quality
                if client_info.should_reduce_updates():
                    new_interval = min(self.max_interval, interval * 1.2)
                else:
                    new_interval = max(self.min_interval, interval * 0.9)
                self.broadcast_intervals[addr] = new_interval

                # Send snapshot
                self.send_message(addr, MSG_TYPES['SNAPSHOT'],
                                  self.snapshot_id, self.seq_num, snapshot_data, reliable=True)
                self.seq_num += 1

                client_info.last_snapshot_time = current_time
                client_info.last_snapshot_seq = self.snapshot_id
                clients_sent += 1

                # Log positions
                game_state = self.game.get_state()
                if client_info.player_id in game_state['players']:
                    row, col = game_state['players'][client_info.player_id]
                    self.position_log.write(
                        f"{current_time:.3f},{self.snapshot_id},{client_info.player_id},{row},{col}\n")

            self.position_log.flush()

            # Log if we sent any snapshots
            if clients_sent > 0 and self.snapshot_id % 10 == 0:
                game_state = self.game.get_state()
                player_count = len(game_state['players'])
                self.log(f"Sent snapshot {self.snapshot_id} to {clients_sent} players")

    def handle_connect(self, addr):
        """Handle new client connection"""
        with self.lock:
            # Check if already connected
            if addr in self.clients:
                self.log(f"Duplicate connection from {addr}, refreshing")
                client_info = self.clients[addr]
                # Resend welcome
                payload = struct.pack('BB', client_info.player_id, self.game.grid_size)
                self.send_message(addr, MSG_TYPES['WELCOME'], 0, 0, payload, reliable=True)
                return

            # Check server capacity
            if len(self.clients) >= 4:
                self.log(f"Server full, rejecting connection from {addr}")
                return

            # Assign player ID (1-4)
            for player_id in range(1, 5):
                if player_id not in self.player_to_addr:
                    break
            else:
                player_id = len(self.clients) + 1

            # Add player to game
            if not self.game.add_player(player_id):
                self.log(f"Failed to add player {player_id} to game")
                # Try to find empty spot in game
                for pid in range(1, 5):
                    if pid not in self.game.players:
                        if self.game.add_player(pid):
                            player_id = pid
                            break
                else:
                    self.log(f"Game is full, cannot add player")
                    return

            # Create client info
            client_info = ClientInfo(addr, player_id)
            self.clients[addr] = client_info
            self.player_to_addr[player_id] = addr

            # Send welcome message
            payload = struct.pack('BB', player_id, self.game.grid_size)
            self.send_message(addr, MSG_TYPES['WELCOME'], 0, 0, payload, reliable=True)

            # Send current events
            self.send_current_events(addr)

            # Log connection
            self.log(f"Player {player_id} connected from {addr[0]}:{addr[1]}")
            self.log(f"Total clients: {len(self.clients)}/4")

            # Force immediate snapshot
            client_info.needs_full_update = True
            client_info.last_snapshot_time = 0

    def send_current_events(self, addr):
        """Send all current events to client"""
        game_state = self.game.get_state()
        for event_id, event_data in game_state['events'].items():
            if not event_data['collected']:
                payload = struct.pack('BBBB', event_id, event_data['type'],
                                      event_data['row'], event_data['col'])
                self.send_message(addr, MSG_TYPES['EVENT_SPAWN'], 0, 0, payload)

    def handle_move(self, player_id, payload, timestamp):
        """Handle player move with event checking"""
        with self.lock:
            if len(payload) != 1:
                return

            direction = struct.unpack('B', payload)[0]
            dir_names = {0: 'UP', 1: 'DOWN', 2: 'LEFT', 3: 'RIGHT'}
            direction_name = dir_names.get(direction, 'UNKNOWN')

            result = self.game.move_player(player_id, direction)

            if result['success']:
                # Check for event collection
                if result.get('event_collected'):
                    event_data = result['event_collected']

                    # Broadcast event collection
                    collect_payload = struct.pack('BBBB',
                                                  event_data['event_id'],
                                                  event_data['event_type'],
                                                  player_id, 0)
                    self.broadcast_to_all(MSG_TYPES['EVENT_COLLECT'], collect_payload, reliable=True)

                    self.log(f"Player {player_id} collected Star at move {direction_name}")

            else:
                self.log(f"Player {player_id} move {direction_name} blocked")

    def handle_claim(self, player_id, addr):
        """Handle cell claim attempt"""
        with self.lock:
            result = self.game.claim_cell(player_id)

            if result['success']:
                row, col = result['position']
                self.send_message(addr, MSG_TYPES['ACK'], 0, 0,
                                  struct.pack('BB', row, col), reliable=True)

                # Log claim
                game_state = self.game.get_state()
                score = game_state['scores'].get(player_id, 0)
                self.log(f"Player {player_id} claimed ({row},{col}), score: {score}")

                if result['game_over']:
                    self.handle_game_over(result)
            else:
                row, col = result['position']
                self.send_message(addr, MSG_TYPES['NACK'], 0, 0,
                                  struct.pack('BB', row, col))
                self.log(f"Player {player_id} claim failed at ({row},{col})")

    def handle_game_over(self, result):
        """Handle game over condition"""
        winner = result['winner']
        win_reason = result.get('win_reason', 'Game ended')
        scores = result['scores']

        # Create game over payload
        win_reason_bytes = win_reason.encode('utf-8')
        payload = struct.pack('BB', winner, len(win_reason_bytes)) + win_reason_bytes
        payload += struct.pack('BBBB', scores[1], scores[2], scores[3], scores[4])

        # Broadcast to all clients
        self.broadcast_to_all(MSG_TYPES['GAME_OVER'], payload, reliable=True)

        self.log(f"GAME OVER! Winner: Player {winner} - {win_reason}")
        self.log(f"Final scores: P1={scores[1]}, P2={scores[2]}, P3={scores[3]}, P4={scores[4]}")

        # Schedule server shutdown
        self.log("Game over, server will shutdown in 10 seconds")
        threading.Timer(10.0, self.initiate_shutdown).start()

    def initiate_shutdown(self):
        """Gracefully shutdown server"""
        self.log("Initiating server shutdown...")
        self.running = False

    def check_timeouts(self):
        """Check for client timeouts and cleanup"""
        current_time = time.time()
        timed_out = []

        with self.lock:
            for addr, client_info in list(self.clients.items()):
                # Check heartbeat timeout (15 seconds)
                if current_time - client_info.last_heartbeat > 15.0:
                    timed_out.append((addr, client_info.player_id, "heartbeat timeout"))
                # Check snapshot timeout (30 seconds)
                elif current_time - client_info.last_snapshot_time > 30.0 and client_info.last_snapshot_time > 0:
                    timed_out.append((addr, client_info.player_id, "stale connection"))

            for addr, player_id, reason in timed_out:
                self.disconnect_client(addr, player_id, reason)

        # Periodic cleanup
        if current_time - self.last_cleanup_time > 30.0:
            self.cleanup_resources()
            self.last_cleanup_time = current_time

    def disconnect_client(self, addr, player_id, reason="disconnected"):
        """Disconnect a client"""
        with self.lock:
            if addr in self.clients:
                del self.clients[addr]

            if player_id in self.player_to_addr:
                del self.player_to_addr[player_id]

            # Remove player from game
            if player_id in self.game.players:
                self.game.players.pop(player_id)

            self.log(f"Player {player_id} {reason} from {addr[0]}:{addr[1]}")
            self.log(f"Remaining clients: {len(self.clients)}/4")

    def cleanup_resources(self):
        """Clean up old resources"""
        # Prune old snapshots from history
        current_time = time.time()
        while (self.snapshot_history and
               current_time - self.snapshot_history[0]['time'] > 30.0):
            self.snapshot_history.popleft()

        # Clear old pending acks
        for client_info in self.clients.values():
            current_time = time.time()
            expired_acks = []
            for seq_num, (_, send_time) in client_info.pending_acks.items():
                if current_time - send_time > 5.0:
                    expired_acks.append(seq_num)

            for seq_num in expired_acks:
                del client_info.pending_acks[seq_num]

    def update_metrics(self):
        """Update and log server metrics"""
        current_time = time.time()
        if current_time - self.last_metrics_time < 2.0:
            return

        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            memory_mb = psutil.Process().memory_info().rss / 1024 / 1024

            # Calculate bandwidth
            time_diff = current_time - self.last_metrics_time
            if time_diff > 0:
                bandwidth_kbps = (self.stats['bytes_sent'] * 8 / 1000) / time_diff
            else:
                bandwidth_kbps = 0

            # Calculate average latency
            avg_latency = 0
            if self.clients:
                latencies = [c.avg_latency for c in self.clients.values()]
                avg_latency = sum(latencies) / len(latencies)

            # Log metrics
            self.metrics_log.write(
                f"{current_time:.3f},{len(self.clients)},{cpu_percent:.1f},"
                f"{memory_mb:.1f},{bandwidth_kbps:.1f},{avg_latency:.1f}\n")
            self.metrics_log.flush()

            # Reset counters
            self.stats['bytes_sent'] = 0
            self.last_metrics_time = current_time

            # Log status periodically
            if int(current_time) % 10 == 0:
                game_state = self.game.get_state()
                grid_filled = sum(cell != 0 for row in game_state['grid'] for cell in row)
                total_cells = self.game.grid_size * self.game.grid_size
                fill_percent = (grid_filled / total_cells) * 100 if total_cells > 0 else 0

                self.log(f"Status: {len(self.clients)} players, "
                         f"grid: {grid_filled}/{total_cells} ({fill_percent:.1f}%), "
                         f"events: {len(game_state['events'])} active")

        except Exception as e:
            self.log(f"Metrics error: {e}")

    def handle_packet(self, data, addr):
        """Handle incoming packet"""
        self.log(f"DEBUG: Processing packet from {addr}, size: {len(data)} bytes")

        if len(data) < HEADER_SIZE:
            self.log(f"DEBUG: Packet too small: {len(data)} bytes")
            return

        try:
            # Debug: print first few bytes
            self.log(f"DEBUG: First 10 bytes: {data[:10].hex() if len(data) >= 10 else 'too short'}")

            header = parse_header(data[:HEADER_SIZE])
            if not header:
                self.log(f"DEBUG: Failed to parse header")
                # Print what we got
                if len(data) >= 4:
                    self.log(f"DEBUG: First 4 bytes as string: {data[:4]}")
                    self.log(f"DEBUG: Expected PROTO_ID: {PROTO_ID}")
                return

            protocol_id, version, msg_type, snapshot_id, seq_num, timestamp, payload_len, checksum = header

            self.log(f"DEBUG: Packet parsed - type: {msg_type}, seq: {seq_num}")

            # Validate protocol ID
            if protocol_id != PROTO_ID:
                self.log(f"DEBUG: Invalid protocol ID: got {protocol_id}, expected {PROTO_ID}")
                return

            if version != VERSION:
                self.log(f"Client {addr} using wrong version: {version}")
                return

            # Get payload
            payload = data[HEADER_SIZE:]
            if len(payload) != payload_len:
                self.log(f"Payload length mismatch from {addr}: expected {payload_len}, got {len(payload)}")
                return

            # Verify checksum
            if compute_checksum(payload) != checksum:
                self.log(f"Checksum mismatch from {addr}")
                return

            # Handle compressed payload
            if msg_type == MSG_TYPES['COMPRESSED']:
                try:
                    payload = zlib.decompress(payload)
                    msg_type = MSG_TYPES['SNAPSHOT']  # Assume snapshot after decompression
                except:
                    self.log(f"Decompression failed from {addr}")
                    return

            # Calculate latency
            latency = int(time.time() * 1000) - timestamp

            with self.lock:
                client_info = self.clients.get(addr)

                # Update heartbeat for any message from client
                if client_info:
                    client_info.last_heartbeat = time.time()
                    client_info.update_quality(latency, 0)

                # Handle message types
                if msg_type == MSG_TYPES['CONNECT']:
                    self.log(f"CONNECT received from {addr}")
                    self.handle_connect(addr)

                elif msg_type == MSG_TYPES['HEARTBEAT']:
                    # Already updated heartbeat above
                    self.log(f"DEBUG: Heartbeat from {addr}")
                    pass

                elif msg_type == MSG_TYPES['ACK_SNAPSHOT']:
                    if client_info:
                        client_info.seq_manager.ack_received(seq_num)

                elif msg_type == MSG_TYPES['RESEND_REQUEST']:
                    if client_info and len(payload) >= 4:
                        missing_seq = struct.unpack('I', payload[:4])[0]
                        self.handle_resend_request(addr, missing_seq)

                elif client_info:
                    # Handle game actions
                    if msg_type == MSG_TYPES['MOVE']:
                        self.handle_move(client_info.player_id, payload, timestamp)

                    elif msg_type == MSG_TYPES['CLAIM']:
                        self.handle_claim(client_info.player_id, addr)

                else:
                    # Unknown client sent message
                    self.log(f"Unknown client {addr} sent message type {msg_type}")
                    # Maybe send them a connect request?
                    if msg_type != MSG_TYPES['CONNECT']:
                        self.log(f"Telling unknown client to connect first")

        except Exception as e:
            self.log(f"Packet handling error from {addr}: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")

    def handle_resend_request(self, addr, missing_seq):
        """Handle client request for missing data"""
        client_info = self.clients.get(addr)
        if not client_info:
            return

        # Find requested snapshot in history
        for snapshot in self.snapshot_history:
            if snapshot['seq'] == missing_seq:
                msg_type = MSG_TYPES['COMPRESSED'] if snapshot['compressed'] else MSG_TYPES['SNAPSHOT']
                self.send_message(addr, msg_type,
                                  snapshot['seq'], 0, snapshot['data'], reliable=True)
                self.log(f"Resent snapshot {missing_seq} to Player {client_info.player_id}")
                break

    def broadcast_thread(self):
        """Main broadcast loop"""
        last_snapshot_time = time.time()

        while self.running:
            try:
                current_time = time.time()

                # Broadcast snapshot at appropriate interval
                if current_time - last_snapshot_time >= self.min_interval:
                    self.broadcast_snapshot()
                    last_snapshot_time = current_time

                # Check for timeouts
                self.check_timeouts()

                # Update metrics
                self.update_metrics()

                # Small sleep to prevent CPU hogging
                time.sleep(0.001)

            except Exception as e:
                self.log(f"Broadcast thread error: {e}")

    def run(self):
        """Main server loop"""
        self.log(f"Starting server on port {PORT}")
        self.log(f"Update rate: {UPDATE_HZ}Hz (adaptive)")
        self.log(f"Grid size: {self.game.grid_size}x{self.game.grid_size}")
        self.log(f"Win condition: First to {MAX_SCORE_TO_WIN} blocks")
        self.log(f"Compression: Enabled, Delta encoding: Enabled")

        print(f"\n{'=' * 60}")
        print(f"Grid Clash Server v2")
        print(f"Port: {PORT}, Update Rate: {UPDATE_HZ}Hz (adaptive)")
        print(f"Max Players: 4")
        print(f"Win Condition: First to {MAX_SCORE_TO_WIN} blocks")
        print(f"Special Event: ★ Star (steal enemy cells)")
        print(f"Waiting for players to connect...")
        print(f"{'=' * 60}\n")

        # Start broadcast thread
        broadcast_thread = threading.Thread(target=self.broadcast_thread, daemon=True)
        broadcast_thread.start()

        # Main receive loop - FIXED VERSION
        self.log("Server ready, listening for connections...")

        while self.running:
            try:
                # Set timeout to avoid blocking forever
                self.sock.settimeout(0.1)

                try:
                    # Receive packet
                    data, addr = self.sock.recvfrom(4096)

                    # Log EVERY packet received
                    self.log(f"RAW PACKET from {addr}: {len(data)} bytes")

                    # Update received stats
                    self.stats['packets_received'] += 1
                    self.stats['bytes_received'] += len(data)

                    # Process packet immediately (not in separate thread)
                    self.handle_packet(data, addr)

                except socket.timeout:
                    # Expected for non-blocking socket
                    continue

            except Exception as e:
                if self.running:  # Only log if still running
                    self.log(f"Receive loop error: {e}")
                    import traceback
                    self.log(f"Traceback: {traceback.format_exc()}")

        self.cleanup()

    def cleanup(self):
        """Cleanup resources on shutdown"""
        runtime = time.time() - self.start_time

        # Log final statistics
        self.log(f"Server runtime: {runtime:.1f} seconds")
        self.log(f"Packets sent: {self.stats['packets_sent']}, "
                 f"received: {self.stats['packets_received']}")

        # Game statistics
        game_state = self.game.get_state()
        self.log(f"Final scores: "
                 f"P1={game_state['scores'].get(1, 0)}, "
                 f"P2={game_state['scores'].get(2, 0)}, "
                 f"P3={game_state['scores'].get(3, 0)}, "
                 f"P4={game_state['scores'].get(4, 0)}")

        # Event statistics
        event_stats = {'Star': {'total': 0, 'collected': 0}}
        for event_id, event_data in game_state.get('events', {}).items():
            event_stats['Star']['total'] += 1
            if event_data['collected']:
                event_stats['Star']['collected'] += 1

        for event_name, stats in event_stats.items():
            collection_rate = (stats['collected'] / stats['total'] * 100) if stats['total'] > 0 else 0
            self.log(f"{event_name} events: {stats['collected']}/{stats['total']} collected ({collection_rate:.1f}%)")

        # Close files
        self.log_file.close()
        self.position_log.close()
        self.metrics_log.close()

        try:
            self.sock.close()
        except:
            pass

        print(f"\n{'=' * 60}")
        print(f"Server shutdown complete")
        print(f"Runtime: {runtime:.1f}s")
        print(f"Total packets: {self.stats['packets_sent']} sent, "
              f"{self.stats['packets_received']} received")
        print(f"Final scores: P1={game_state['scores'].get(1, 0)}, "
              f"P2={game_state['scores'].get(2, 0)}, "
              f"P3={game_state['scores'].get(3, 0)}, "
              f"P4={game_state['scores'].get(4, 0)}")
        print(f"{'=' * 60}")


if __name__ == '__main__':
    server = Server()
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Server interrupted by user")
        server.running = False
        server.cleanup()
    except Exception as e:
        print(f"\n[ERROR] Server crashed: {e}")
        import traceback

        traceback.print_exc()
        server.cleanup()