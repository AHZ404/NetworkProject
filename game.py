# game.py
"""
Consolidated game logic for Grid Clash
Contains all game rules, state management, and serialization
"""
import random
import time
import struct
import math
from typing import Dict, Tuple, List, Optional, Set

# Game constants
GRID_SIZE = 20
MAX_SCORE_TO_WIN = 200  # New: Game ends when player reaches 200 blocks
COLORS = {
    0: (255, 255, 255),  # Unclaimed - white
    1: (255, 0, 0),  # Player 1 - red
    2: (0, 255, 0),  # Player 2 - green
    3: (0, 0, 255),  # Player 3 - blue
    4: (255, 255, 0)  # Player 4 - yellow
}
OUTLINE_COLORS = {
    1: (200, 50, 50),  # Darker red outline
    2: (50, 200, 50),  # Darker green outline
    3: (50, 50, 200),  # Darker blue outline
    4: (200, 200, 50)  # Darker yellow outline
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
EVENT_DURATION_STAR = 3.0  # 3 seconds
EVENT_SPAWN_INTERVAL = 3.0  # Every 3 seconds (changed from 5)
EVENTS_PER_SPAWN = 1  # Always spawn exactly 1 event (no randomness)


class PlayerEvent:
    """Represents an active event on a player"""

    def __init__(self, event_type: int, expiration_time: float):
        self.event_type = event_type
        self.expiration_time = expiration_time

    def is_active(self, current_time: float) -> bool:
        return current_time < self.expiration_time


class GameEvent:
    """Represents an event on the grid"""

    def __init__(self, event_id: int, event_type: int, row: int, col: int, spawn_time: float):
        self.event_id = event_id
        self.event_type = event_type  # Only STAR event now
        self.row = row
        self.col = col
        self.spawn_time = spawn_time
        self.collected = False
        self.collected_by = 0


class GridClashGame:
    """
    Complete game logic for Grid Clash multiplayer game.
    Updated rules:
    1. Players can move through their OWN claimed cells
    2. Players cannot move through OTHER players' claimed cells
    3. Player outline shows when standing on claimed cell
    4. Star event spawns every 3 seconds in empty accessible cells
    5. Game ends when any player reaches 200 claimed blocks OR all cells claimed
    """

    def __init__(self, grid_size: int = GRID_SIZE, seed: int = 42):
        self.grid_size = grid_size
        self.grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
        self.players: Dict[int, Tuple[int, int]] = {}  # player_id -> (row, col)
        self.scores: Dict[int, int] = {}  # player_id -> score
        self.game_over = False
        self.winner: Optional[int] = None
        self.win_reason = ""  # Track why game ended
        self.next_event_id = 1

        # Event system (only STAR events now)
        self.events: Dict[int, GameEvent] = {}  # event_id -> GameEvent
        self.player_events: Dict[int, PlayerEvent] = {}  # player_id -> active PlayerEvent
        self.last_event_spawn_time = time.time()

        random.seed(seed)

        # Initialize scores for all possible players
        for i in range(1, 5):
            self.scores[i] = 0

    def update_events(self):
        """Update event states and check for expirations"""
        current_time = time.time()

        # Spawn new events every 3 seconds (always exactly 1 STAR event)
        if current_time - self.last_event_spawn_time >= EVENT_SPAWN_INTERVAL:
            self.spawn_events()
            self.last_event_spawn_time = current_time

        # Check for event expirations on players
        players_to_remove = []
        for player_id, player_event in self.player_events.items():
            if not player_event.is_active(current_time):
                players_to_remove.append(player_id)

        for player_id in players_to_remove:
            del self.player_events[player_id]

    def spawn_events(self):
        """Spawn exactly 1 STAR event at random empty accessible position"""
        current_time = time.time()

        # Find all empty accessible cells (not claimed and no player and no existing event)
        empty_cells = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                # Check if cell is empty and no player on it
                if self.grid[i][j] == 0:
                    has_player = False
                    for player_pos in self.players.values():
                        if player_pos == (i, j):
                            has_player = True
                            break
                    if not has_player:
                        empty_cells.append((i, j))

        if empty_cells:
            row, col = random.choice(empty_cells)

            event = GameEvent(
                event_id=self.next_event_id,
                event_type=EVENT_STAR,  # Always STAR event
                row=row,
                col=col,
                spawn_time=current_time
            )

            self.events[self.next_event_id] = event
            self.next_event_id += 1

    def check_event_collision(self, player_id: int) -> Optional[GameEvent]:
        """Check if player is standing on an event"""
        if player_id not in self.players:
            return None

        player_row, player_col = self.players[player_id]

        for event in self.events.values():
            if not event.collected and event.row == player_row and event.col == player_col:
                return event

        return None

    def collect_event(self, player_id: int, event: GameEvent) -> dict:
        """Player collects an event (only STAR events now)"""
        event.collected = True
        event.collected_by = player_id

        current_time = time.time()

        if event.event_type == EVENT_STAR:
            # Star: Can steal enemy blocks by moving over them for 3 seconds
            expiration_time = current_time + EVENT_DURATION_STAR
            self.player_events[player_id] = PlayerEvent(EVENT_STAR, expiration_time)
            event_name = "Star"
            effect = "Can steal enemy blocks by moving over them"

        # Remove from active events
        del self.events[event.event_id]

        return {
            'event_id': event.event_id,
            'event_type': event.event_type,
            'event_name': event_name,
            'player_id': player_id,
            'effect': effect,
            'expiration_time': expiration_time
        }

    def get_player_cell_status(self, player_id: int) -> dict:
        """
        Get information about the cell a player is standing on.

        Returns:
            Dictionary with:
            - 'is_claimed': bool
            - 'owner': int (0 if unclaimed, player_id if claimed)
            - 'is_own_cell': bool (True if player owns this cell)
        """
        if player_id not in self.players:
            return {'is_claimed': False, 'owner': 0, 'is_own_cell': False}

        row, col = self.players[player_id]

        # Check bounds
        if not (0 <= row < self.grid_size and 0 <= col < self.grid_size):
            return {'is_claimed': False, 'owner': 0, 'is_own_cell': False}

        owner = self.grid[row][col]

        return {
            'is_claimed': owner != 0,
            'owner': owner,
            'is_own_cell': owner == player_id
        }

    def add_player(self, player_id: int) -> bool:
        """Add a new player to the game."""
        if player_id in self.players or self.game_over:
            return False
        if not 1 <= player_id <= 4:
            return False

        # Find all unclaimed positions
        unclaimed_positions = []
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if self.grid[i][j] == 0:
                    unclaimed_positions.append((i, j))

        if not unclaimed_positions:
            return False

        # Place player on random unclaimed cell
        row, col = random.choice(unclaimed_positions)
        self.players[player_id] = (row, col)
        self.scores[player_id] = 0

        return True

    def move_player(self, player_id: int, direction: int) -> bool:
        """
        Move a player in the given direction.
        NEW RULE: Players can move through their OWN claimed cells.
        SPECIAL EVENT: Star allows stealing enemy blocks.
        """
        if player_id not in self.players or self.game_over:
            return False

        row, col = self.players[player_id]

        new_row, new_col = self._calculate_move(row, col, direction)

        # Check if move is valid (not out of bounds)
        if (new_row, new_col) == (row, col):
            return False

        # Check for event collisions BEFORE moving
        # (Events are checked at the new position)
        event_at_new_pos = None
        for event in self.events.values():
            if not event.collected and event.row == new_row and event.col == new_col:
                event_at_new_pos = event
                break

        # Check if target cell is accessible
        target_cell_owner = self.grid[new_row][new_col]

        move_allowed = False
        steal_cell = False

        if target_cell_owner == 0:  # Unclaimed cell - always allowed
            move_allowed = True
        elif target_cell_owner == player_id:  # Player's own claimed cell - allowed
            move_allowed = True
        else:  # Other player's claimed cell
            # Check if player has star event (can steal by moving over)
            if player_id in self.player_events:
                if self.player_events[player_id].event_type == EVENT_STAR:
                    move_allowed = True
                    steal_cell = True
            else:
                move_allowed = False

        if move_allowed:
            # Update player position
            old_row, old_col = row, col
            self.players[player_id] = (new_row, new_col)

            # Handle cell stealing if applicable
            if steal_cell and target_cell_owner != 0:
                # Steal the cell from other player
                old_owner = self.grid[new_row][new_col]
                self.grid[new_row][new_col] = player_id
                self.scores[player_id] += 1
                if old_owner in self.scores:
                    self.scores[old_owner] = max(0, self.scores[old_owner] - 1)

            # Check if game is over after move
            if self._is_game_over():
                self.game_over = True
                self.winner, self.win_reason = self._calculate_winner()

            return True

        return False

    def claim_cell(self, player_id: int) -> dict:
        """Player attempts to claim the cell they're standing on."""
        if player_id not in self.players or self.game_over:
            return self._create_claim_result(False, (0, 0))

        row, col = self.players[player_id]

        if self.grid[row][col] == 0:
            # Successful claim
            self.grid[row][col] = player_id
            self.scores[player_id] += 1

            # Check if game is over (score reached 200)
            if self._is_game_over():
                self.game_over = True
                self.winner, self.win_reason = self._calculate_winner()
                return self._create_claim_result(
                    True, (row, col), True, self.winner, self._get_scores_array(), self.win_reason
                )
            else:
                return self._create_claim_result(True, (row, col))
        else:
            # Cell already claimed
            if self.grid[row][col] == player_id:
                return self._create_claim_result(True, (row, col), message="Already your cell")
            else:
                return self._create_claim_result(False, (row, col), message="Cell claimed by another player")

    def get_grid_bytes(self) -> bytes:
        """Serialize grid to bytes for network transmission."""
        return bytes([self.grid[i][j] for i in range(self.grid_size)
                      for j in range(self.grid_size)])

    def get_positions_bytes(self) -> bytes:
        """Serialize player positions to bytes for network transmission."""
        positions_bytes = bytes([len(self.players)])
        for pid, (row, col) in self.players.items():
            positions_bytes += bytes([pid, row, col])
        return positions_bytes

    def get_events_bytes(self) -> bytes:
        """Serialize active events to bytes for network transmission."""
        active_events = [e for e in self.events.values() if not e.collected]
        events_bytes = bytes([len(active_events)])

        for event in active_events:
            events_bytes += bytes([
                event.event_id,
                event.event_type,
                event.row,
                event.col
            ])

        return events_bytes

    def get_player_events_bytes(self) -> bytes:
        """Serialize player active events to bytes."""
        events_bytes = bytes([len(self.player_events)])

        for player_id, player_event in self.player_events.items():
            # Calculate remaining time in milliseconds
            current_time = time.time()
            remaining_ms = max(0, int((player_event.expiration_time - current_time) * 1000))

            events_bytes += bytes([
                player_id,
                player_event.event_type
            ])
            events_bytes += struct.pack('>I', remaining_ms)  # 4 bytes for remaining time

        return events_bytes

    def get_snapshot_payload(self) -> bytes:
        """Get complete snapshot payload for broadcasting (with events)."""
        return (
                self.get_grid_bytes() +
                self.get_positions_bytes() +
                self.get_events_bytes() +
                self.get_player_events_bytes()
        )

    def update_from_snapshot(self, grid_bytes: bytes, positions_bytes: bytes) -> bool:
        """Update game state from received snapshot (client-side)."""
        # Validate input sizes
        expected_grid_size = self.grid_size * self.grid_size
        if len(grid_bytes) < expected_grid_size:
            return False

        # Update grid (first part of payload)
        grid_bytes_part = grid_bytes[:expected_grid_size]
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                self.grid[i][j] = grid_bytes_part[i * self.grid_size + j]

        # Update player positions
        if len(positions_bytes) < 1:
            return False

        num_players = positions_bytes[0]
        pos_idx = 1
        self.players.clear()

        for _ in range(num_players):
            if pos_idx + 2 >= len(positions_bytes):
                break
            pid = positions_bytes[pos_idx]
            row = positions_bytes[pos_idx + 1]
            col = positions_bytes[pos_idx + 2]
            if 1 <= pid <= 4 and 0 <= row < self.grid_size and 0 <= col < self.grid_size:
                self.players[pid] = (row, col)
            pos_idx += 3

        # Recalculate scores
        self._recalculate_scores()

        # Check game over
        if not self.game_over:
            self.game_over = self._is_game_over()
            if self.game_over:
                self.winner, self.win_reason = self._calculate_winner()

        return True

    def update_events_from_snapshot(self, events_bytes: bytes, player_events_bytes: bytes) -> bool:
        """Update events from snapshot (client-side)."""
        # Parse events on grid
        if len(events_bytes) > 0:
            num_events = events_bytes[0]
            idx = 1
            self.events.clear()

            for _ in range(num_events):
                if idx + 3 >= len(events_bytes):
                    break
                event_id = events_bytes[idx]
                event_type = events_bytes[idx + 1]
                row = events_bytes[idx + 2]
                col = events_bytes[idx + 3]

                if 0 <= row < self.grid_size and 0 <= col < self.grid_size:
                    event = GameEvent(event_id, event_type, row, col, time.time())
                    self.events[event_id] = event

                idx += 4

        # Parse player events
        if len(player_events_bytes) > 0:
            num_player_events = player_events_bytes[0]
            idx = 1
            self.player_events.clear()

            for _ in range(num_player_events):
                if idx + 5 >= len(player_events_bytes):
                    break
                player_id = player_events_bytes[idx]
                event_type = player_events_bytes[idx + 1]
                remaining_ms = struct.unpack('>I', player_events_bytes[idx + 2:idx + 6])[0]

                expiration_time = time.time() + (remaining_ms / 1000.0)
                self.player_events[player_id] = PlayerEvent(event_type, expiration_time)

                idx += 6

        return True

    def get_state(self) -> dict:
        """Get current game state for client rendering."""
        return {
            'grid': [row[:] for row in self.grid],  # Deep copy
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
        """Reset game to initial state."""
        self.grid = [[0 for _ in range(self.grid_size)] for _ in range(self.grid_size)]
        self.players.clear()
        self.scores.clear()
        for i in range(1, 5):
            self.scores[i] = 0
        self.game_over = False
        self.winner = None
        self.win_reason = ""
        self.events.clear()
        self.player_events.clear()
        self.next_event_id = 1
        self.last_event_spawn_time = time.time()

    # Private helper methods
    def _calculate_move(self, row: int, col: int, direction: int) -> Tuple[int, int]:
        """Calculate new position based on direction."""
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
        """Check if game is over: any player reached 200 blocks OR all cells claimed."""
        # Check if any player reached MAX_SCORE_TO_WIN
        for pid, score in self.scores.items():
            if score >= MAX_SCORE_TO_WIN:
                return True

        # Check if all cells are claimed
        for row in self.grid:
            for cell in row:
                if cell == 0:
                    return False
        return True

    def _calculate_winner(self) -> Tuple[int, str]:
        """Calculate winner and reason."""
        # First check if someone reached 200 blocks
        for pid, score in self.scores.items():
            if score >= MAX_SCORE_TO_WIN:
                return pid, f"Reached {MAX_SCORE_TO_WIN} blocks!"

        # Otherwise, winner is player with highest score
        max_score = -1
        winner = 0
        for pid, score in self.scores.items():
            if score > max_score:
                max_score = score
                winner = pid

        return winner, "All cells claimed"

    def _get_scores_array(self) -> List[int]:
        """Get scores as array indexed by player ID."""
        scores_arr = [0] * 5  # Index 0-4
        for pid, score in self.scores.items():
            scores_arr[pid] = score
        return scores_arr

    def _recalculate_scores(self) -> None:
        """Recalculate scores from current grid state."""
        # Reset scores
        for pid in self.scores:
            self.scores[pid] = 0

        # Count claimed cells
        for row in self.grid:
            for cell in row:
                if 1 <= cell <= 4:
                    self.scores[cell] += 1

    def _create_claim_result(self, success: bool, position: Tuple[int, int],
                             game_over: bool = False, winner: int = 0,
                             scores: Optional[List[int]] = None,
                             win_reason: str = "",
                             message: str = "") -> dict:
        """Helper to create standardized claim result dict."""
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

    # Static utility methods
    @staticmethod
    def calculate_position_error(pos1: Tuple[float, float], pos2: Tuple[float, float]) -> float:
        """Calculate Euclidean distance between two positions."""
        return ((pos1[0] - pos2[0]) ** 2 + (pos1[1] - pos2[1]) ** 2) ** 0.5

    @staticmethod
    def interpolate_position(current: Tuple[float, float], target: Tuple[float, float],
                             speed: float, dt: float) -> Tuple[float, float]:
        """Smoothly interpolate from current to target position."""
        curr_row, curr_col = current
        target_row, target_col = target

        # Interpolate row
        if curr_row != target_row:
            step = min(speed * dt, abs(curr_row - target_row))
            curr_row += step * (1 if target_row > curr_row else -1)

        # Interpolate column
        if curr_col != target_col:
            step = min(speed * dt, abs(curr_col - target_col))
            curr_col += step * (1 if target_col > curr_col else -1)

        return (curr_row, curr_col)

    @staticmethod
    def get_color(player_id: int) -> Tuple[int, int, int]:
        """Get RGB color for a player ID."""
        return COLORS.get(player_id, (255, 255, 255))

    @staticmethod
    def get_outline_color(player_id: int) -> Tuple[int, int, int]:
        """Get outline color for a player ID (darker version)."""
        return OUTLINE_COLORS.get(player_id, (150, 150, 150))

    @staticmethod
    def get_event_color(event_type: int) -> Tuple[int, int, int]:
        """Get RGB color for an event type."""
        return EVENT_COLORS.get(event_type, (255, 255, 255))

    @staticmethod
    def should_draw_outline(player_row: int, player_col: int, grid: List[List[int]],
                            player_id: int) -> bool:
        """
        Determine if a player should have an outline drawn.
        Outline shows when player is standing on a claimed cell.
        """
        if not (0 <= player_row < len(grid) and 0 <= player_col < len(grid[0])):
            return False

        cell_owner = grid[player_row][player_col]
        # Draw outline if standing on any claimed cell (including own)
        return cell_owner != 0

    @staticmethod
    def get_event_name(event_type: int) -> str:
        """Get display name for event type."""
        if event_type == EVENT_STAR:
            return "Star"
        return "Unknown"