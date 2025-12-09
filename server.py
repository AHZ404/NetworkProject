# server.py
import socket
import time
import struct
import threading
import psutil
from datetime import datetime
from common import *
from game import GridClashGame  # Import consolidated game logic


class Server:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', PORT))

        # Game instance
        self.game = GridClashGame()

        # Network state
        self.clients = {}  # addr -> player_id
        self.player_addrs = {}  # player_id -> addr
        self.next_player_id = 1
        self.snapshot_id = 0
        self.seq_num = 0
        self.running = True
        self.lock = threading.Lock()
        self.last_snapshot_payload = None
        self.start_time = time.time()
        self.total_packets_sent = 0
        self.total_packets_received = 0

        # Event timing
        self.last_event_spawn_time = time.time()
        self.event_spawn_interval = 3.0  # Every 3 seconds
        self.last_event_check_time = time.time()

        # Logging
        self.log_file = open('server_log.txt', 'w')
        self.position_log = open('server_position_log.csv', 'w')
        self.position_log.write('time,snapshot_id,player_id,row,col\n')
        self.metrics_log = open('server_metrics.csv', 'w')
        self.metrics_log.write('timestamp,cpu_percent,clients_connected,packets_sent,packets_received,bandwidth_kbps\n')

        self.packets_sent = 0
        self.bytes_sent = 0
        self.metrics_update_interval = 2.0
        self.last_metrics_time = time.time()

    def _get_timestamp(self):
        """Get current timestamp in the format: YYYY-MM-DD HH:MM:SS,SSS"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    def log(self, msg, prefix="[SERVER]"):
        """Enhanced logging with timestamp"""
        timestamp = self._get_timestamp()
        full_msg = f"{timestamp} - {prefix} {msg}"
        print(full_msg)  # Also print to console
        self.log_file.write(full_msg + "\n")
        self.log_file.flush()

    def send_message(self, addr, msg_type, snapshot_id, seq_num, payload=b''):
        timestamp = int(time.time() * 1000)
        payload_len = len(payload)
        checksum = compute_checksum(payload)
        header = struct.pack(HEADER_FMT, PROTO_ID, VERSION, msg_type,
                             snapshot_id, seq_num, timestamp, payload_len, checksum)
        data = header + payload
        self.sock.sendto(data, addr)
        self.packets_sent += 1
        self.total_packets_sent += 1
        self.bytes_sent += len(data)

        # Enhanced logging for sent messages
        player_id = self.clients.get(addr, 0)
        if msg_type == MSG_TYPES['WELCOME']:
            if len(payload) >= 2:
                assigned_id, grid_size = struct.unpack('BB', payload[:2])
                self.log(f"Sent WELCOME to Player{assigned_id} at {addr[0]}:{addr[1]} seq={seq_num}")
        elif msg_type == MSG_TYPES['SNAPSHOT']:
            # Don't log every snapshot to avoid spam, log every 10th
            if snapshot_id % 10 == 0:
                game_state = self.game.get_state()
                player_count = len(game_state['players'])
                self.log(f"Broadcast SNAPSHOT seq={seq_num} to {player_count} players")
        elif msg_type == MSG_TYPES['ACK']:
            if len(payload) >= 2:
                row, col = struct.unpack('BB', payload[:2])
                self.log(f"Sent ACK to Player{player_id} seq={seq_num}: claim_success at ({row},{col})")
        elif msg_type == MSG_TYPES['NACK']:
            if len(payload) >= 2:
                row, col = struct.unpack('BB', payload[:2])
                self.log(f"Sent NACK to Player{player_id} seq={seq_num}: claim_failed at ({row},{col})")
        elif msg_type == MSG_TYPES['GAME_OVER']:
            if len(payload) >= 6:  # Updated for win_reason
                winner = payload[0]
                win_reason_len = payload[1]
                if win_reason_len > 0:
                    win_reason = payload[2:2 + win_reason_len].decode('utf-8', errors='ignore')
                else:
                    win_reason = "Game ended"
                self.log(f"Broadcast GAME_OVER seq={seq_num}: winner=Player{winner}, reason={win_reason}")
        elif msg_type == MSG_TYPES['EVENT_SPAWN']:
            if len(payload) >= 4:
                event_id, event_type, row, col = struct.unpack('BBBB', payload[:4])
                event_name = "Star"
                self.log(f"Sent EVENT_SPAWN: {event_name} at ({row},{col}) to {addr[0]}:{addr[1]}")
        elif msg_type == MSG_TYPES['EVENT_COLLECT']:
            if len(payload) >= 4:
                event_id, event_type, player_id, _ = struct.unpack('BBBB', payload[:4])
                event_name = "Star"
                self.log(f"Sent EVENT_COLLECT: Player{player_id} collected {event_name}")

    def broadcast_thread(self):
        """Broadcast game state with redundancy and handle events"""
        last_log_time = time.time()

        while self.running:
            time.sleep(UPDATE_INTERVAL)
            with self.lock:
                current_time = time.time()

                # Update events
                self.game.update_events()

                # Check for event collisions
                if current_time - self.last_event_check_time > 0.1:  # Check every 100ms
                    self.check_event_collisions()
                    self.last_event_check_time = current_time

                self.snapshot_id += 1
                self.seq_num += 1

                # Get snapshot from game (now includes events)
                current_payload = self.game.get_snapshot_payload()

                # Add redundancy
                if self.last_snapshot_payload:
                    redundant_payload = self.last_snapshot_payload + current_payload
                else:
                    redundant_payload = current_payload
                self.last_snapshot_payload = current_payload

                # Log positions
                curr_time = time.time()
                game_state = self.game.get_state()
                for pid, (row, col) in game_state['players'].items():
                    self.position_log.write(f"{curr_time},{self.snapshot_id},{pid},{row},{col}\n")
                self.position_log.flush()

                # Broadcast to all clients
                client_count = len(self.clients)
                for addr in list(self.clients.keys()):
                    self.send_message(addr, MSG_TYPES['SNAPSHOT'],
                                      self.snapshot_id, self.seq_num, redundant_payload)

                # Send event spawn messages if new events spawned
                if current_time - self.last_event_spawn_time >= self.event_spawn_interval:
                    self.broadcast_new_events()
                    self.last_event_spawn_time = current_time

                # Log snapshot broadcast periodically
                if time.time() - last_log_time > 5.0:  # Every 5 seconds
                    grid_filled = sum(cell != 0 for row in game_state['grid'] for cell in row)
                    total_cells = self.game.grid_size * self.game.grid_size
                    active_events = len(game_state['events'])
                    active_player_events = len(game_state['player_events'])

                    # Show scores and check for near-win
                    scores_info = []
                    for pid in range(1, 5):
                        if pid in game_state['scores']:
                            score = game_state['scores'][pid]
                            scores_info.append(f"P{pid}={score}")
                            if score >= 180:  # Alert when close to win
                                self.log(f"ALERT: Player {pid} has {score} blocks! Close to winning!")

                    self.log(
                        f"Status: {client_count} players, scores: {', '.join(scores_info)}, "
                        f"grid: {grid_filled}/{total_cells} filled, events: {active_events} active")
                    last_log_time = time.time()

                # Update metrics
                self._update_metrics()

    def check_event_collisions(self):
        """Check if any player has collected an event"""
        for player_id in list(self.clients.values()):
            event = self.game.check_event_collision(player_id)
            if event:
                result = self.game.collect_event(player_id, event)

                # Log event collection
                event_name = "Star"
                self.log(f"Player {player_id} collected {event_name} event at ({event.row},{event.col})")

                # Send event collect message to all clients
                payload = struct.pack('BBBB', event.event_id, event.event_type, player_id, 0)
                for addr in list(self.clients.keys()):
                    self.send_message(addr, MSG_TYPES['EVENT_COLLECT'], 0, 0, payload)

    def broadcast_new_events(self):
        """Broadcast newly spawned events to all clients"""
        game_state = self.game.get_state()
        spawned_count = 0

        for event_id, event_data in game_state['events'].items():
            if not event_data['collected']:
                # Check if this is a newly spawned event (not already broadcast)
                # We'll send all active events each time for simplicity

                # Send event spawn message
                payload = struct.pack('BBBB', event_id, event_data['type'],
                                      event_data['row'], event_data['col'])

                for addr in list(self.clients.keys()):
                    self.send_message(addr, MSG_TYPES['EVENT_SPAWN'], 0, 0, payload)

                spawned_count += 1

        if spawned_count > 0:
            self.log(f"Spawned {spawned_count} Star event(s)")

    def _update_metrics(self):
        current_time = time.time()
        if current_time - self.last_metrics_time >= self.metrics_update_interval:
            cpu_percent = psutil.cpu_percent(interval=None)
            bandwidth_kbps = (self.bytes_sent * 8 / 1000) / (current_time - self.last_metrics_time)

            self.metrics_log.write(
                f"{current_time:.3f},{cpu_percent},{len(self.clients)},"
                f"{self.total_packets_sent},{self.total_packets_received},{bandwidth_kbps:.2f}\n")
            self.metrics_log.flush()

            self.bytes_sent = 0
            self.last_metrics_time = current_time

    def handle_connect(self, addr):
        if addr not in self.clients and self.next_player_id <= 4:
            player_id = self.next_player_id
            self.next_player_id += 1

            # Add to network tables
            self.clients[addr] = player_id
            self.player_addrs[player_id] = addr

            # Add player to game
            if self.game.add_player(player_id):
                payload = struct.pack('BB', player_id, self.game.grid_size)
                self.send_message(addr, MSG_TYPES['WELCOME'], 0, 0, payload)
                self.log(f"Player {player_id} connected from {addr[0]}:{addr[1]}")

                # Log initial position
                game_state = self.game.get_state()
                if player_id in game_state['players']:
                    row, col = game_state['players'][player_id]
                    self.log(f"Player {player_id} placed at starting position ({row},{col})")

                    # Send current events to new player
                    self.send_current_events_to_player(addr)
            else:
                self.log(f"Failed to add player {player_id} - game full or error")

    def send_current_events_to_player(self, addr):
        """Send all current active events to a newly connected player"""
        game_state = self.game.get_state()

        for event_id, event_data in game_state['events'].items():
            if not event_data['collected']:
                payload = struct.pack('BBBB', event_id, event_data['type'],
                                      event_data['row'], event_data['col'])
                self.send_message(addr, MSG_TYPES['EVENT_SPAWN'], 0, 0, payload)

    def handle_move(self, player_id, payload):
        if len(payload) == 1:
            direction = struct.unpack('B', payload)[0]
            dir_names = {0: 'UP', 1: 'DOWN', 2: 'LEFT', 3: 'RIGHT'}
            direction_name = dir_names.get(direction, 'UNKNOWN')

            # Get current position before move
            game_state = self.game.get_state()
            old_row, old_col = game_state['players'].get(player_id, (-1, -1))

            # Attempt move
            success = self.game.move_player(player_id, direction)

            # Get new position
            game_state = self.game.get_state()
            new_row, new_col = game_state['players'].get(player_id, (-1, -1))

            # Check for event collision after move
            if success:
                event = self.game.check_event_collision(player_id)
                if event:
                    result = self.game.collect_event(player_id, event)

                    # Log event collection
                    event_name = "Star"
                    self.log(f"Player {player_id} collected {event_name} event while moving to ({new_row},{new_col})")

                    # Send event collect message to all clients
                    payload = struct.pack('BBBB', event.event_id, event.event_type, player_id, 0)
                    for addr in list(self.clients.keys()):
                        self.send_message(addr, MSG_TYPES['EVENT_COLLECT'], 0, 0, payload)

            if success:
                self.log(f"Player {player_id} moved {direction_name}: ({old_row},{old_col}) -> ({new_row},{new_col})")
            else:
                # Player tried to move but was blocked
                target_row, target_col = old_row, old_col
                if direction == 0 and old_row > 0:  # UP
                    target_row = old_row - 1
                elif direction == 1 and old_row < self.game.grid_size - 1:  # DOWN
                    target_row = old_row + 1
                elif direction == 2 and old_col > 0:  # LEFT
                    target_col = old_col - 1
                elif direction == 3 and old_col < self.game.grid_size - 1:  # RIGHT
                    target_col = old_col + 1

                cell_owner = self.game.grid[target_row][
                    target_col] if 0 <= target_row < self.game.grid_size and 0 <= target_col < self.game.grid_size else -1

                if cell_owner == 0:
                    self.log(f"Player {player_id} move {direction_name} blocked: at grid edge")
                elif cell_owner == player_id:
                    # This shouldn't happen with new rules, but just in case
                    self.log(f"Player {player_id} move {direction_name} blocked: bug?")
                else:
                    self.log(
                        f"Player {player_id} move {direction_name} blocked: target cell ({target_row},{target_col}) owned by Player {cell_owner}")

    def handle_claim(self, player_id, addr):
        # Get position before claim
        game_state = self.game.get_state()
        row, col = game_state['players'].get(player_id, (-1, -1))
        cell_before = self.game.grid[row][
            col] if 0 <= row < self.game.grid_size and 0 <= col < self.game.grid_size else -1

        result = self.game.claim_cell(player_id)

        if result['success']:
            row, col = result['position']
            self.send_message(addr, MSG_TYPES['ACK'], 0, 0, struct.pack('BB', row, col))

            if cell_before == 0:
                self.log(f"Player {player_id} claimed new cell at ({row},{col})")
            elif cell_before == player_id:
                self.log(f"Player {player_id} re-claimed own cell at ({row},{col})")
            else:
                self.log(f"Player {player_id} claimed cell at ({row},{col}) previously owned by Player {cell_before}")

            if result['game_over']:
                winner = result['winner']
                win_reason = result.get('win_reason', 'Game ended')
                scores = result['scores']

                # Send win reason along with winner
                win_reason_bytes = win_reason.encode('utf-8')
                payload = struct.pack('BB', winner, len(win_reason_bytes)) + win_reason_bytes

                # Add scores (4 bytes)
                payload += struct.pack('BBBB', scores[1], scores[2], scores[3], scores[4])

                for client_addr in list(self.clients.keys()):
                    self.send_message(client_addr, MSG_TYPES['GAME_OVER'], 0, 0, payload)

                self.running = False
                self.log(f"GAME OVER! Winner: Player {winner} - {win_reason}")
                self.log(f"Final scores: P1={scores[1]}, P2={scores[2]}, P3={scores[3]}, P4={scores[4]}")
                print(f"\n{'=' * 60}")
                print(f"GAME OVER! Winner: Player {winner}")
                print(f"Reason: {win_reason}")
                print(f"Final Scores: P1={scores[1]}, P2={scores[2]}, P3={scores[3]}, P4={scores[4]}")
                print(f"{'=' * 60}\n")
        else:
            row, col = result['position']
            cell_owner = self.game.grid[row][col]
            self.send_message(addr, MSG_TYPES['NACK'], 0, 0, struct.pack('BB', row, col))

            if cell_owner == player_id:
                self.log(f"Player {player_id} failed to claim cell at ({row},{col}): already their own cell")
            else:
                self.log(f"Player {player_id} failed to claim cell at ({row},{col}): owned by Player {cell_owner}")

    def run(self):
        self.log(f"Starting server on port {PORT}")
        self.log(f"Game rules: Players can move through THEIR OWN claimed cells")
        self.log(f"Game rules: Players cannot move through OTHER players' claimed cells")
        self.log(f"Win condition: First player to claim 200 blocks OR all cells claimed")
        self.log(f"Special events: 1 Star event spawns every 3 seconds in empty cells")
        self.log(f"Star event: Steal enemy blocks by moving over them (3s duration)")
        self.log(f"Update rate: {UPDATE_HZ}Hz, Grid size: {self.game.grid_size}x{self.game.grid_size}")
        print(f"\n{'=' * 60}")
        print(f"Grid Clash Server Running")
        print(f"Port: {PORT}, Update Rate: {UPDATE_HZ}Hz")
        print(f"Win Condition: First to 200 blocks OR all cells claimed")
        print(f"Special Event: ★ Star (steal enemy cells)")
        print(f"Star spawns every 3 seconds in empty cells")
        print(f"Waiting for players to connect...")
        print(f"{'=' * 60}\n")

        broadcast_thread = threading.Thread(target=self.broadcast_thread, daemon=True)
        broadcast_thread.start()

        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                self.total_packets_received += 1

                if len(data) < HEADER_SIZE:
                    continue

                header = data[:HEADER_SIZE]
                protocol_id, version, msg_type, snapshot_id, seq_num, timestamp, payload_len, checksum = struct.unpack(
                    HEADER_FMT, header)

                if protocol_id != PROTO_ID or version != VERSION or payload_len != len(data) - HEADER_SIZE:
                    continue

                payload = data[HEADER_SIZE:]
                if compute_checksum(payload) != checksum:
                    continue

                with self.lock:
                    if msg_type == MSG_TYPES['CONNECT']:
                        self.log(f"Received CONNECT from {addr[0]}:{addr[1]} seq={seq_num}")
                        self.handle_connect(addr)
                    elif addr in self.clients:
                        player_id = self.clients[addr]
                        if msg_type == MSG_TYPES['MOVE']:
                            self.handle_move(player_id, payload)
                        elif msg_type == MSG_TYPES['CLAIM']:
                            self.log(f"Received CLAIM from Player{player_id} seq={seq_num}")
                            self.handle_claim(player_id, addr)
                        elif msg_type == MSG_TYPES['ACK']:
                            # Client ACK for our message
                            pass
                        elif msg_type == MSG_TYPES['EVENT_COLLECT']:
                            # Client might send event collect (though server initiates)
                            pass

            except Exception as e:
                self.log(f"Error: {e}")

        self.cleanup()

    def cleanup(self):
        runtime = time.time() - self.start_time
        self.log(f"Shutting down after {runtime:.1f} seconds")
        self.log(f"Total packets sent: {self.total_packets_sent}, received: {self.total_packets_received}")

        # Log final event statistics
        game_state = self.game.get_state()
        event_stats = {'Star': {'total': 0, 'collected': 0}}

        for event_id, event_data in game_state.get('events', {}).items():
            event_stats['Star']['total'] += 1
            if event_data['collected']:
                event_stats['Star']['collected'] += 1

        for event_name, stats in event_stats.items():
            collection_rate = (stats['collected'] / stats['total'] * 100) if stats['total'] > 0 else 0
            self.log(f"{event_name} events: {stats['collected']}/{stats['total']} collected ({collection_rate:.1f}%)")

        self.log_file.close()
        self.position_log.close()
        self.metrics_log.close()
        self.sock.close()

        print(f"\n{'=' * 60}")
        print(f"Server shutdown complete")
        print(f"Runtime: {runtime:.1f}s")
        print(f"Total packets: {self.total_packets_sent} sent, {self.total_packets_received} received")

        # Print event statistics
        if event_stats:
            print(f"Event Statistics:")
            for event_name, stats in event_stats.items():
                collection_rate = (stats['collected'] / stats['total'] * 100) if stats['total'] > 0 else 0
                print(f"  {event_name}: {stats['collected']}/{stats['total']} collected ({collection_rate:.1f}%)")

        print(f"{'=' * 60}")


if __name__ == '__main__':
    server = Server()
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Server interrupted by user")
        server.running = False
        server.cleanup()