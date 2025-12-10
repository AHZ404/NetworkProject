# client.py
import math
import socket
import time
import struct
import threading
import sys
import random
import pygame
import zlib
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
        self.sock.settimeout(0.01)  # Non-blocking with timeout

        self.server_addr = (host, port)

        # Game instance
        self.game = GridClashGame()
        self.player_id = None
        self.display_positions = {}

        # Client state
        self.client_name = client_name or f"Client_{random.randint(100, 999)}"
        self.snapshot_id = 0
        self.pending_claim = False
        self.claim_timer = 0.0
        self.claim_timeout = 0.5  # Increased timeout
        self.running = True
        self.auto = auto
        self.seq_num = 0
        self.local_seq = 0

        # Sequence and reliability
        self.seq_manager = SequenceManager()
        self.pending_snapshots = {}
        self.last_server_seq = 0

        # Event system
        self.events = {}
        self.player_events = {}
        self.event_pulse_time = 0.0

        # Prediction and reconciliation
        self.input_buffer = deque(maxlen=60)  # Store last 1 second of inputs at 60Hz
        self.predicted_state = None
        self.prediction_seq = 0
        self.reconciliation_buffer = deque(maxlen=30)

        # Connection monitoring
        self.connected = False
        self.connection_start = 0
        self.connection_timeout = 10.0
        self.avg_latency = 0
        self.packet_loss = 0
        self.last_heartbeat_time = 0
        self.heartbeat_interval = 2.0

        # Logging
        self.log_file = open('client_log.txt', 'w')
        self.position_log = None
        self.metric_file = None
        self.last_recv_time = 0
        self.last_snapshot_time = 0

        # Performance tracking
        self.frame_times = deque(maxlen=60)
        self.update_times = deque(maxlen=60)
        self.render_times = deque(maxlen=60)

    def _get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[CLIENT]"):
        timestamp = self._get_timestamp()
        if self.player_id:
            log_prefix = f"[CLIENT {self.player_id:03d}]"
        else:
            log_prefix = f"[CLIENT {self.client_name}]"

        full_msg = f"{timestamp} - {log_prefix} {msg}"
        print(full_msg)
        self.log_file.write(full_msg + "\n")
        self.log_file.flush()


    def send_message(self, msg_type, snapshot_id, seq_num, payload=b'', reliable=False):
        """Send message with optional reliability"""
        try:
            # DEBUG: Always print what we're sending
            msg_names = {v: k for k, v in MSG_TYPES.items()}
            msg_name = msg_names.get(msg_type, f"UNKNOWN({msg_type})")
            self.log(f"DEBUG: Preparing to send {msg_name}")

            if reliable:
                self.seq_manager.packet_sent(seq_num)

            # Don't compress CONNECT/WELCOME messages
            if len(payload) > 100 and msg_type not in [MSG_TYPES['HEARTBEAT'], MSG_TYPES['CONNECT'],
                                                       MSG_TYPES['WELCOME']]:
                compressed = zlib.compress(payload, level=1)
                if len(compressed) < len(payload):
                    payload = compressed
                    msg_type = MSG_TYPES['COMPRESSED']

            timestamp = int(time.time() * 1000)
            payload_len = len(payload)
            checksum = compute_checksum(payload)

            # DEBUG: Print checksum
            self.log(f"DEBUG: Checksum for payload ({payload_len} bytes): {checksum}")

            header = create_header(msg_type, snapshot_id, seq_num, payload_len, checksum)
            data = header + payload

            self.log(f"DEBUG: Sending {len(data)} bytes total (header: {len(header)}, payload: {payload_len})")

            # DEBUG: Print first few bytes
            if len(data) > 0:
                self.log(f"DEBUG: First 4 bytes: {data[:4].hex() if len(data) >= 4 else 'too short'}")

            bytes_sent = self.sock.sendto(data, self.server_addr)
            self.log(f"DEBUG: Sent {bytes_sent} bytes to {self.server_addr}")

            # Log important messages
            if msg_type == MSG_TYPES['CONNECT']:
                self.log(f"Sent CONNECT seq={self.local_seq}")
                self.local_seq += 1

        except Exception as e:
            self.log(f"Send error: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")

    def predict_move(self, direction):
        """Predict move locally for immediate feedback"""
        if not self.player_id:
            return

        # Store input for reconciliation
        input_record = {
            'seq': self.seq_num,
            'direction': direction,
            'timestamp': time.time(),
            'position_before': self.game.players.get(self.player_id, (0, 0))
        }

        self.input_buffer.append(input_record)

        # Apply prediction
        if self.predicted_state is None:
            self.predicted_state = self.game.get_state()

        # Simple prediction: move in direction
        game_state = self.game.get_state()
        if self.player_id in game_state['players']:
            row, col = game_state['players'][self.player_id]

            # Calculate new position
            new_row, new_col = row, col
            if direction == 0 and row > 0:
                new_row = row - 1
            elif direction == 1 and row < self.game.grid_size - 1:
                new_row = row + 1
            elif direction == 2 and col > 0:
                new_col = col - 1
            elif direction == 3 and col < self.game.grid_size - 1:
                new_col = col + 1

            # Update predicted state
            if self.predicted_state:
                self.predicted_state['players'][self.player_id] = (new_row, new_col)

    def reconcile_state(self, server_state):
        """Reconcile predicted state with server state"""
        if not self.predicted_state or not self.player_id:
            return

        # Check if prediction matches server
        server_pos = server_state['players'].get(self.player_id)
        predicted_pos = self.predicted_state['players'].get(self.player_id)

        if server_pos and predicted_pos and server_pos != predicted_pos:
            # Prediction was wrong, revert and replay inputs
            self.log(f"Reconciliation needed: server={server_pos}, predicted={predicted_pos}")

            # Revert to server state
            self.predicted_state = server_state.copy()

            # Replay inputs that happened after this snapshot
            for input_record in list(self.input_buffer):
                # Only replay if input was after the server state
                # (simplified - in real implementation, track timestamps)
                pass

    def handle_packet(self, data, addr):
        """Handle incoming packet"""
        if len(data) < HEADER_SIZE:
            return

        try:
            header = parse_header(data[:HEADER_SIZE])
            if not header:
                return

            protocol_id, version, msg_type, snapshot_id, seq_num, timestamp, payload_len, checksum = header

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
            recv_time = int(time.time() * 1000)
            latency = recv_time - timestamp

            # Update latency tracking
            self.avg_latency = self.avg_latency * 0.9 + latency * 0.1

            # Check for packet loss
            missing = self.seq_manager.packet_received(seq_num)
            if missing:
                self.packet_loss = min(1.0, self.packet_loss * 0.9 + 0.1)
                # Request missing packets
                for missing_seq in missing[:3]:  # Limit to 3 requests
                    self.send_message(MSG_TYPES['RESEND_REQUEST'], 0, 0,
                                      struct.pack('I', missing_seq))
            else:
                self.packet_loss = self.packet_loss * 0.99

            # Handle message type
            if msg_type == MSG_TYPES['WELCOME'] and len(payload) == 2:
                self.player_id, grid_size = struct.unpack('BB', payload)
                self.connected = True
                self.connection_start = time.time()

                self.log(f"Connected as Player {self.player_id}")

                # Open log files
                self.position_log = open(f'client_{self.player_id}_position_log.csv', 'w')
                self.position_log.write('time,snapshot_id,row,col,latency\n')
                self.metric_file = open(f'client_{self.player_id}_metrics.csv', 'w')
                self.metric_file.write('timestamp,snapshot_id,latency,packet_loss,fps\n')

                # Send heartbeat immediately
                self.send_heartbeat()

            elif msg_type == MSG_TYPES['SNAPSHOT']:
                # Acknowledge receipt
                self.send_message(MSG_TYPES['ACK_SNAPSHOT'], snapshot_id, seq_num)

                # Process snapshot
                if snapshot_id > self.snapshot_id:
                    self.process_snapshot(snapshot_id, seq_num, payload, latency)
                    self.last_snapshot_time = time.time()

            elif msg_type == MSG_TYPES['EVENT_SPAWN']:
                if len(payload) >= 4:
                    event_id, event_type, row, col = struct.unpack('BBBB', payload[:4])
                    self.events[event_id] = {
                        'type': event_type,
                        'row': row,
                        'col': col,
                        'collected': False
                    }

            elif msg_type == MSG_TYPES['EVENT_COLLECT']:
                if len(payload) >= 4:
                    event_id, event_type, player_id, _ = struct.unpack('BBBB', payload[:4])
                    if event_id in self.events:
                        self.events[event_id]['collected'] = True
                        self.events[event_id]['collected_time'] = time.time()

                    if player_id == self.player_id:
                        print(f"\n{'★' * 20}")
                        print("YOU COLLECTED A STAR!")
                        print("You can steal enemy blocks for 3 seconds!")
                        print(f"{'★' * 20}\n")

            elif msg_type == MSG_TYPES['ACK']:
                self.pending_claim = False
                if len(payload) >= 2:
                    row, col = struct.unpack('BB', payload[:2])

            elif msg_type == MSG_TYPES['NACK']:
                self.pending_claim = False
                self.log("Claim failed - cell already owned")

            elif msg_type == MSG_TYPES['GAME_OVER']:
                self.handle_game_over(payload)

        except Exception as e:
            self.log(f"Packet handling error: {e}")

    def process_snapshot(self, snapshot_id, seq_num, payload, latency):
        """Process incoming snapshot"""
        # Update from delta
        success = self.game.update_from_delta(payload)

        if success:
            self.snapshot_id = snapshot_id
            self.last_server_seq = seq_num

            # Reconcile with predicted state
            server_state = self.game.get_state()
            self.reconcile_state(server_state)

            # Update display positions
            for pid, pos in server_state['players'].items():
                if pid not in self.display_positions:
                    self.display_positions[pid] = (float(pos[0]), float(pos[1]))

            # Log metrics
            if self.metric_file and self.player_id:
                current_time = time.time()
                self.metric_file.write(
                    f"{current_time},{snapshot_id},{latency},"
                    f"{self.packet_loss:.3f},{self.get_fps():.1f}\n")
                self.metric_file.flush()

            # Log position
            if self.position_log and self.player_id in self.display_positions:
                row, col = self.display_positions[self.player_id]
                self.position_log.write(
                    f"{time.time()},{snapshot_id},{row},{col},{latency}\n")
                self.position_log.flush()

    def handle_game_over(self, payload):
        """Handle game over message"""
        if len(payload) >= 6:
            winner = payload[0]
            win_reason_len = payload[1]
            win_reason = ""
            if win_reason_len > 0:
                win_reason = payload[2:2 + win_reason_len].decode('utf-8', errors='ignore')
                score_start = 2 + win_reason_len
                if len(payload) >= score_start + 4:
                    scores = struct.unpack('BBBB', payload[score_start:score_start + 4])

                    self.log(f"GAME OVER! Winner: Player {winner}, Reason: {win_reason}")
                    print(f"\n{'=' * 50}")
                    print(f"GAME OVER!")
                    print(f"Winner: Player {winner}")
                    print(f"Reason: {win_reason}")
                    print(f"Final Scores: P1={scores[0]}, P2={scores[1]}, P3={scores[2]}, P4={scores[3]}")
                    print(f"{'=' * 50}\n")

                    self.running = False

    def send_heartbeat(self):
        """Send heartbeat to server"""
        if self.connected:
            self.send_message(MSG_TYPES['HEARTBEAT'], 0, 0)
            self.last_heartbeat_time = time.time()

    def check_connection(self):
        """Check connection health"""
        current_time = time.time()

        # Send heartbeat periodically
        if self.connected and current_time - self.last_heartbeat_time > self.heartbeat_interval:
            self.send_heartbeat()

        # Check for timeout
        if not self.connected and current_time - self.connection_start > self.connection_timeout:
            self.log("Connection timeout")
            self.running = False

        # Check for server timeout
        if self.connected and current_time - self.last_snapshot_time > 5.0:
            self.log("Server seems unresponsive")
            # Try reconnecting
            if current_time - self.last_snapshot_time > 10.0:
                self.log("Server timeout - disconnecting")
                self.running = False

    def receive_thread(self):
        """Thread for receiving packets"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                if addr == self.server_addr:
                    self.handle_packet(data, addr)
                    self.last_recv_time = time.time()
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:  # Only log if still running
                    self.log(f"Receive error: {e}")

    def auto_input_thread(self):
        """Auto bot thread"""
        while self.running and self.connected:
            time.sleep(random.uniform(0.2, 0.8))  # Slower auto inputs

            self.seq_num += 1

            if random.random() < 0.7:  # 70% move, 30% claim
                direction = random.choice([0, 1, 2, 3])
                self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num,
                                  struct.pack('B', direction), reliable=True)
            else:
                self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num, b'', reliable=True)
                self.pending_claim = True
                self.claim_timer = self.claim_timeout

    def get_fps(self):
        """Calculate current FPS"""
        if not self.frame_times:
            return 0.0
        return len(self.frame_times) / sum(self.frame_times)

    def render_debug_info(self, screen, font):
        """Render debug information"""
        debug_lines = [
            f"Player: {self.player_id or 'Connecting...'}",
            f"FPS: {self.get_fps():.1f}",
            f"Latency: {self.avg_latency:.0f}ms",
            f"Packet Loss: {self.packet_loss * 100:.1f}%",
            f"Snapshot: {self.snapshot_id}",
            f"Events: {len([e for e in self.events.values() if not e['collected']])}",
        ]

        y_offset = 5
        for line in debug_lines:
            text = font.render(line, True, (200, 200, 255))
            screen.blit(text, (5, y_offset))
            y_offset += 20

    def run(self):
        """Main client loop"""
        self.log(f"Starting client '{self.client_name}'")
        self.log(f"Connecting to {self.server_addr[0]}:{self.server_addr[1]}")

        # Connect to server
        self.connection_start = time.time()
        self.send_message(MSG_TYPES['CONNECT'], 0, 0, b'', reliable=True)

        # Start threads
        threading.Thread(target=self.receive_thread, daemon=True).start()
        if self.auto:
            threading.Thread(target=self.auto_input_thread, daemon=True).start()

        # PyGame setup
        pygame.init()
        grid_size = self.game.grid_size
        screen = pygame.display.set_mode((grid_size * CELL_SIZE, grid_size * CELL_SIZE))
        pygame.display.set_caption(f"Grid Clash - {self.client_name}")
        clock = pygame.time.Clock()

        # Fonts
        font = pygame.font.Font(None, 24)
        small_font = pygame.font.Font(None, 18)
        title_font = pygame.font.Font(None, 32)
        debug_font = pygame.font.Font(None, 16)

        # Smoothing
        smoothing_speed = 8.0

        # Main loop
        while self.running:
            frame_start = time.time()

            # Check connection
            self.check_connection()

            # Handle claim timeout
            if self.pending_claim:
                self.claim_timer -= clock.get_time() / 1000.0
                if self.claim_timer <= 0:
                    self.pending_claim = False
                    self.log("Claim timeout")

            # Handle events
            current_time = time.time()

            # Clean expired player events
            expired_players = []
            for pid, event_data in list(self.player_events.items()):
                if current_time > event_data.get('expiration_time', 0):
                    expired_players.append(pid)

            for pid in expired_players:
                if pid in self.player_events:
                    del self.player_events[pid]

            # Clean collected events after delay
            events_to_remove = []
            for event_id, event_data in list(self.events.items()):
                if (event_data.get('collected') and
                        current_time - event_data.get('collected_time', 0) > 1.0):
                    events_to_remove.append(event_id)

            for event_id in events_to_remove:
                del self.events[event_id]

            # Process pygame events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    self.log("User quit")
                elif event.type == pygame.KEYDOWN and self.player_id:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                        self.log("User quit (ESC)")
                    elif not self.pending_claim:
                        if event.key == pygame.K_UP:
                            self.seq_num += 1
                            self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num,
                                              struct.pack('B', 0), reliable=True)
                            self.predict_move(0)
                        elif event.key == pygame.K_DOWN:
                            self.seq_num += 1
                            self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num,
                                              struct.pack('B', 1), reliable=True)
                            self.predict_move(1)
                        elif event.key == pygame.K_LEFT:
                            self.seq_num += 1
                            self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num,
                                              struct.pack('B', 2), reliable=True)
                            self.predict_move(2)
                        elif event.key == pygame.K_RIGHT:
                            self.seq_num += 1
                            self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num,
                                              struct.pack('B', 3), reliable=True)
                            self.predict_move(3)
                        elif event.key == pygame.K_SPACE:
                            self.seq_num += 1
                            self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num,
                                              b'', reliable=True)
                            self.pending_claim = True
                            self.claim_timer = self.claim_timeout

            # Smooth positions
            game_state = self.game.get_state()
            for pid, target_pos in game_state['players'].items():
                if pid in self.display_positions:
                    current_pos = self.display_positions[pid]
                    new_pos = GridClashGame.interpolate_position(
                        current_pos, target_pos, smoothing_speed, clock.get_time() / 1000.0)
                    self.display_positions[pid] = new_pos
                else:
                    self.display_positions[pid] = (float(target_pos[0]), float(target_pos[1]))

            # Render
            render_start = time.time()
            screen.fill((20, 20, 30))  # Dark blue background

            # Draw grid cells
            for i in range(grid_size):
                for j in range(grid_size):
                    cell_value = game_state['grid'][i][j]
                    color = GridClashGame.get_color(cell_value)
                    pygame.draw.rect(screen, color,
                                     (j * CELL_SIZE, i * CELL_SIZE, CELL_SIZE, CELL_SIZE))

            # Draw grid lines
            for i in range(grid_size + 1):
                pygame.draw.line(screen, (40, 40, 50),
                                 (0, i * CELL_SIZE),
                                 (grid_size * CELL_SIZE, i * CELL_SIZE), 1)
                pygame.draw.line(screen, (40, 40, 50),
                                 (i * CELL_SIZE, 0),
                                 (i * CELL_SIZE, grid_size * CELL_SIZE), 1)

            # Draw events
            pulse = (math.sin(self.event_pulse_time * 5) + 1) * 0.5
            for event_id, event_data in self.events.items():
                if not event_data['collected']:
                    row, col = event_data['row'], event_data['col']

                    # Star drawing
                    center_x = col * CELL_SIZE + CELL_SIZE / 2
                    center_y = row * CELL_SIZE + CELL_SIZE / 2
                    radius = int(CELL_SIZE / 4)

                    # Pulsing color
                    base_color = (255, 215, 0)
                    pulse_color = (255, 255, 200)
                    color = (
                        int(base_color[0] * (1 - pulse) + pulse_color[0] * pulse),
                        int(base_color[1] * (1 - pulse) + pulse_color[1] * pulse),
                        int(base_color[2] * (1 - pulse) + pulse_color[2] * pulse)
                    )

                    # Draw star
                    points = []
                    for i in range(5):
                        angle = math.pi / 2 + i * 4 * math.pi / 5
                        outer_x = center_x + radius * math.cos(angle)
                        outer_y = center_y + radius * math.sin(angle)
                        inner_x = center_x + (radius / 2) * math.cos(angle + 2 * math.pi / 10)
                        inner_y = center_y + (radius / 2) * math.sin(angle + 2 * math.pi / 10)
                        points.extend([(outer_x, outer_y), (inner_x, inner_y)])
                    pygame.draw.polygon(screen, color, points)

            # Draw players
            for pid, (row, col) in self.display_positions.items():
                color = GridClashGame.get_color(pid)
                center_x = col * CELL_SIZE + CELL_SIZE / 2
                center_y = row * CELL_SIZE + CELL_SIZE / 2
                radius = int(CELL_SIZE / 3)

                # Draw player
                pygame.draw.circle(screen, color, (int(center_x), int(center_y)), radius)

                # Draw outline if on claimed cell
                cell_row, cell_col = int(row), int(col)
                if GridClashGame.should_draw_outline(cell_row, cell_col, game_state['grid'], pid):
                    outline_color = GridClashGame.get_outline_color(pid)
                    pygame.draw.circle(screen, outline_color,
                                       (int(center_x), int(center_y)), radius + 3, 3)

                # Draw event indicator
                if pid in self.player_events:
                    star_y = center_y - radius - int(CELL_SIZE / 8) - 2
                    pygame.draw.circle(screen, (255, 215, 0),
                                       (int(center_x), int(star_y)), int(CELL_SIZE / 8))

            # Draw UI
            if self.player_id:
                # Player info
                player_color = GridClashGame.get_color(self.player_id)
                player_text = font.render(f"You: Player {self.player_id}", True, player_color)
                screen.blit(player_text, (5, grid_size * CELL_SIZE - 150))

                # Score
                your_score = game_state['scores'].get(self.player_id, 0)
                score_text = font.render(f"Score: {your_score}/200", True, (255, 255, 255))
                screen.blit(score_text, (5, grid_size * CELL_SIZE - 125))

                # Progress bar
                progress = min(your_score / 200, 1.0)
                pygame.draw.rect(screen, (60, 60, 70), (5, grid_size * CELL_SIZE - 100, 150, 12))
                pygame.draw.rect(screen, (0, 200, 0), (5, grid_size * CELL_SIZE - 100, int(150 * progress), 12))

                # Cell status
                cell_status = self.game.get_player_cell_status(self.player_id)
                if cell_status['is_claimed']:
                    if cell_status['is_own_cell']:
                        status_color = (0, 255, 0)
                        status_text = "Your claimed cell"
                    else:
                        status_color = (255, 100, 100)
                        status_text = f"Player {cell_status['owner']}'s cell"
                else:
                    status_color = (200, 200, 255)
                    status_text = "Unclaimed - Press SPACE!"

                status_surface = small_font.render(status_text, True, status_color)
                screen.blit(status_surface, (5, grid_size * CELL_SIZE - 80))

                # Active events
                if self.player_id in self.player_events:
                    remaining = max(0, self.player_events[self.player_id].get('expiration_time', 0) - current_time)
                    event_text = font.render(f"★ STAR: {remaining:.1f}s", True, (255, 215, 0))
                    screen.blit(event_text, (5, grid_size * CELL_SIZE - 55))

                # Controls reminder
                controls = small_font.render("Controls: Arrows=Move, SPACE=Claim, ESC=Quit", True, (180, 180, 180))
                screen.blit(controls, (5, grid_size * CELL_SIZE - 30))

                # Connection status
                if self.connected:
                    conn_text = small_font.render(f"Connected ({self.avg_latency:.0f}ms)", True, (0, 255, 0))
                else:
                    conn_text = small_font.render("Connecting...", True, (255, 255, 0))
                screen.blit(conn_text, (5, grid_size * CELL_SIZE - 10))

            # Draw debug info
            self.render_debug_info(screen, debug_font)

            # Game over overlay
            if game_state['game_over']:
                overlay = pygame.Surface((grid_size * CELL_SIZE, grid_size * CELL_SIZE), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 200))
                screen.blit(overlay, (0, 0))

                winner = game_state['winner']
                win_reason = game_state.get('win_reason', 'Game ended')
                winner_color = GridClashGame.get_color(winner)

                winner_text = title_font.render(f"GAME OVER! Winner: Player {winner}", True, winner_color)
                winner_rect = winner_text.get_rect(center=(grid_size * CELL_SIZE // 2, grid_size * CELL_SIZE // 2 - 30))
                screen.blit(winner_text, winner_rect)

                reason_text = font.render(win_reason, True, (255, 255, 255))
                reason_rect = reason_text.get_rect(center=(grid_size * CELL_SIZE // 2, grid_size * CELL_SIZE // 2))
                screen.blit(reason_text, reason_rect)

            pygame.display.flip()

            # Update timing
            frame_time = time.time() - frame_start
            self.frame_times.append(frame_time)
            self.event_pulse_time += frame_time

            # Cap at 60 FPS
            clock.tick(60)

        # Cleanup
        pygame.quit()
        self.log("Client shutting down")

        if self.log_file:
            self.log_file.close()
        if self.position_log:
            self.position_log.close()
        if self.metric_file:
            self.metric_file.close()

        self.sock.close()


if __name__ == '__main__':
    auto = len(sys.argv) > 1 and sys.argv[1] == 'auto'
    client_name = None
    if len(sys.argv) > 2:
        client_name = sys.argv[2]

    client = Client(auto=auto, client_name=client_name)
    client.run()