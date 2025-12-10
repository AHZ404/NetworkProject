# server.py - ROBUST UDP VERSION
import socket
import time
import struct
import threading
import zlib
import csv
from datetime import datetime
from typing import Dict, Tuple, Any

try:
    import psutil
except ImportError:
    psutil = None

from common import *
from game import GridClashGame


class ClientInfo:
    def __init__(self, addr, player_id):
        self.addr = addr
        self.player_id = player_id
        self.connected_time = time.time()
        self.last_heartbeat = time.time()
        self.last_snapshot_time = 0
        self.avg_latency = 0
        self.sequence_counter = 0
        self.last_acked_snapshot_id = 0


class Server:
    def __init__(self):
        # UDP Socket Only
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        self.sock.bind(('0.0.0.0', PORT))
        self.sock.setblocking(False)

        self.game = GridClashGame()

        # Client management
        self.clients: Dict[tuple, ClientInfo] = {}
        self.player_to_addr: Dict[int, tuple] = {}
        self.clients_lock = threading.Lock()

        # Snapshot history buffer
        self.snapshot_history: Dict[int, Tuple[bool, bytes]] = {}
        self.snapshot_id = 0

        self.running = True
        self.start_time = time.time()
        self.last_snapshot_time = time.time()
        self.last_event_spawn_time = time.time()

        # Statistics
        self.stats = {
            'packets_sent': 0, 'packets_received': 0,
            'bytes_sent': 0, 'bytes_received': 0,
        }

        # Logging
        self.log_file = open('server_log.txt', 'w', encoding='utf-8', errors='replace')

        # Metrics
        self.metrics_file = open('server_metrics.csv', 'w', newline='')
        self.csv_writer = csv.writer(self.metrics_file)
        self.csv_writer.writerow(['timestamp', 'cpu_percent', 'clients_connected',
                                  'packets_sent', 'packets_received', 'bandwidth_kbps'])
        self.last_metrics_time = time.time()
        self.last_bytes_total = 0

        # --- LOG ACTION: Listening ---
        self.log(f"Server initialized. Listening on port {PORT}...", prefix="[STARTUP]")

    def _get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[SERVER]"):
        timestamp = self._get_timestamp()
        safe_msg = ''.join(char for char in str(msg) if ord(char) < 128)
        full_msg = f"{timestamp} - {prefix} {safe_msg}"
        print(full_msg)
        self.log_file.write(full_msg + "\n")
        self.log_file.flush()

    def send_packet_safe(self, addr, msg_type, snapshot_id, seq_num, payload=b''):
        try:
            if msg_type == MSG_TYPES['SNAPSHOT'] and len(payload) > 200:
                compressed = zlib.compress(payload, level=1)
                if len(compressed) < len(payload):
                    payload = compressed
                    msg_type = MSG_TYPES['COMPRESSED']

            if msg_type not in [MSG_TYPES['COMPRESSED'], MSG_TYPES['FULL_SNAPSHOT']] and len(payload) > 200:
                compressed = zlib.compress(payload, level=1)
                if len(compressed) < len(payload):
                    payload = compressed

            timestamp_ms = int(time.time() * 1000)
            if len(payload) > (MAX_PACKET_SIZE - HEADER_SIZE):
                self.log(f"WARN: Payload size {len(payload)} exceeds max", prefix="[WARN]")
                return False, 0

            payload_len = len(payload)
            checksum = compute_checksum(payload)

            header = create_header(msg_type, snapshot_id, seq_num, payload_len, checksum)
            data = header + payload

            self.sock.sendto(data, addr)
            self.stats['packets_sent'] += 1
            self.stats['bytes_sent'] += len(data)
            return True, timestamp_ms
        except Exception:
            return False, 0

    def _get_full_grid_payload(self) -> bytes:
        payload = bytearray()
        for row in self.game.grid:
            payload.extend(row)
        payload.append(len(self.game.players))
        for pid, pos in self.game.players.items():
            payload.extend(struct.pack('BBB', pid, pos[0], pos[1]))
        scores_arr = self.game._get_scores_array()
        payload.extend(struct.pack('BBBB', scores_arr[1], scores_arr[2], scores_arr[3], scores_arr[4]))
        active_events = [e for e in self.game.events.values() if not e.collected]
        payload.append(len(active_events))
        for event in active_events:
            payload.extend(struct.pack('BBBB', event.event_id, event.event_type, event.row, event.col))
        current_time = time.time()
        payload.append(len(self.game.player_events))
        for pid, pevent in self.game.player_events.items():
            remaining_ms = int(pevent.get_remaining_time(current_time) * 1000)
            payload.extend(struct.pack('BB', pid, pevent.event_type))
            payload.extend(struct.pack('>I', remaining_ms))
        compressed = zlib.compress(bytes(payload), level=1)
        return compressed

    def handle_connect(self, addr):
        with self.clients_lock:
            if addr in self.clients:
                client = self.clients[addr]
                payload = struct.pack('BB', client.player_id, self.game.grid_size)
                self.send_packet_safe(addr, MSG_TYPES['WELCOME'], 0, 0, payload)
                # --- LOG ACTION: Re-Connection ---
                self.log(f"Client re-connected (Player {client.player_id}) from {addr}", prefix="[RECONNECT]")
                return

            if len(self.clients) >= 4:
                self.log(f"Server full, rejecting: {addr}", prefix="[WARN]")
                return

            player_id = None
            for pid in range(1, 5):
                if pid not in self.player_to_addr:
                    player_id = pid
                    break

            if not player_id: return

            if self.game.add_player(player_id):
                client_info = ClientInfo(addr, player_id)
                self.clients[addr] = client_info
                self.player_to_addr[player_id] = addr
                # --- LOG ACTION: Connection & Joining ---
                self.log(f"New connection accepted. Assigned Player ID {player_id} to {addr}", prefix="[CONNECT]")
            else:
                return

        payload = struct.pack('BB', player_id, self.game.grid_size)
        self.send_packet_safe(addr, MSG_TYPES['WELCOME'], 0, 0, payload)

        full_state_payload = self._get_full_grid_payload()
        self.send_packet_safe(addr, MSG_TYPES['FULL_SNAPSHOT'], self.snapshot_id,
                              self.clients[addr].sequence_counter, full_state_payload)
        self.clients[addr].sequence_counter += 1
        self.clients[addr].last_acked_snapshot_id = self.snapshot_id

    def broadcast_snapshot_to_all(self):
        current_time = time.time()
        if current_time - self.last_snapshot_time < UPDATE_INTERVAL: return

        self.last_snapshot_time = current_time
        self.snapshot_id += 1
        self.game.update_events()

        is_compressed, snapshot_data = self.game.get_compressed_snapshot(0)
        self.snapshot_history[self.snapshot_id] = (is_compressed, snapshot_data)

        min_acked_id = self.snapshot_id - MAX_SNAPSHOT_HISTORY
        if self.clients:
            min_acked_id = min(min_acked_id, min(c.last_acked_snapshot_id for c in self.clients.values()))

        keys_to_delete = [k for k in self.snapshot_history if k < min_acked_id]
        for k in keys_to_delete:
            del self.snapshot_history[k]

        if current_time - self.last_event_spawn_time >= EVENT_SPAWN_INTERVAL:
            event = self.game.spawn_event()
            if event:
                ep = struct.pack('BBBB', event.event_id, event.event_type, event.row, event.col)
                self.broadcast_message_to_all(MSG_TYPES['EVENT_SPAWN'], ep)
                # --- LOG ACTION: Event Spawn (optional context) ---
                self.log(f"Spawned Event {event.event_id} (Type {event.event_type}) at {event.row},{event.col}",
                         prefix="[GAME]")
            self.last_event_spawn_time = current_time

        with self.clients_lock:
            clients_list = list(self.clients.items())

        for addr, client_info in clients_list:
            msg_type = MSG_TYPES['COMPRESSED'] if is_compressed else MSG_TYPES['SNAPSHOT']
            if self.send_packet_safe(addr, msg_type, self.snapshot_id, client_info.sequence_counter, snapshot_data)[0]:
                client_info.sequence_counter += 1
                client_info.last_snapshot_time = current_time

    def broadcast_message_to_all(self, msg_type, payload=b''):
        with self.clients_lock:
            clients_list = list(self.clients.keys())
        for addr in clients_list:
            client = self.clients.get(addr)
            if client:
                self.send_packet_safe(addr, msg_type, 0, client.sequence_counter, payload)
                client.sequence_counter += 1

    def receive_packets(self):
        try:
            while True:
                data, addr = self.sock.recvfrom(4096)
                self.handle_packet(data, addr)
                self.stats['packets_received'] += 1
                self.stats['bytes_received'] += len(data)
        except BlockingIOError:
            pass
        except Exception:
            pass

    def handle_packet(self, data, addr):
        if len(data) < HEADER_SIZE: return
        try:
            header = parse_header(data[:HEADER_SIZE])
            if not header: return
            _, _, msg_type, snapshot_id, _, timestamp, payload_len, checksum = header
            payload = data[HEADER_SIZE:]
            if len(payload) != payload_len or compute_checksum(payload) != checksum: return

            if msg_type == MSG_TYPES['COMPRESSED']:
                payload = zlib.decompress(payload)
                msg_type = MSG_TYPES['SNAPSHOT']

            if msg_type == MSG_TYPES['CONNECT']:
                self.handle_connect(addr)
                return

            with self.clients_lock:
                client = self.clients.get(addr)
            if not client: return

            latency = max(0, int(time.time() * 1000) - timestamp)
            client.last_heartbeat = time.time()
            client.avg_latency = client.avg_latency * 0.9 + latency * 0.1

            if msg_type == MSG_TYPES['MOVE']:
                if len(payload) == 1:
                    self.handle_move(client.player_id, struct.unpack('B', payload)[0])
            elif msg_type == MSG_TYPES['CLAIM']:
                self.handle_claim(client.player_id, addr)
            elif msg_type == MSG_TYPES['HEARTBEAT']:
                self.send_packet_safe(addr, MSG_TYPES['HEARTBEAT'], 0, client.sequence_counter)
                client.sequence_counter += 1
            elif msg_type == MSG_TYPES['ACK_SNAPSHOT']:
                client.last_acked_snapshot_id = max(client.last_acked_snapshot_id, snapshot_id)

        except Exception:
            pass

    def handle_move(self, player_id, direction):
        result = self.game.move_player(player_id, direction)

        if result['success']:
            # --- LOG ACTION: Change of client position ---
            old_pos = result['old_position']
            new_pos = result['new_position']
            self.log(f"Player {player_id} moved from {old_pos} to {new_pos}", prefix="[MOVE]")

            if result.get('event_collected'):
                ed = result['event_collected']
                # --- LOG ACTION: Client got special boost ---
                self.log(f"Player {player_id} collected BOOST (Type {ed['event_type']}) at {new_pos}!",
                         prefix="[BOOST]")

                pl = struct.pack('BBBB', ed['event_id'], ed['event_type'], player_id, 0)
                self.broadcast_message_to_all(MSG_TYPES['EVENT_COLLECT'], pl)

    def handle_claim(self, player_id, addr):
        result = self.game.claim_cell(player_id)
        msg_type = MSG_TYPES['ACK'] if result['success'] else MSG_TYPES['NACK']
        row, col = result['position']

        if result['success']:
            # --- LOG ACTION: Client claim a block ---
            self.log(f"Player {player_id} successfully CLAIMED block at ({row}, {col})", prefix="[CLAIM]")

        with self.clients_lock:
            client = self.clients.get(addr)
            if client:
                self.send_packet_safe(addr, msg_type, 0, client.sequence_counter, struct.pack('BB', row, col))
                client.sequence_counter += 1
        if result.get('game_over'): self.handle_game_over(result)

    def handle_game_over(self, result):
        winner = result['winner']
        reason = result.get('win_reason', 'Game Over').encode('utf-8')
        scores = result['scores']
        payload = struct.pack('BB', winner, len(reason)) + reason
        payload += struct.pack('BBBB', scores[1], scores[2], scores[3], scores[4])
        self.broadcast_message_to_all(MSG_TYPES['GAME_OVER'], payload)
        self.log(f"Game Over. Winner: {winner}")
        threading.Timer(10.0, self.initiate_shutdown).start()

    def initiate_shutdown(self):
        self.running = False

    def check_client_timeouts(self):
        curr = time.time()
        to_remove = []
        with self.clients_lock:
            for addr, client in self.clients.items():
                if curr - client.last_heartbeat > 60.0:
                    to_remove.append((addr, client.player_id))
        for addr, pid in to_remove:
            self.disconnect_client(addr, pid, "timeout")

    def disconnect_client(self, addr, pid, reason):
        with self.clients_lock:
            if addr in self.clients: del self.clients[addr]
            if pid in self.player_to_addr: del self.player_to_addr[pid]
        if pid in self.game.players: self.game.players.pop(pid)
        self.log(f"Player {pid} disconnected: {reason}", prefix="[DISCONNECT]")

    def update_metrics(self):
        curr = time.time()
        if curr - self.last_metrics_time >= 1.0:
            total_bytes = self.stats['bytes_sent'] + self.stats['bytes_received']
            bw = ((total_bytes - self.last_bytes_total) * 8) / (1000 * (curr - self.last_metrics_time))
            cpu = psutil.cpu_percent() if psutil else 0.0
            with self.clients_lock: count = len(self.clients)
            self.csv_writer.writerow([f"{curr:.2f}", f"{cpu:.1f}", count,
                                      self.stats['packets_sent'], self.stats['packets_received'], f"{bw:.2f}"])
            self.metrics_file.flush()
            self.last_metrics_time = curr
            self.last_bytes_total = total_bytes

    def run(self):
        last_chk = time.time()
        while self.running:
            try:
                self.receive_packets()
                self.broadcast_snapshot_to_all()
                self.update_metrics()
                if time.time() - last_chk > 10.0:
                    self.check_client_timeouts()
                    last_chk = time.time()
                time.sleep(0.01)
            except KeyboardInterrupt:
                self.running = False
            except Exception:
                pass
        self.log_file.close()
        self.metrics_file.close()
        self.sock.close()


if __name__ == '__main__': Server().run()