# game.py
"""
Optimized game logic for Grid Clash
"""
import random
import time
import struct
import math
from typing import Dict, Tuple, List, Optional, Set
import zlib

# Game constants
GRID_SIZE = 20
MAX_SCORE_TO_WIN = 200
COLORS = {
    0: (255, 255, 255),  # Unclaimed - white
    1: (255, 50, 50),  # Player 1 - bright red
    2: (50, 255, 50),  # Player 2 - bright green
    3: (50, 100, 255),  # Player 3 - bright blue
    4: (255, 255, 50)  # Player 4 - bright yellow
}
OUTLINE_COLORS = {
    1: (200, 0, 0),  # Darker red outline
    2: (0, 200, 0),  # Darker green outline
    3: (0, 50, 200),  # Darker blue outline
    4: (200, 200, 0)  # Darker yellow outline
}
EVENT_COLORS = {
    1: (255, 215, 0),  # Gold for star
}
DIRECTIONS = {
    'UP': 0,
    'DOWN': 1,
    'LEFT': 2,
    'RIGHT': 3
}

# Event constants
EVENT_STAR = 1
EVENT_DURATION_STAR = 3.0
EVENT_SPAWN_INTERVAL = 3.0
MAX_EVENTS_ON_GRID = 5  # Limit simultaneous events

# Movement validation cache
MOVE_CACHE_SIZE = 100


class PlayerEvent:
    """Represents an active event on a player"""

    def __init__(self, event_type: int, expiration_time: float):
        self.event_type = event_type
        self.expiration_time = expiration_time

    def is_active(self, current_time: float) -> bool:
        return current_time < self.expiration_time

    def get_remaining_time(self, current_time: float) -> float:
        return max(0.0, self.expiration_time - current_time)


class GameEvent:
    """Represents an event on the grid"""

    def __init__(self, event_id: int, event_type: int, row: int, col: int, spawn_time: float):
        self.event_id = event_id
        self.event_type = event_type
        self.row = row
        self.col = col
        self.spawn_time = spawn_time
        self.collected = False
        self.collected_by = 0
        self.collect_time = 0.0


class GridClashGame:
    """
    Optimized game logic with caching and efficient state management
    """

    def __init__(self, grid_size: int = GRID_SIZE, seed: int = 42):
        self.grid_size = grid_size
        self.grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
        self.players: Dict[int, Tuple[int, int]] = {}
        self.scores: Dict[int, int] = {i: 0 for i in range(1, 5)}
        self.game_over = False
        self.winner: Optional[int] = None
        self.win_reason = ""
        self.next_event_id = 1

        # Event system
        self.events: Dict[int, GameEvent] = {}
        self.player_events: Dict[int, PlayerEvent] = {}
        self.last_event_spawn_time = time.time()
        self.event_spawn_positions = set()

        # State caching for delta encoding
        self.last_grid_state = None
        self.last_player_positions = {}
        self.last_snapshot_hash = 0

        # Movement validation cache
        self.move_cache: Dict[Tuple[int, int, int], bool] = {}
        self.cache_hits = 0
        self.cache_misses = 0

        random.seed(seed)

    def update_events(self):
        """Update event states with time-based expiration"""
        current_time = time.time()

        # Spawn new events
        if (current_time - self.last_event_spawn_time >= EVENT_SPAWN_INTERVAL and
                len(self.events) < MAX_EVENTS_ON_GRID):
            self.spawn_event()
            self.last_event_spawn_time = current_time

        # Remove old uncollected events (5 minute lifetime)
        events_to_remove = []
        for event_id, event in self.events.items():
            if (current_time - event.spawn_time > 300.0 and
                    not event.collected):  # 5 minutes
                events_to_remove.append(event_id)

        for event_id in events_to_remove:
            del self.events[event_id]
            if (event.row, event.col) in self.event_spawn_positions:
                self.event_spawn_positions.remove((event.row, event.col))

        # Remove expired player events
        players_to_remove = []
        for player_id, player_event in self.player_events.items():
            if not player_event.is_active(current_time):
                players_to_remove.append(player_id)

        for player_id in players_to_remove:
            del self.player_events[player_id]

    def spawn_event(self):
        """Spawn a single event at random empty accessible position"""
        current_time = time.time()

        # Find empty cells that don't have events or players
        empty_cells = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if (self.grid[i][j] == 0 and
                        (i, j) not in self.event_spawn_positions and
                        not any(pos == (i, j) for pos in self.players.values())):
                    empty_cells.append((i, j))

        if empty_cells:
            row, col = random.choice(empty_cells)

            event = GameEvent(
                event_id=self.next_event_id,
                event_type=EVENT_STAR,
                row=row,
                col=col,
                spawn_time=current_time
            )

            self.events[self.next_event_id] = event
            self.event_spawn_positions.add((row, col))
            self.next_event_id += 1

            return event
        return None

    def check_event_collision(self, player_id: int) -> Optional[GameEvent]:
        """Check if player is standing on an event"""
        if player_id not in self.players:
            return None

        player_row, player_col = self.players[player_id]

        for event in self.events.values():
            if (not event.collected and
                    event.row == player_row and
                    event.col == player_col):
                return event

        return None

    def collect_event(self, player_id: int, event: GameEvent) -> dict:
        """Player collects an event"""
        current_time = time.time()
        event.collected = True
        event.collected_by = player_id
        event.collect_time = current_time

        # Remove from active events
        if (event.row, event.col) in self.event_spawn_positions:
            self.event_spawn_positions.remove((event.row, event.col))

        if event.event_type == EVENT_STAR:
            expiration_time = current_time + EVENT_DURATION_STAR
            self.player_events[player_id] = PlayerEvent(EVENT_STAR, expiration_time)

        # Don't delete immediately for fade-out effect
        return {
            'event_id': event.event_id,
            'event_type': event.event_type,
            'player_id': player_id,
            'expiration_time': expiration_time if event.event_type == EVENT_STAR else 0
        }

    def get_player_cell_status(self, player_id: int) -> dict:
        """Get information about the cell a player is standing on"""
        if player_id not in self.players:
            return {'is_claimed': False, 'owner': 0, 'is_own_cell': False}

        row, col = self.players[player_id]

        if not (0 <= row < self.grid_size and 0 <= col < self.grid_size):
            return {'is_claimed': False, 'owner': 0, 'is_own_cell': False}

        owner = self.grid[row][col]

        return {
            'is_claimed': owner != 0,
            'owner': owner,
            'is_own_cell': owner == player_id
        }

    def add_player(self, player_id: int) -> bool:
        """Add a new player to the game"""
        if player_id in self.players or self.game_over:
            return False
        if not 1 <= player_id <= 4:
            return False

        # Find all unclaimed positions not occupied by events
        valid_positions = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if (self.grid[i][j] == 0 and
                        not any(event.row == i and event.col == j
                                for event in self.events.values() if not event.collected)):
                    valid_positions.append((i, j))

        if not valid_positions:
            return False

        # Place player on random valid cell
        row, col = random.choice(valid_positions)
        self.players[player_id] = (row, col)
        self.last_player_positions[player_id] = (row, col)

        return True

    def move_player(self, player_id: int, direction: int) -> dict:
        """
        Move a player with caching and event checking
        Returns dict with move result and optional event collection
        """
        if player_id not in self.players or self.game_over:
            return {'success': False, 'event_collected': None}

        row, col = self.players[player_id]

        # Check cache first
        cache_key = (player_id, row, col, direction)
        if cache_key in self.move_cache:
            self.cache_hits += 1
            if not self.move_cache[cache_key]:
                return {'success': False, 'event_collected': None}
        else:
            self.cache_misses += 1

        # Calculate new position
        new_row, new_col = self._calculate_move(row, col, direction)

        if (new_row, new_col) == (row, col):
            self.move_cache[cache_key] = False
            return {'success': False, 'event_collected': None}

        # Check movement validity
        target_owner = self.grid[new_row][new_col]
        can_move = False
        steal_cell = False

        if target_owner == 0:
            can_move = True
        elif target_owner == player_id:
            can_move = True
        elif player_id in self.player_events:
            if self.player_events[player_id].event_type == EVENT_STAR:
                can_move = True
                steal_cell = True

        if not can_move:
            self.move_cache[cache_key] = False
            return {'success': False, 'event_collected': None}

        # Update player position
        old_row, old_col = row, col
        self.players[player_id] = (new_row, new_col)

        # Check for event collision at new position
        event_collected = None
        event = self.check_event_collision(player_id)
        if event:
            event_collected = self.collect_event(player_id, event)

        # Handle cell stealing
        if steal_cell and target_owner != 0:
            old_owner = self.grid[new_row][new_col]
            self.grid[new_row][new_col] = player_id
            self.scores[player_id] += 1
            if old_owner in self.scores:
                self.scores[old_owner] = max(0, self.scores[old_owner] - 1)

        # Update cache
        self.move_cache[cache_key] = True

        # Check game over
        if self._is_game_over():
            self.game_over = True
            self.winner, self.win_reason = self._calculate_winner()

        # Prune cache if too large
        if len(self.move_cache) > MOVE_CACHE_SIZE:
            # Remove oldest entries
            keys = list(self.move_cache.keys())
            for key in keys[:MOVE_CACHE_SIZE // 2]:
                del self.move_cache[key]

        return {
            'success': True,
            'old_position': (old_row, old_col),
            'new_position': (new_row, new_col),
            'event_collected': event_collected,
            'steal_cell': steal_cell
        }

    def claim_cell(self, player_id: int) -> dict:
        """Player attempts to claim the cell they're standing on"""
        if player_id not in self.players or self.game_over:
            return self._create_claim_result(False, (0, 0))

        row, col = self.players[player_id]

        if self.grid[row][col] == 0:
            # Successful claim
            self.grid[row][col] = player_id
            self.scores[player_id] += 1

            # Check if game is over
            if self._is_game_over():
                self.game_over = True
                self.winner, self.win_reason = self._calculate_winner()
                return self._create_claim_result(
                    True, (row, col), True, self.winner,
                    self._get_scores_array(), self.win_reason
                )
            else:
                return self._create_claim_result(True, (row, col))
        else:
            # Cell already claimed
            if self.grid[row][col] == player_id:
                return self._create_claim_result(True, (row, col),
                                                 message="Already your cell")
            else:
                return self._create_claim_result(False, (row, col),
                                                 message="Cell claimed by another player")

    def get_compressed_snapshot(self, last_snapshot_hash: int = 0) -> bytes:
        """
        Get compressed snapshot with delta encoding
        Returns (is_compressed, payload)
        """
        # Calculate current hash
        current_hash = self._calculate_state_hash()

        # If state hasn't changed, send minimal payload
        if current_hash == last_snapshot_hash:
            return (False, struct.pack('B', 0))  # No change marker

        # Get delta from last state
        grid_delta, positions_delta = self._get_state_delta()

        # Build payload
        payload = struct.pack('B', 1)  # Changed flag

        # Grid delta
        payload += struct.pack('H', len(grid_delta))
        for row, col, value in grid_delta:
            payload += struct.pack('BBB', row, col, value)

        # Positions delta
        payload += struct.pack('B', len(positions_delta))
        for pid, (row, col) in positions_delta.items():
            payload += struct.pack('BBB', pid, row, col)

        # Active events
        active_events = [e for e in self.events.values() if not e.collected]
        payload += struct.pack('B', len(active_events))
        for event in active_events:
            payload += struct.pack('BBBB', event.event_id, event.event_type,
                                   event.row, event.col)

        # Player events
        current_time = time.time()
        payload += struct.pack('B', len(self.player_events))
        for pid, pevent in self.player_events.items():
            remaining_ms = int(pevent.get_remaining_time(current_time) * 1000)
            payload += struct.pack('BB', pid, pevent.event_type)
            payload += struct.pack('>I', remaining_ms)

        # Scores (only changed ones)
        score_changes = []
        for pid, score in self.scores.items():
            if pid not in self.last_player_positions or score != self.scores.get(pid, 0):
                score_changes.append((pid, score))

        payload += struct.pack('B', len(score_changes))
        for pid, score in score_changes:
            payload += struct.pack('BB', pid, min(score, 255))

        # Compress if payload is large
        if len(payload) > 100:
            compressed = zlib.compress(payload, level=1)
            if len(compressed) < len(payload) * 0.8:  # Only compress if significant savings
                return (True, compressed)

        return (False, payload)

    def update_from_delta(self, delta_data: bytes) -> bool:
        """Update game state from delta snapshot"""
        try:
            if len(delta_data) < 1:
                return False

            changed_flag = struct.unpack('B', delta_data[:1])[0]
            if changed_flag == 0:
                return True  # No changes

            idx = 1

            # Grid delta
            if idx + 2 > len(delta_data):
                return False
            num_grid_changes = struct.unpack('H', delta_data[idx:idx + 2])[0]
            idx += 2

            for _ in range(num_grid_changes):
                if idx + 3 > len(delta_data):
                    break
                row, col, value = struct.unpack('BBB', delta_data[idx:idx + 3])
                if 0 <= row < self.grid_size and 0 <= col < self.grid_size:
                    self.grid[row][col] = value
                idx += 3

            # Positions delta
            if idx + 1 > len(delta_data):
                return False
            num_position_changes = delta_data[idx]
            idx += 1

            for _ in range(num_position_changes):
                if idx + 3 > len(delta_data):
                    break
                pid, row, col = struct.unpack('BBB', delta_data[idx:idx + 3])
                if 1 <= pid <= 4 and 0 <= row < self.grid_size and 0 <= col < self.grid_size:
                    self.players[pid] = (row, col)
                idx += 3

            # Recalculate scores
            self._recalculate_scores()

            # Check game over
            if not self.game_over:
                self.game_over = self._is_game_over()
                if self.game_over:
                    self.winner, self.win_reason = self._calculate_winner()

            return True
        except:
            return False

    def get_state(self) -> dict:
        """Get current game state for client rendering"""
        return {
            'grid': [row[:] for row in self.grid],
            'players': self.players.copy(),
            'scores': self.scores.copy(),
            'events': {eid: {
                'type': e.event_type,
                'row': e.row,
                'col': e.col,
                'collected': e.collected
            } for eid, e in self.events.items() if not e.collected},
            'player_events': {pid: {
                'type': pe.event_type,
                'expiration_time': pe.expiration_time
            } for pid, pe in self.player_events.items()},
            'game_over': self.game_over,
            'winner': self.winner,
            'win_reason': self.win_reason,
            'grid_size': self.grid_size
        }

    def reset(self) -> None:
        """Reset game to initial state"""
        self.grid = [[0 for _ in range(self.grid_size)] for _ in range(self.grid_size)]
        self.players.clear()
        self.scores = {i: 0 for i in range(1, 5)}
        self.game_over = False
        self.winner = None
        self.win_reason = ""
        self.events.clear()
        self.player_events.clear()
        self.event_spawn_positions.clear()
        self.next_event_id = 1
        self.last_event_spawn_time = time.time()
        self.move_cache.clear()
        self.last_grid_state = None
        self.last_player_positions.clear()
        self.last_snapshot_hash = 0

    # Private helper methods
    def _calculate_move(self, row: int, col: int, direction: int) -> Tuple[int, int]:
        if direction == DIRECTIONS['UP'] and row > 0:
            return (row - 1, col)
        elif direction == DIRECTIONS['DOWN'] and row < self.grid_size - 1:
            return (row + 1, col)
        elif direction == DIRECTIONS['LEFT'] and col > 0:
            return (row, col - 1)
        elif direction == DIRECTIONS['RIGHT'] and col < self.grid_size - 1:
            return (row, col + 1)
        return (row, col)

    def _is_game_over(self) -> bool:
        # Check max score
        for score in self.scores.values():
            if score >= MAX_SCORE_TO_WIN:
                return True

        # Check all cells claimed
        for row in self.grid:
            for cell in row:
                if cell == 0:
                    return False
        return True

    def _calculate_winner(self) -> Tuple[int, str]:
        # Check for max score win
        for pid, score in self.scores.items():
            if score >= MAX_SCORE_TO_WIN:
                return pid, f"Reached {MAX_SCORE_TO_WIN} blocks!"

        # Highest score win
        max_score = -1
        winner = 0
        for pid, score in self.scores.items():
            if score > max_score:
                max_score = score
                winner = pid

        return winner, "All cells claimed"

    def _get_scores_array(self) -> List[int]:
        scores_arr = [0] * 5
        for pid, score in self.scores.items():
            scores_arr[pid] = min(score, 255)
        return scores_arr

    def _recalculate_scores(self) -> None:
        # Recalculate from grid
        temp_scores = {i: 0 for i in range(1, 5)}
        for row in self.grid:
            for cell in row:
                if 1 <= cell <= 4:
                    temp_scores[cell] += 1
        self.scores = temp_scores

    def _create_claim_result(self, success: bool, position: Tuple[int, int],
                             game_over: bool = False, winner: int = 0,
                             scores: Optional[List[int]] = None,
                             win_reason: str = "",
                             message: str = "") -> dict:
        result = {
            'success': success,
            'position': position,
            'game_over': game_over,
            'message': message
        }
        if game_over:
            result['winner'] = winner
            result['win_reason'] = win_reason
            result['scores'] = scores or self._get_scores_array()
        return result

    def _calculate_state_hash(self) -> int:
        """Calculate hash of current game state for delta encoding"""
        import hashlib
        h = hashlib.md5()

        # Hash grid
        for row in self.grid:
            h.update(bytes(row))

        # Hash player positions
        for pid in sorted(self.players.keys()):
            row, col = self.players[pid]
            h.update(bytes([pid, row, col]))

        # Hash scores
        for pid in sorted(self.scores.keys()):
            h.update(bytes([pid, self.scores[pid] & 0xFF]))

        return int.from_bytes(h.digest()[:4], 'little')

    def _get_state_delta(self):
        """Get changes since last state"""
        grid_delta = []
        positions_delta = {}

        # Compare with last known state
        if self.last_grid_state:
            for i in range(self.grid_size):
                for j in range(self.grid_size):
                    if self.grid[i][j] != self.last_grid_state[i][j]:
                        grid_delta.append((i, j, self.grid[i][j]))
        else:
            # First time, mark all as changed
            for i in range(self.grid_size):
                for j in range(self.grid_size):
                    grid_delta.append((i, j, self.grid[i][j]))

        # Player positions
        for pid, pos in self.players.items():
            last_pos = self.last_player_positions.get(pid)
            if last_pos != pos:
                positions_delta[pid] = pos

        # Update cached state
        self.last_grid_state = [row[:] for row in self.grid]
        self.last_player_positions = self.players.copy()
        self.last_snapshot_hash = self._calculate_state_hash()

        return grid_delta, positions_delta

    # Static utility methods
    @staticmethod
    def calculate_position_error(pos1: Tuple[float, float], pos2: Tuple[float, float]) -> float:
        return ((pos1[0] - pos2[0]) ** 2 + (pos1[1] - pos2[1]) ** 2) ** 0.5

    @staticmethod
    def interpolate_position(current: Tuple[float, float], target: Tuple[float, float],
                             speed: float, dt: float) -> Tuple[float, float]:
        curr_row, curr_col = current
        target_row, target_col = target

        if curr_row != target_row:
            step = min(speed * dt, abs(curr_row - target_row))
            curr_row += step * (1 if target_row > curr_row else -1)

        if curr_col != target_col:
            step = min(speed * dt, abs(curr_col - target_col))
            curr_col += step * (1 if target_col > curr_col else -1)

        return (curr_row, curr_col)

    @staticmethod
    def get_color(player_id: int) -> Tuple[int, int, int]:
        return COLORS.get(player_id, (255, 255, 255))

    @staticmethod
    def get_outline_color(player_id: int) -> Tuple[int, int, int]:
        return OUTLINE_COLORS.get(player_id, (150, 150, 150))

    @staticmethod
    def get_event_color(event_type: int) -> Tuple[int, int, int]:
        return EVENT_COLORS.get(event_type, (255, 255, 255))

    @staticmethod
    def should_draw_outline(player_row: int, player_col: int, grid: List[List[int]],
                            player_id: int) -> bool:
        if not (0 <= player_row < len(grid) and 0 <= player_col < len(grid[0])):
            return False

        cell_owner = grid[player_row][player_col]
        return cell_owner != 0

    @staticmethod
    def get_event_name(event_type: int) -> str:
        if event_type == EVENT_STAR:
            return "Star"
        return "Unknown"