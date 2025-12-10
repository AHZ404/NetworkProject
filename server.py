# server.py - FINAL FIXED VERSION
import socket
import time
import struct
import threading
import zlib
from datetime import datetime
from collections import deque
from typing import Dict, Tuple

# Import from common and game modules
from common import *
from game import GridClashGame


class ClientInfo:
    """Lightweight client information"""

    def __init__(self, addr, player_id):
        self.addr = addr
        self.player_id = player_id
        self.connected_time = time.time()
        self.last_heartbeat = time.time()
        self.last_snapshot_time = 0
        self.avg_latency = 0
        self.sequence_counter = 0


class Server:
    def __init__(self):
        # Socket setup
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        self.sock.bind(('0.0.0.0', PORT))
        self.sock.setblocking(False)

        # Game instance
        self.game = GridClashGame()

        # Client management (protected by lock)
        self.clients: Dict[tuple, ClientInfo] = {}
        self.player_to_addr: Dict[int, tuple] = {}
        self.clients_lock = threading.Lock()

        # Server state
        self.snapshot_id = 0
        self.running = True

        # Timing
        self.start_time = time.time()
        self.last_snapshot_time = time.time()
        self.last_event_spawn_time = time.time()

        # Statistics
        self.stats = {
            'packets_sent': 0,
            'packets_received': 0,
            'bytes_sent': 0,
            'bytes_received': 0,
        }

        # Logging - Open with UTF-8 encoding for safety
        self.log_file = open('server_log.txt', 'w', encoding='utf-8', errors='replace')

        print(f"[START] Server started on port {PORT} (Optimized for 4 players)")

    def _get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[SERVER]"):
        """Safe logging"""
        timestamp = self._get_timestamp()

        # Remove any non-ASCII characters from message
        safe_msg = ''.join(char for char in str(msg) if ord(char) < 128)

        full_msg = f"{timestamp} - {prefix} {safe_msg}"

        # Always print to console for monitoring
        print(full_msg)

        # Write to file
        self.log_file.write(full_msg + "\n")
        self.log_file.flush()

    def send_packet_safe(self, addr, msg_type, snapshot_id, seq_num, payload=b''):
        """Safe packet sending with error handling"""
        try:
            # Quick compression check
            if len(payload) > 200 and msg_type not in [MSG_TYPES['HEARTBEAT'], MSG_TYPES['WELCOME']]:
                compressed = zlib.compress(payload, level=1)
                if len(compressed) < len(payload):
                    payload = compressed
                    msg_type = MSG_TYPES['COMPRESSED']

            timestamp = int(time.time() * 1000)
            payload_len = len(payload)
            checksum = compute_checksum(payload)

            header = create_header(msg_type, snapshot_id, seq_num, payload_len, checksum)
            data = header + payload

            # Send with timeout
            self.sock.settimeout(0.5)
            self.sock.sendto(data, addr)
            self.sock.setblocking(False)

            # Update stats
            self.stats['packets_sent'] += 1
            self.stats['bytes_sent'] += len(data)

            return True

        except socket.timeout:
            self.log(f"Timeout sending to {addr}", prefix="[WARN]")
            return False
        except ConnectionResetError:
            # Client disconnected, remove it
            with self.clients_lock:
                if addr in self.clients:
                    client_info = self.clients[addr]
                    self.log(f"Client {client_info.player_id} disconnected (connection reset)", prefix="[DISCONNECT]")
                    self.disconnect_client(addr, client_info.player_id, "connection reset")
            return False
        except Exception as e:
            self.log(f"Send error to {addr}: {e}", prefix="[ERROR]")
            return False

    def broadcast_snapshot_to_all(self):
        """Broadcast game snapshot to ALL connected clients"""
        current_time = time.time()

        # Only broadcast at 30Hz
        if current_time - self.last_snapshot_time < UPDATE_INTERVAL:
            return

        self.last_snapshot_time = current_time
        self.snapshot_id += 1

        # Update game events
        self.game.update_events()

        # Generate snapshot data once
        is_compressed, snapshot_data = self.game.get_compressed_snapshot(0)

        # Handle event spawning
        if current_time - self.last_event_spawn_time >= EVENT_SPAWN_INTERVAL:
            event = self.game.spawn_event()
            if event:
                self.log(f"Spawned Star at ({event.row},{event.col})", prefix="[EVENT]")
                event_payload = struct.pack('BBBB', event.event_id, event.event_type,
                                            event.row, event.col)
                self.broadcast_message_to_all(MSG_TYPES['EVENT_SPAWN'], event_payload)
            self.last_event_spawn_time = current_time

        # Get all clients quickly
        with self.clients_lock:
            clients_list = list(self.clients.items())

        # Send snapshot to each client
        for addr, client_info in clients_list:
            try:
                # Send snapshot
                msg_type = MSG_TYPES['COMPRESSED'] if is_compressed else MSG_TYPES['SNAPSHOT']
                success = self.send_packet_safe(addr, msg_type, self.snapshot_id,
                                                client_info.sequence_counter, snapshot_data)

                if success:
                    client_info.sequence_counter += 1
                    client_info.last_snapshot_time = current_time

            except Exception as e:
                self.log(f"Failed to send snapshot to {addr}: {e}", prefix="[ERROR]")

    def broadcast_message_to_all(self, msg_type, payload=b''):
        """Broadcast a message to all clients"""
        with self.clients_lock:
            clients_list = list(self.clients.keys())

        for addr in clients_list:
            try:
                with self.clients_lock:
                    client_info = self.clients.get(addr)
                    if client_info:
                        seq = client_info.sequence_counter
                        self.send_packet_safe(addr, msg_type, 0, seq, payload)
                        client_info.sequence_counter += 1
            except:
                pass

    def handle_connect(self, addr):
        """Handle new client connection"""
        with self.clients_lock:
            # Check if already connected
            if addr in self.clients:
                client_info = self.clients[addr]
                client_info.last_heartbeat = time.time()
                self.log(f"Client reconnected: {addr[0]}:{addr[1]}", prefix="[CONNECT]")
                return

            # Check server capacity
            if len(self.clients) >= 4:
                self.log(f"Server full, rejecting: {addr[0]}:{addr[1]}", prefix="[WARN]")
                return

            # Find free player ID
            player_id = None
            for pid in range(1, 5):
                if pid not in self.player_to_addr:
                    player_id = pid
                    break

            if not player_id:
                self.log("No free player IDs", prefix="[ERROR]")
                return

            # Add player to game
            if not self.game.add_player(player_id):
                self.log(f"Failed to add player {player_id} to game", prefix="[ERROR]")
                return

            # Create client info
            client_info = ClientInfo(addr, player_id)
            self.clients[addr] = client_info
            self.player_to_addr[player_id] = addr

            self.log(f"Player {player_id} connected from {addr[0]}:{addr[1]}", prefix="[CONNECT]")

        # Send welcome message
        payload = struct.pack('BB', player_id, self.game.grid_size)
        self.send_packet_safe(addr, MSG_TYPES['WELCOME'], 0, 0, payload)

        # Send current events
        game_state = self.game.get_state()
        for event_id, event_data in game_state['events'].items():
            if not event_data['collected']:
                event_payload = struct.pack('BBBB', event_id, event_data['type'],
                                            event_data['row'], event_data['col'])
                self.send_packet_safe(addr, MSG_TYPES['EVENT_SPAWN'], 0, 1, event_payload)

    def handle_move(self, player_id, direction):
        """Handle player movement"""
        with self.clients_lock:
            if player_id not in self.player_to_addr:
                return

        # Process move in game
        result = self.game.move_player(player_id, direction)

        # Broadcast event collection if needed
        if result.get('success') and result.get('event_collected'):
            event_data = result['event_collected']
            collect_payload = struct.pack('BBBB',
                                          event_data['event_id'],
                                          event_data['event_type'],
                                          player_id, 0)
            self.broadcast_message_to_all(MSG_TYPES['EVENT_COLLECT'], collect_payload)

            # Special announcement for star collection
            if event_data['event_type'] == EVENT_STAR:
                self.log(f"Player {player_id} collected a STAR!", prefix="[EVENT]")

    def handle_claim(self, player_id, addr):
        """Handle cell claim"""
        with self.clients_lock:
            if player_id not in self.player_to_addr:
                return

        # Process claim
        result = self.game.claim_cell(player_id)

        # Send response
        if result['success']:
            row, col = result['position']
            with self.clients_lock:
                client_info = self.clients.get(addr)
                if client_info:
                    seq = client_info.sequence_counter
                    self.send_packet_safe(addr, MSG_TYPES['ACK'], 0, seq,
                                          struct.pack('BB', row, col))
                    client_info.sequence_counter += 1

            self.log(f"P{player_id} claimed ({row},{col})", prefix="[CLAIM]")

            if result['game_over']:
                self.handle_game_over(result)
        else:
            row, col = result['position']
            with self.clients_lock:
                client_info = self.clients.get(addr)
                if client_info:
                    seq = client_info.sequence_counter
                    self.send_packet_safe(addr, MSG_TYPES['NACK'], 0, seq,
                                          struct.pack('BB', row, col))
                    client_info.sequence_counter += 1

    def handle_game_over(self, result):
        """Handle game over"""
        winner = result['winner']
        win_reason = result.get('win_reason', 'Game ended')
        scores = result['scores']

        win_reason_bytes = win_reason.encode('utf-8')
        payload = struct.pack('BB', winner, len(win_reason_bytes)) + win_reason_bytes
        payload += struct.pack('BBBB', scores[1], scores[2], scores[3], scores[4])

        self.broadcast_message_to_all(MSG_TYPES['GAME_OVER'], payload)

        self.log(f"GAME OVER! Winner: Player {winner} ({win_reason})", prefix="[GAME]")

        # Auto-shutdown after 10 seconds
        threading.Timer(10.0, self.initiate_shutdown).start()

    def initiate_shutdown(self):
        self.log("Initiating graceful shutdown...", prefix="[SHUTDOWN]")
        self.running = False

    def check_client_timeouts(self):
        """Remove inactive clients"""
        current_time = time.time()
        to_remove = []

        with self.clients_lock:
            for addr, client_info in list(self.clients.items()):
                # 60 second timeout for all clients (bots might be slow)
                if current_time - client_info.last_heartbeat > 60.0:
                    to_remove.append((addr, client_info.player_id))

        for addr, player_id in to_remove:
            self.disconnect_client(addr, player_id, "timeout")

    def disconnect_client(self, addr, player_id, reason="disconnected"):
        """Cleanly disconnect a client"""
        with self.clients_lock:
            if addr in self.clients:
                del self.clients[addr]
            if player_id in self.player_to_addr:
                del self.player_to_addr[player_id]

        # Remove from game
        if player_id in self.game.players:
            self.game.players.pop(player_id)

        self.log(f"Player {player_id} {reason} ({addr[0]}:{addr[1]})", prefix="[DISCONNECT]")

    def receive_packets(self):
        """Non-blocking packet reception"""
        try:
            # Read all available packets
            while True:
                try:
                    data, addr = self.sock.recvfrom(4096)
                    self.handle_packet(data, addr)

                    # Update stats
                    self.stats['packets_received'] += 1
                    self.stats['bytes_received'] += len(data)

                except BlockingIOError:
                    # No more data
                    break
                except ConnectionResetError:
                    # Silently handle connection reset (already handled in send_packet_safe)
                    break
                except Exception as e:
                    # Don't log common errors to avoid spam
                    break

        except Exception as e:
            # Don't log loop errors to avoid spam
            pass

    def handle_packet(self, data, addr):
        """Process incoming packet"""
        if len(data) < HEADER_SIZE:
            return

        try:
            header = parse_header(data[:HEADER_SIZE])
            if not header:
                return

            protocol_id, version, msg_type, snapshot_id, seq_num, timestamp, payload_len, checksum = header

            # Validate protocol
            if protocol_id != PROTO_ID or version != VERSION:
                return

            payload = data[HEADER_SIZE:]
            if len(payload) != payload_len:
                return

            if compute_checksum(payload) != checksum:
                return

            # Handle compressed payload
            if msg_type == MSG_TYPES['COMPRESSED']:
                try:
                    payload = zlib.decompress(payload)
                    msg_type = MSG_TYPES['SNAPSHOT']
                except:
                    return

            # Calculate latency
            latency = max(0, int(time.time() * 1000) - timestamp)

            # Get client info
            with self.clients_lock:
                client_info = self.clients.get(addr)

            # Handle message types
            if msg_type == MSG_TYPES['CONNECT']:
                self.handle_connect(addr)

            elif msg_type == MSG_TYPES['HEARTBEAT']:
                if client_info:
                    client_info.last_heartbeat = time.time()
                    client_info.avg_latency = client_info.avg_latency * 0.9 + latency * 0.1
                    # Send response
                    with self.clients_lock:
                        seq = client_info.sequence_counter
                        self.send_packet_safe(addr, MSG_TYPES['HEARTBEAT'], 0, seq)
                        client_info.sequence_counter += 1

            elif msg_type == MSG_TYPES['ACK_SNAPSHOT']:
                # Just acknowledge, no processing needed
                pass

            elif msg_type == MSG_TYPES['RESEND_REQUEST']:
                # Ignore resend requests for now (snapshots are sent regularly)
                pass

            elif client_info:
                # Update heartbeat for any valid packet
                client_info.last_heartbeat = time.time()
                client_info.avg_latency = client_info.avg_latency * 0.9 + latency * 0.1

                # Process inputs immediately
                if msg_type == MSG_TYPES['MOVE']:
                    if len(payload) == 1:
                        direction = struct.unpack('B', payload)[0]
                        # Process immediately for responsiveness
                        self.handle_move(client_info.player_id, direction)

                elif msg_type == MSG_TYPES['CLAIM']:
                    # Process immediately
                    self.handle_claim(client_info.player_id, addr)

        except Exception as e:
            # Don't log packet handling errors to avoid spam
            pass

    def game_loop(self):
        """Main game processing loop"""
        last_timeout_check = time.time()
        stats_timer = time.time()

        while self.running:
            try:
                current_time = time.time()

                # 1. Process network packets
                self.receive_packets()

                # 2. Broadcast snapshots at 30Hz
                self.broadcast_snapshot_to_all()

                # 3. Check for client timeouts every 10 seconds
                if current_time - last_timeout_check > 10.0:
                    self.check_client_timeouts()
                    last_timeout_check = current_time

                # 4. Print stats every 30 seconds
                if current_time - stats_timer > 30.0:
                    with self.clients_lock:
                        client_count = len(self.clients)
                    self.log(f"Status: {client_count}/4 clients connected", prefix="[STATUS]")
                    stats_timer = current_time

                # 5. Small sleep to prevent CPU spinning
                time.sleep(0.01)  # ~100Hz polling

            except Exception as e:
                # Don't log loop errors
                pass

    def run(self):
        """Main server entry point"""
        print("\n" + "=" * 60)
        print("GRID CLASH SERVER - FIXED VERSION")
        print("=" * 60)
        print(f"Port: {PORT}")
        print(f"Max Players: 4")
        print(f"Update Rate: {UPDATE_HZ}Hz")
        print(f"Grid Size: {GRID_SIZE}x{GRID_SIZE}")
        print("=" * 60)
        print("Waiting for players...")
        print("Start clients with:")
        print("  Human: python client.py")
        print("  Bot:   python client.py auto BotName")
        print("=" * 60 + "\n")

        try:
            # Run everything in the main thread for simplicity
            self.game_loop()

        except KeyboardInterrupt:
            self.log("Shutdown requested by user", prefix="[INFO]")
            self.running = False
        except Exception as e:
            self.log(f"Server crashed: {e}", prefix="[CRITICAL]")
            self.running = False

        self.cleanup()

    def cleanup(self):
        """Clean shutdown"""
        runtime = time.time() - self.start_time

        # Get final game state
        game_state = self.game.get_state()

        # Close files
        self.log_file.close()

        try:
            self.sock.close()
        except:
            pass

        # Final statistics
        print("\n" + "=" * 60)
        print("SERVER SHUTDOWN STATISTICS")
        print("=" * 60)
        print(f"Runtime: {runtime:.1f} seconds")
        print(f"Packets Sent: {self.stats['packets_sent']}")
        print(f"Packets Received: {self.stats['packets_received']}")
        print(f"Final Scores:")
        for pid in range(1, 5):
            score = game_state['scores'].get(pid, 0)
            print(f"  Player {pid}: {score}/200")

        if game_state['game_over']:
            print(f"\nWinner: Player {game_state['winner']}")
            print(f"Reason: {game_state.get('win_reason', 'Game ended')}")

        print("=" * 60)


if __name__ == '__main__':
    server = Server()
    server.run()