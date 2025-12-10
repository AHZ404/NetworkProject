# client.py - ROBUST UDP VERSION
import math
import socket
import time
import struct
import threading
import sys
import random
import pygame
import zlib
import csv
from datetime import datetime
from collections import deque
from common import *
from game import GridClashGame


class Client:
    def __init__(self, host=HOST, port=PORT, auto=False, client_name=None):
        # Network setup
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        self.sock.bind(('', 0))
        self.sock.settimeout(0.01)

        self.server_addr = (host, port)

        # LOGGING (Thread-Safe)
        self.log_file = open('client_log.txt', 'w')
        self.log_lock = threading.Lock()

        self.game = GridClashGame()
        self.player_id = None
        self.display_positions = {}

        self.client_name = client_name or f"Client_{random.randint(100, 999)}"
        self.snapshot_id = 0
        self.pending_claim = False
        self.claim_timer = 0.0
        self.claim_timeout = 0.5
        self.running = True
        self.auto = auto
        self.seq_num = 0

        self.seq_manager = SequenceManager()
        self.events = {}
        self.input_buffer = deque(maxlen=60)
        self.predicted_state = None
        self.frame_times = deque(maxlen=60)

        self.connected = False
        self.connection_start = 0
        self.last_heartbeat_time = 0
        self.avg_latency = 0
        self.packet_loss = 0

        # Metrics Tracking (Placeholders for required metrics)
        self.last_receive_time_ms = 0
        self.inter_arrival_times = deque(maxlen=30)
        self.server_position = {}  # Authoritative position from last snapshot

        # File handles
        self.position_log = None
        self.pos_writer = None
        self.metric_file = None
        self.metric_writer = None
        self.event_pulse_time = 0.0

    def _get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[CLIENT]"):
        timestamp = self._get_timestamp()
        pid_str = f"{self.player_id:03d}" if self.player_id else "???"
        full_msg = f"{timestamp} - [CLIENT {pid_str}] {msg}"
        print(full_msg)
        with self.log_lock:
            self.log_file.write(full_msg + "\n")
            self.log_file.flush()

    def send_message(self, msg_type, snapshot_id, seq_num, payload=b'', reliable=False):
        try:
            if reliable: self.seq_manager.packet_sent(seq_num)

            # Do not compress for SNAPSHOT messages, as server handles it using COMPRESSED type
            # And do not compress for very small messages
            if len(payload) > 100 and msg_type not in [MSG_TYPES['HEARTBEAT'], MSG_TYPES['CONNECT'], MSG_TYPES['MOVE'],
                                                       MSG_TYPES['CLAIM']]:
                compressed = zlib.compress(payload, level=1)
                if len(compressed) < len(payload):
                    payload = compressed
                    msg_type = MSG_TYPES['COMPRESSED']  # Re-use COMPRESSED for other large payloads

            timestamp = int(time.time() * 1000)
            payload_len = len(payload)
            checksum = compute_checksum(payload)

            header = create_header(msg_type, snapshot_id, seq_num, payload_len, checksum)
            data = header + payload
            self.sock.sendto(data, self.server_addr)
        except Exception as e:
            self.log(f"Send error: {e}")

    def predict_move(self, direction):
        if not self.player_id: return

        if self.predicted_state is None:
            self.predicted_state = self.game.get_state()

        game_state = self.game.get_state()
        if self.player_id in game_state['players']:
            row, col = game_state['players'][self.player_id]
            new_row, new_col = row, col
            if direction == 0 and row > 0:
                new_row = row - 1
            elif direction == 1 and row < self.game.grid_size - 1:
                new_row = row + 1
            elif direction == 2 and col > 0:
                new_col = col - 1
            elif direction == 3 and col < self.game.grid_size - 1:
                new_col = col + 1

            if self.predicted_state:
                self.predicted_state['players'][self.player_id] = (new_row, new_col)

    def _apply_full_snapshot(self, payload: bytes):
        """Manually update game state from a full snapshot payload (compressed zlib data)"""
        try:
            data = zlib.decompress(payload)
        except zlib.error:
            self.log("Failed to decompress full snapshot data.", prefix="[ERROR]")
            return False

        idx = 0
        grid_size = self.game.grid_size

        # 1. Full grid (grid_size * grid_size bytes)
        expected_grid_len = grid_size * grid_size
        if idx + expected_grid_len > len(data): return False

        for i in range(grid_size):
            for j in range(grid_size):
                self.game.grid[i][j] = data[idx]
                idx += 1

        # 2. Player positions
        if idx + 1 > len(data): return False
        num_players = data[idx]
        idx += 1

        self.game.players.clear()
        self.server_position.clear()
        for _ in range(num_players):
            if idx + 3 > len(data): break
            pid, row, col = struct.unpack('BBB', data[idx:idx + 3])
            if 1 <= pid <= 4 and 0 <= row < grid_size and 0 <= col < grid_size:
                self.game.players[pid] = (row, col)
                self.server_position[pid] = (row, col)
            idx += 3

        # 3. Scores
        if idx + 4 > len(data): return False
        scores = struct.unpack('BBBB', data[idx:idx + 4])
        self.game.scores[1], self.game.scores[2], self.game.scores[3], self.game.scores[4] = scores
        idx += 4

        # 4. Events
        self.game.events.clear()
        if idx + 1 > len(data): return False
        num_events = data[idx]
        idx += 1
        for _ in range(num_events):
            if idx + 4 > len(data): break
            eid, etype, r, c = struct.unpack('BBBB', data[idx:idx + 4])
            # Recreate GameEvent object (or sufficient data)
            self.game.events[eid] = self.game.GameEvent(eid, etype, r, c, time.time())
            idx += 4

        # 5. Player Events
        self.game.player_events.clear()
        if idx + 1 > len(data): return False
        num_pevents = data[idx]
        idx += 1
        for _ in range(num_pevents):
            if idx + 6 > len(data): break
            pid, etype = struct.unpack('BB', data[idx:idx + 2])
            remaining_ms = struct.unpack('>I', data[idx + 2:idx + 6])[0]

            # Recreate PlayerEvent object
            self.game.player_events[pid] = self.game.PlayerEvent(etype, time.time() + remaining_ms / 1000.0)
            idx += 6

        self.game.last_grid_state = [row[:] for row in self.game.grid]
        self.game.last_player_positions = self.game.players.copy()

        self.log(f"Applied full snapshot from ID {self.snapshot_id}.", prefix="[SYNC]")
        return True

    def _update_server_positions(self):
        """Update authoritative player positions after delta application"""
        # This is a safe way to get the latest authoritative position
        for pid, pos in self.game.players.items():
            self.server_position[pid] = pos

    def _calculate_jitter(self, recv_time_ms):
        """Calculates jitter based on the mean deviation of inter-arrival times (in ms)"""
        if self.last_receive_time_ms == 0:
            return 0.0

        inter_arrival = recv_time_ms - self.last_receive_time_ms
        self.inter_arrival_times.append(inter_arrival)

        if len(self.inter_arrival_times) < 2:
            return 0.0

        # Jitter is the mean deviation of inter-arrival times
        mean_ia = sum(self.inter_arrival_times) / len(self.inter_arrival_times)
        jitter = sum(abs(ia - mean_ia) for ia in self.inter_arrival_times) / len(self.inter_arrival_times)
        return jitter

    def _log_snapshot_metrics(self, snapshot_id, seq_num, server_timestamp_ms, recv_time_ms, latency_ms):
        """Log all required metrics upon receiving a snapshot"""
        if not self.metric_writer or not self.player_id: return

        # Calculate Jitter
        jitter_ms = self._calculate_jitter(recv_time_ms)

        # Calculate Perceived Position Error (Euclidean distance)
        perceived_error = 0.0
        server_pos = self.server_position.get(self.player_id)
        display_pos = self.display_positions.get(self.player_id)

        if server_pos and display_pos:
            # Note: server_pos is integer (row, col) but display_pos is interpolated float (row, col)
            perceived_error = GridClashGame.calculate_position_error(server_pos, display_pos)

        # Placeholder values for server-side metrics
        cpu_percent = 0.0
        bandwidth_per_client_kbps = 0.0

        self.metric_writer.writerow([
            f"{time.time():.6f}",
            self.player_id,
            snapshot_id,
            seq_num,
            server_timestamp_ms,
            recv_time_ms,
            latency_ms,
            f"{jitter_ms:.2f}",
            f"{perceived_error:.4f}",
            f"{cpu_percent:.1f}",
            f"{bandwidth_per_client_kbps:.2f}"
        ])
        self.metric_file.flush()

    def handle_packet(self, data, addr):
        if len(data) < HEADER_SIZE: return
        try:
            recv_time_ms = int(time.time() * 1000)  # Client receive time
            header = parse_header(data[:HEADER_SIZE])
            if not header: return
            _, _, msg_type, snapshot_id, seq_num, server_timestamp_ms, payload_len, checksum = header
            payload = data[HEADER_SIZE:]
            if len(payload) != payload_len or compute_checksum(payload) != checksum: return

            if msg_type == MSG_TYPES['COMPRESSED']:
                try:
                    payload = zlib.decompress(payload)
                    msg_type = MSG_TYPES['SNAPSHOT']  # Assume compressed data is a delta snapshot
                except:
                    return

            latency_ms = recv_time_ms - server_timestamp_ms
            self.avg_latency = self.avg_latency * 0.9 + latency_ms * 0.1

            missing = self.seq_manager.packet_received(seq_num)
            self.packet_loss = min(1.0, self.packet_loss * 0.9 + 0.1) if missing else self.packet_loss * 0.99

            if msg_type == MSG_TYPES['WELCOME']:
                if not self.connected and len(payload) == 2:
                    self.player_id, grid_size = struct.unpack('BB', payload)
                    self.game.grid_size = grid_size
                    self.game.grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]  # Initialize grid
                    self.connected = True
                    self.log(f"Connected! Assigned Player ID: {self.player_id}")

                    # Setup position log
                    self.position_log = open(f'client_{self.player_id}_position_log.csv', 'w', newline='')
                    self.pos_writer = csv.writer(self.position_log)
                    self.pos_writer.writerow(
                        ['time', 'snapshot_id', 'player_id', 'server_row', 'server_col', 'display_row', 'display_col'])

                    # Setup metrics log (UPDATED HEADER)
                    self.metric_file = open(f'client_{self.player_id}_metrics.csv', 'w', newline='')
                    self.metric_writer = csv.writer(self.metric_file)
                    self.metric_writer.writerow(
                        ['timestamp', 'client_id', 'snapshot_id', 'seq_num', 'server_timestamp_ms', 'recv_time_ms',
                         'latency_ms', 'jitter_ms', 'perceived_position_error', 'cpu_percent',
                         'bandwidth_per_client_kbps'])

            elif msg_type == MSG_TYPES['FULL_SNAPSHOT']:
                if snapshot_id > self.snapshot_id and self._apply_full_snapshot(payload):
                    self.snapshot_id = snapshot_id
                    self.reconcile_state()
                    self.last_receive_time_ms = recv_time_ms
                    self.send_message(MSG_TYPES['ACK_SNAPSHOT'], snapshot_id, seq_num)
                    self._log_snapshot_metrics(snapshot_id, seq_num, server_timestamp_ms, recv_time_ms, latency_ms)

            elif msg_type == MSG_TYPES['SNAPSHOT']:
                self.send_message(MSG_TYPES['ACK_SNAPSHOT'], snapshot_id, seq_num)
                if snapshot_id > self.snapshot_id:
                    # NOTE: update_from_delta relies on the last state being correct.
                    # If this is the first snapshot after FULL_SNAPSHOT, it should work.
                    if self.game.update_from_delta(payload):
                        self.snapshot_id = snapshot_id
                        self._update_server_positions()  # Get authoritative positions
                        self.reconcile_state()
                        self.last_receive_time_ms = recv_time_ms
                        self._log_snapshot_metrics(snapshot_id, seq_num, server_timestamp_ms, recv_time_ms, latency_ms)


            elif msg_type == MSG_TYPES['EVENT_SPAWN']:
                if len(payload) >= 4:
                    eid, etype, r, c = struct.unpack('BBBB', payload[:4])
                    # Update client-side event tracking
                    self.events[eid] = {'type': etype, 'row': r, 'col': c, 'collected': False}

            elif msg_type == MSG_TYPES['EVENT_COLLECT']:
                if len(payload) >= 4:
                    eid, _, pid, _ = struct.unpack('BBBB', payload[:4])
                    if eid in self.events: self.events[eid]['collected'] = True
                    if pid == self.player_id: print("★ STAR COLLECTED! ★")

            elif msg_type == MSG_TYPES['ACK']:
                self.pending_claim = False
            elif msg_type == MSG_TYPES['NACK']:
                self.pending_claim = False
            elif msg_type == MSG_TYPES['GAME_OVER']:
                self.running = False

        except Exception as e:
            self.log(f"Packet error: {e}")

    def reconcile_state(self):
        if not self.predicted_state or not self.player_id: return
        server_state = self.game.get_state()

        # Smooth interpolation target update
        for pid, pos in server_state['players'].items():
            if pid not in self.display_positions:
                self.display_positions[pid] = (float(pos[0]), float(pos[1]))

        # Check prediction error
        server_pos = server_state['players'].get(self.player_id)
        pred_pos = self.predicted_state['players'].get(self.player_id)
        if server_pos and pred_pos and server_pos != pred_pos:
            # We must also update the predicted state's grid to match the server's, which
            # is done by copying the entire server state
            self.predicted_state = server_state.copy()  # Snap back

    def connect_to_server(self):
        """Robust Connection Loop: Keep trying until success"""
        self.log(f"Connecting to {self.server_addr}...")
        self.connection_start = time.time()

        # Try for 10 seconds
        while not self.connected and time.time() - self.connection_start < 10.0:
            # Send CONNECT
            self.send_message(MSG_TYPES['CONNECT'], 0, 0, b'', reliable=True)

            # Listen briefly for response
            start_wait = time.time()
            while time.time() - start_wait < 0.5:  # 500ms timeout
                try:
                    data, addr = self.sock.recvfrom(4096)
                    if addr == self.server_addr:
                        self.handle_packet(data, addr)
                        if self.connected:
                            # Wait a bit longer for the FULL_SNAPSHOT packet to arrive after WELCOME
                            # This is important for late-joiners.
                            time.sleep(0.1)
                            return True
                except socket.timeout:
                    pass
                except Exception:
                    pass

            self.log("Retrying connection...")

        return self.connected

    def get_fps(self):
        if not self.frame_times: return 0.0
        return len(self.frame_times) / sum(self.frame_times)

    def render_debug_info(self, screen, font):
        lines = [
            f"Player: {self.player_id}",
            f"FPS: {self.get_fps():.1f}",
            f"Latency: {self.avg_latency:.0f}ms",
            f"Loss: {self.packet_loss * 100:.1f}%",
        ]
        y = 5
        for line in lines:
            screen.blit(font.render(line, True, (200, 200, 255)), (5, y))
            y += 20

    def receive_thread(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                if addr == self.server_addr: self.handle_packet(data, addr)
            except:
                pass

    def auto_input_thread(self):
        while self.running and self.connected:
            time.sleep(random.uniform(0.3, 0.8))
            if random.random() < 0.7:
                self.seq_num += 1
                d = random.choice([0, 1, 2, 3])
                self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', d))
            else:
                self.seq_num += 1
                self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num, b'')

    def run(self):
        if not self.connect_to_server():
            self.log("Failed to connect after 10s. Exiting.")
            return

        threading.Thread(target=self.receive_thread, daemon=True).start()
        if self.auto: threading.Thread(target=self.auto_input_thread, daemon=True).start()

        pygame.init()
        screen = pygame.display.set_mode((self.game.grid_size * CELL_SIZE, self.game.grid_size * CELL_SIZE))
        pygame.display.set_caption(f"Grid Clash - P{self.player_id}")
        clock = pygame.time.Clock()
        font = pygame.font.Font(None, 24)
        debug_font = pygame.font.Font(None, 16)

        while self.running:
            start = time.time()

            # Heartbeat
            if time.time() - self.last_heartbeat_time > 2.0:
                self.send_message(MSG_TYPES['HEARTBEAT'], 0, 0)
                self.last_heartbeat_time = time.time()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE: self.running = False

                    move = None
                    if event.key == pygame.K_UP:
                        move = 0
                    elif event.key == pygame.K_DOWN:
                        move = 1
                    elif event.key == pygame.K_LEFT:
                        move = 2
                    elif event.key == pygame.K_RIGHT:
                        move = 3

                    if move is not None:
                        self.seq_num += 1
                        self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', move), True)
                        self.predict_move(move)
                    elif event.key == pygame.K_SPACE and not self.pending_claim:
                        self.seq_num += 1
                        self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num, b'', True)
                        self.pending_claim = True

            # Interpolation
            gs = self.game.get_state()
            if self.predicted_state and self.player_id:
                tpos = self.predicted_state['players'].get(self.player_id)
                if tpos:
                    curr = self.display_positions.get(self.player_id, tpos)
                    self.display_positions[self.player_id] = GridClashGame.interpolate_position(curr, tpos, 12.0,
                                                                                                clock.get_time() / 1000)

            for pid, pos in gs['players'].items():
                if pid != self.player_id:
                    curr = self.display_positions.get(pid, pos)
                    self.display_positions[pid] = GridClashGame.interpolate_position(curr, pos, 8.0,
                                                                                     clock.get_time() / 1000)
            # Log Position Data (Continuous/Per-Frame)
            if self.pos_writer and self.player_id and self.snapshot_id > 0:
                server_pos = self.server_position.get(self.player_id, (0, 0))
                display_pos = self.display_positions.get(self.player_id, server_pos)
                self.pos_writer.writerow([
                    f"{time.time():.6f}",
                    self.snapshot_id,
                    self.player_id,
                    server_pos[0],
                    server_pos[1],
                    display_pos[0],
                    display_pos[1]
                ])
                self.position_log.flush()

            # Draw
            screen.fill((20, 20, 30))
            for i in range(self.game.grid_size):
                for j in range(self.game.grid_size):
                    val = gs['grid'][i][j]
                    color = GridClashGame.get_color(val)
                    pygame.draw.rect(screen, color, (j * CELL_SIZE, i * CELL_SIZE, CELL_SIZE, CELL_SIZE))

            # Grid lines
            for i in range(self.game.grid_size + 1):
                pygame.draw.line(screen, (40, 40, 50), (0, i * CELL_SIZE),
                                 (self.game.grid_size * CELL_SIZE, i * CELL_SIZE))
                pygame.draw.line(screen, (40, 40, 50), (i * CELL_SIZE, 0),
                                 (i * CELL_SIZE, self.game.grid_size * CELL_SIZE))

            # Events
            pulse = (math.sin(self.event_pulse_time * 5) + 1) * 0.5
            for eid, edata in self.events.items():
                if not edata['collected']:
                    cx, cy = edata['col'] * CELL_SIZE + CELL_SIZE / 2, edata['row'] * CELL_SIZE + CELL_SIZE / 2
                    pygame.draw.circle(screen, (255, 215, 0), (int(cx), int(cy)), int(CELL_SIZE / 3))

            # Players
            for pid, (r, c) in self.display_positions.items():
                color = GridClashGame.get_color(pid)
                cx, cy = c * CELL_SIZE + CELL_SIZE / 2, r * CELL_SIZE + CELL_SIZE / 2
                pygame.draw.circle(screen, color, (int(cx), int(cy)), int(CELL_SIZE / 3))
                if GridClashGame.should_draw_outline(int(r), int(c), gs['grid'], pid):
                    pygame.draw.circle(screen, (200, 200, 200), (int(cx), int(cy)), int(CELL_SIZE / 3) + 2, 2)

            score = gs['scores'].get(self.player_id, 0)
            screen.blit(font.render(f"P{self.player_id} | Score: {score}", True, (255, 255, 255)),
                        (10, self.game.grid_size * CELL_SIZE - 30))
            self.render_debug_info(screen, debug_font)

            pygame.display.flip()
            dt = time.time() - start
            self.frame_times.append(dt)
            self.event_pulse_time += dt
            clock.tick(60)

        pygame.quit()
        if self.log_file: self.log_file.close()
        if self.metric_file: self.metric_file.close()
        if self.position_log: self.position_log.close()
        self.sock.close()


if __name__ == '__main__':
    Client(auto=(len(sys.argv) > 1 and sys.argv[1] == 'auto')).run()