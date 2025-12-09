# client.py
import socket
import time
import struct
import threading
import sys
import random
import pygame
from datetime import datetime
from common import *
from game import GridClashGame  # Import consolidated game logic


class Client:
    def __init__(self, host=HOST, port=PORT, auto=False, client_name=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))
        self.server_addr = (host, port)

        # Game instance for local state
        self.game = GridClashGame()
        self.player_id = None
        self.display_positions = {}  # Smoothed positions for rendering
        self.client_name = client_name or f"Client_{random.randint(100, 999)}"

        # Client state
        self.snapshot_id = 0
        self.pending_claim = False
        self.claim_timer = 0.0
        self.claim_timeout = 0.2
        self.running = True
        self.auto = auto
        self.seq_num = 0
        self.local_seq = 0  # Local sequence for logging

        # Logging
        self.log_file = open('client_log.txt', 'w')
        self.position_log = None
        self.metric_file = None
        self.last_recv_time = 0
        self.connected = False

    def _get_timestamp(self):
        """Get current timestamp in the format: YYYY-MM-DD HH:MM:SS,SSS"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[CLIENT]"):
        """Enhanced logging with timestamp and client identifier"""
        timestamp = self._get_timestamp()
        if self.player_id:
            log_prefix = f"[CLIENT {self.player_id:03d}]"
        else:
            log_prefix = f"[CLIENT {self.client_name}]"

        full_msg = f"{timestamp} - {log_prefix} {msg}"
        print(full_msg)  # Also print to console
        self.log_file.write(full_msg + "\n")
        self.log_file.flush()

    def send_message(self, msg_type, snapshot_id, seq_num, payload=b''):
        timestamp = int(time.time() * 1000)
        payload_len = len(payload)
        checksum = compute_checksum(payload)
        header = struct.pack(HEADER_FMT, PROTO_ID, VERSION, msg_type,
                             snapshot_id, seq_num, timestamp, payload_len, checksum)
        data = header + payload
        self.sock.sendto(data, self.server_addr)

        # Enhanced logging for sent messages
        if msg_type == MSG_TYPES['CONNECT']:
            self.log(f"Sent CONNECT seq={self.local_seq}")
            self.local_seq += 1
        elif msg_type == MSG_TYPES['MOVE']:
            direction = struct.unpack('B', payload)[0] if payload else -1
            dir_names = {0: 'UP', 1: 'DOWN', 2: 'LEFT', 3: 'RIGHT'}
            direction_name = dir_names.get(direction, 'UNKNOWN')
            self.log(f"Sent MOVE seq={self.local_seq}: direction={direction_name} ({direction})")
            self.local_seq += 1
        elif msg_type == MSG_TYPES['CLAIM']:
            self.log(f"Sent CLAIM seq={self.local_seq}")
            self.local_seq += 1

    def receive_thread(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                if addr != self.server_addr or len(data) < HEADER_SIZE:
                    continue

                header = data[:HEADER_SIZE]
                protocol_id, version, msg_type, snapshot_id, seq_num, timestamp, payload_len, checksum = struct.unpack(
                    HEADER_FMT, header)

                if protocol_id != PROTO_ID or version != VERSION or payload_len != len(data) - HEADER_SIZE:
                    continue

                payload = data[HEADER_SIZE:]
                if compute_checksum(payload) != checksum:
                    continue

                # Process latency and jitter
                recv_time = int(time.time() * 1000)
                latency = recv_time - timestamp
                curr_time = time.time()
                jitter = 0
                if self.last_recv_time > 0 and msg_type == MSG_TYPES['SNAPSHOT']:
                    inter_arrival = curr_time - self.last_recv_time
                    jitter = abs(inter_arrival - UPDATE_INTERVAL)
                self.last_recv_time = curr_time

                # Handle different message types with enhanced logging
                if msg_type == MSG_TYPES['WELCOME'] and len(payload) == 2:
                    self.player_id, grid_size = struct.unpack('BB', payload)
                    self.connected = True
                    self.log(f"Received WELCOME seq={seq_num}: player_id={self.player_id}, grid_size={grid_size}")

                    # Game already initialized with correct grid size
                    self.position_log = open(f'client_{self.player_id}_position_log.csv', 'w')
                    self.position_log.write('time,snapshot_id,player_id,row,col\n')
                    self.metric_file = open(f'client_{self.player_id}_metrics.csv', 'w')
                    self.metric_file.write('client_id,snapshot_id,seq_num,server_timestamp,recv_time,latency,jitter\n')

                elif msg_type == MSG_TYPES['SNAPSHOT']:
                    if snapshot_id <= self.snapshot_id:
                        continue  # Discard outdated
                    self.snapshot_id = snapshot_id

                    # Handle redundant payload (contains current + previous)
                    payload_size = len(payload)
                    grid_size = self.game.grid_size * self.game.grid_size

                    # Extract the most recent snapshot (last half of payload if redundant)
                    if payload_size >= grid_size * 2:
                        # Has redundancy, use the second (newer) snapshot
                        start_idx = payload_size // 2
                        grid_bytes = payload[start_idx:start_idx + grid_size]
                        pos_start = start_idx + grid_size
                    else:
                        # No redundancy
                        grid_bytes = payload[:grid_size]
                        pos_start = grid_size

                    # Update game state
                    positions_bytes = payload[pos_start:]
                    self.game.update_from_snapshot(grid_bytes, positions_bytes)

                    # Update display positions targets
                    game_state = self.game.get_state()
                    for pid, pos in game_state['players'].items():
                        if pid not in self.display_positions:
                            self.display_positions[pid] = (float(pos[0]), float(pos[1]))

                    # Log snapshot receipt
                    player_count = len(game_state['players'])
                    grid_filled = sum(cell != 0 for row in game_state['grid'] for cell in row)
                    total_cells = self.game.grid_size * self.game.grid_size
                    self.log(
                        f"<<< Received SNAPSHOT seq={seq_num}: players={player_count}, grid_filled={grid_filled}/{total_cells}")

                    # Log metrics
                    if self.metric_file:
                        self.metric_file.write(
                            f"{self.player_id},{snapshot_id},{seq_num},{timestamp},"
                            f"{recv_time},{latency},{jitter}\n")
                        self.metric_file.flush()

                elif msg_type == MSG_TYPES['ACK']:
                    if len(payload) >= 2:
                        row, col = struct.unpack('BB', payload[:2])
                        self.log(f"Received ACK seq={seq_num}: claim_success at ({row},{col})")
                    self.pending_claim = False

                elif msg_type == MSG_TYPES['NACK']:
                    if len(payload) >= 2:
                        row, col = struct.unpack('BB', payload[:2])
                        self.log(f"Received NACK seq={seq_num}: claim_failed at ({row},{col})")
                    self.pending_claim = False

                elif msg_type == MSG_TYPES['GAME_OVER']:
                    if len(payload) == 5:
                        winner, score1, score2, score3, score4 = struct.unpack('BBBBB', payload)
                        self.log(
                            f"Received GAME_OVER seq={seq_num}: winner=Player{winner}, scores=[P1={score1}, P2={score2}, P3={score3}, P4={score4}]")
                        print(f"\n{'=' * 50}")
                        print(f"GAME OVER! Winner: Player {winner}")
                        print(f"Final Scores: P1={score1}, P2={score2}, P3={score3}, P4={score4}")
                        print(f"{'=' * 50}\n")
                    self.running = False

            except Exception as e:
                self.log(f"Receive error: {e}")

    def auto_input_thread(self):
        """Auto bot thread with logging"""
        while self.running and self.connected:
            time.sleep(random.uniform(0.1, 0.5))
            self.seq_num += 1

            if random.random() < 0.8:  # 80% chance to move
                # Send move
                direction = random.choice([0, 1, 2, 3])
                dir_names = {0: 'UP', 1: 'DOWN', 2: 'LEFT', 3: 'RIGHT'}
                direction_name = dir_names.get(direction, 'UNKNOWN')

                game_state = self.game.get_state()
                if self.player_id in game_state['players']:
                    row, col = game_state['players'][self.player_id]
                    self.log(f"Auto MOVE: from ({row},{col}) direction={direction_name}")

                self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', direction))
            else:  # 20% chance to claim
                # Send claim
                game_state = self.game.get_state()
                if self.player_id in game_state['players']:
                    row, col = game_state['players'][self.player_id]
                    cell_status = self.game.get_player_cell_status(self.player_id)
                    if cell_status['is_claimed']:
                        if cell_status['is_own_cell']:
                            self.log(f"Auto CLAIM: at ({row},{col}) - already my cell")
                        else:
                            self.log(f"Auto CLAIM: at ({row},{col}) - owned by Player {cell_status['owner']}")
                    else:
                        self.log(f"Auto CLAIM: at ({row},{col}) - unclaimed cell")

                self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num)
                self.pending_claim = True
                self.claim_timer = self.claim_timeout

    def run(self):
        # Connect to server
        self.log(f"Starting connection to server at {self.server_addr[0]}:{self.server_addr[1]}")
        self.send_message(MSG_TYPES['CONNECT'], 0, 0)

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
        smoothing_speed = 5.0
        font = pygame.font.Font(None, 24)  # For text rendering
        small_font = pygame.font.Font(None, 18)  # Smaller font for stats

        # Connection timeout
        connection_start = time.time()
        connection_timeout = 10.0  # 10 seconds timeout

        # Main loop
        while self.running:
            dt = clock.tick(60) / 1000.0

            # Check connection timeout
            if not self.connected and time.time() - connection_start > connection_timeout:
                self.log("Connection timeout - server not responding")
                self.running = False
                break

            # Handle input
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    self.running = False
                    self.log("User requested quit")
                elif event.type == pygame.KEYDOWN and self.player_id:
                    game_state = self.game.get_state()
                    if self.player_id in game_state['players']:
                        row, col = game_state['players'][self.player_id]

                    if event.key == pygame.K_UP:
                        self.seq_num += 1
                        self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', 0))
                        if self.player_id in game_state['players']:
                            self.log(f"User MOVE UP: from ({row},{col})")
                    elif event.key == pygame.K_DOWN:
                        self.seq_num += 1
                        self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', 1))
                        if self.player_id in game_state['players']:
                            self.log(f"User MOVE DOWN: from ({row},{col})")
                    elif event.key == pygame.K_LEFT:
                        self.seq_num += 1
                        self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', 2))
                        if self.player_id in game_state['players']:
                            self.log(f"User MOVE LEFT: from ({row},{col})")
                    elif event.key == pygame.K_RIGHT:
                        self.seq_num += 1
                        self.send_message(MSG_TYPES['MOVE'], 0, self.seq_num, struct.pack('B', 3))
                        if self.player_id in game_state['players']:
                            self.log(f"User MOVE RIGHT: from ({row},{col})")
                    elif event.key == pygame.K_SPACE and not self.pending_claim:
                        self.seq_num += 1
                        cell_status = self.game.get_player_cell_status(self.player_id)
                        if cell_status['is_claimed']:
                            if cell_status['is_own_cell']:
                                self.log(f"User CLAIM: at ({row},{col}) - already my cell")
                            else:
                                self.log(f"User CLAIM: at ({row},{col}) - owned by Player {cell_status['owner']}")
                        else:
                            self.log(f"User CLAIM: at ({row},{col}) - unclaimed cell")

                        self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num)
                        self.pending_claim = True
                        self.claim_timer = self.claim_timeout

            # Handle claim timeout
            if self.pending_claim:
                self.claim_timer -= dt
                if self.claim_timer <= 0:
                    self.seq_num += 1
                    self.log("Claim timeout - retrying...")
                    self.send_message(MSG_TYPES['CLAIM'], 0, self.seq_num)
                    self.claim_timer = self.claim_timeout

            # Smooth positions
            game_state = self.game.get_state()
            for pid, target_pos in game_state['players'].items():
                if pid in self.display_positions:
                    current_pos = self.display_positions[pid]
                    new_pos = GridClashGame.interpolate_position(
                        current_pos, target_pos, smoothing_speed, dt)
                    self.display_positions[pid] = new_pos
                else:
                    self.display_positions[pid] = (float(target_pos[0]), float(target_pos[1]))

            # Log positions
            if self.position_log and self.player_id in self.display_positions:
                curr_time = time.time()
                row, col = self.display_positions[self.player_id]
                self.position_log.write(f"{curr_time},{self.player_id},{row},{col}\n")
                self.position_log.flush()

            # Render
            screen.fill((0, 0, 0))
            game_state = self.game.get_state()
            grid = game_state['grid']
            grid_size = self.game.grid_size

            # Draw grid cells
            for i in range(grid_size):
                for j in range(grid_size):
                    cell_value = grid[i][j]
                    color = GridClashGame.get_color(cell_value)
                    pygame.draw.rect(screen, color,
                                     (j * CELL_SIZE, i * CELL_SIZE, CELL_SIZE, CELL_SIZE))

            # Draw grid lines for better visibility
            for i in range(grid_size + 1):
                # Horizontal lines
                pygame.draw.line(screen, (50, 50, 50),
                                 (0, i * CELL_SIZE),
                                 (grid_size * CELL_SIZE, i * CELL_SIZE), 1)
                # Vertical lines
                pygame.draw.line(screen, (50, 50, 50),
                                 (i * CELL_SIZE, 0),
                                 (i * CELL_SIZE, grid_size * CELL_SIZE), 1)

            # Draw players with outlines when on claimed cells
            for pid, (row, col) in self.display_positions.items():
                # Get player color
                color = GridClashGame.get_color(pid)
                center_x = col * CELL_SIZE + CELL_SIZE / 2
                center_y = row * CELL_SIZE + CELL_SIZE / 2
                radius = int(CELL_SIZE / 3)

                # Check if player should have outline
                should_draw_outline = False
                if 0 <= int(row) < grid_size and 0 <= int(col) < grid_size:
                    # Check the actual grid cell player is standing on
                    cell_row, cell_col = int(row), int(col)
                    should_draw_outline = GridClashGame.should_draw_outline(
                        cell_row, cell_col, grid, pid
                    )

                # Draw player circle
                pygame.draw.circle(screen, color, (int(center_x), int(center_y)), radius)

                # Draw outline if player is on claimed cell
                if should_draw_outline:
                    outline_color = GridClashGame.get_outline_color(pid)
                    # Draw thicker outline around player
                    pygame.draw.circle(screen, outline_color,
                                       (int(center_x), int(center_y)),
                                       radius + 3, 3)  # 3 pixel thick outline

            # Draw player info and status
            if self.player_id:
                # Show which player you are
                player_color = GridClashGame.get_color(self.player_id)
                player_text = font.render(f"You: Player {self.player_id}", True, player_color)
                screen.blit(player_text, (5, 5))

                # Show current score
                scores = game_state['scores']
                score_text = font.render(f"Score: {scores.get(self.player_id, 0)}", True, (255, 255, 255))
                screen.blit(score_text, (5, 30))

                # Show cell status
                cell_status = self.game.get_player_cell_status(self.player_id)
                if cell_status['is_claimed']:
                    if cell_status['is_own_cell']:
                        status_text = f"On YOUR claimed cell"
                        status_color = (0, 255, 0)  # Green
                    else:
                        status_text = f"On Player {cell_status['owner']}'s claimed cell"
                        status_color = (255, 100, 100)  # Reddish
                else:
                    status_text = "On unclaimed cell - CLAIM IT!"
                    status_color = (200, 200, 255)  # Light blue

                status_surface = font.render(status_text, True, status_color)
                screen.blit(status_surface, (5, 55))

                # Show movement rules reminder
                rules_text = small_font.render("NEW: Can move through YOUR claimed cells", True, (200, 200, 100))
                screen.blit(rules_text, (5, grid_size * CELL_SIZE - 70))

                # Show controls reminder
                controls_text = small_font.render("Controls: Arrow Keys = Move, SPACE = Claim, ESC = Quit", True,
                                                  (200, 200, 200))
                screen.blit(controls_text, (5, grid_size * CELL_SIZE - 45))

                # Show connection status
                if self.connected:
                    conn_text = small_font.render(f"Connected as {self.client_name}", True, (0, 255, 0))
                else:
                    conn_text = small_font.render("Connecting to server...", True, (255, 255, 0))
                screen.blit(conn_text, (5, grid_size * CELL_SIZE - 20))

            # Show game over message
            if game_state['game_over']:
                overlay = pygame.Surface((grid_size * CELL_SIZE, grid_size * CELL_SIZE), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 180))  # Semi-transparent black
                screen.blit(overlay, (0, 0))

                winner = game_state['winner']
                winner_color = GridClashGame.get_color(winner)
                winner_text = font.render(f"GAME OVER! Winner: Player {winner}", True, winner_color)
                winner_rect = winner_text.get_rect(center=(grid_size * CELL_SIZE // 2, grid_size * CELL_SIZE // 2 - 30))
                screen.blit(winner_text, winner_rect)

                scores_text = font.render(
                    f"Scores: P1={game_state['scores'].get(1, 0)} | "
                    f"P2={game_state['scores'].get(2, 0)} | "
                    f"P3={game_state['scores'].get(3, 0)} | "
                    f"P4={game_state['scores'].get(4, 0)}",
                    True, (255, 255, 255)
                )
                scores_rect = scores_text.get_rect(center=(grid_size * CELL_SIZE // 2, grid_size * CELL_SIZE // 2 + 10))
                screen.blit(scores_text, scores_rect)

            pygame.display.flip()

        # Cleanup
        pygame.quit()
        self.log("Client shutting down")
        self.log_file.close()
        if self.position_log:
            self.position_log.close()
        if self.metric_file:
            self.metric_file.close()


if __name__ == '__main__':
    auto = len(sys.argv) > 1 and sys.argv[1] == 'auto'
    client_name = None
    if len(sys.argv) > 2:
        client_name = sys.argv[2]

    client = Client(auto=auto, client_name=client_name)
    client.run()