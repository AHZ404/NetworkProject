# game.py
"""
Consolidated game logic for Grid Clash
Contains all game rules, state management, and serialization
"""
import random
from typing import Dict, Tuple, List, Optional, Set

# Game constants
GRID_SIZE = 20
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
DIRECTIONS = {
    'UP': 0,
    'DOWN': 1,
    'LEFT': 2,
    'RIGHT': 3
}


class GridClashGame:
    """
    Complete game logic for Grid Clash multiplayer game.
    Updated rules:
    1. Players can move through their OWN claimed cells
    2. Players cannot move through OTHER players' claimed cells
    3. Player outline shows when standing on claimed cell
    """

    def __init__(self, grid_size: int = GRID_SIZE, seed: int = 42):
        self.grid_size = grid_size
        self.grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
        self.players: Dict[int, Tuple[int, int]] = {}  # player_id -> (row, col)
        self.scores: Dict[int, int] = {}  # player_id -> score
        self.game_over = False
        self.winner: Optional[int] = None
        random.seed(seed)

        # Initialize scores for all possible players
        for i in range(1, 5):
            self.scores[i] = 0

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
        """
        if player_id not in self.players or self.game_over:
            return False

        row, col = self.players[player_id]
        new_row, new_col = self._calculate_move(row, col, direction)

        # Check if move is valid (not out of bounds)
        if (new_row, new_col) == (row, col):
            return False

        # NEW RULE: Check if target cell is accessible
        target_cell_owner = self.grid[new_row][new_col]

        if target_cell_owner == 0:  # Unclaimed cell - always allowed
            self.players[player_id] = (new_row, new_col)
            return True
        elif target_cell_owner == player_id:  # Player's own claimed cell - allowed
            self.players[player_id] = (new_row, new_col)
            return True
        else:  # Other player's claimed cell - not allowed
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

            # Check if game is over
            if self._is_game_over():
                self.game_over = True
                self.winner = self._calculate_winner()
                return self._create_claim_result(
                    True, (row, col), True, self.winner, self._get_scores_array()
                )
            else:
                return self._create_claim_result(True, (row, col))
        else:
            # Cell already claimed (can be claimed by anyone including owner)
            # NEW: Allow re-claiming of own cells (doesn't change score)
            if self.grid[row][col] == player_id:
                return self._create_claim_result(True, (row, col), message="Already your cell")
            else:
                return self._create_claim_result(False, (row, col), message="Cell claimed by another player")

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
        owner = self.grid[row][col]

        return {
            'is_claimed': owner != 0,
            'owner': owner,
            'is_own_cell': owner == player_id
        }

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

    def get_snapshot_payload(self) -> bytes:
        """Get complete snapshot payload for broadcasting."""
        return self.get_grid_bytes() + self.get_positions_bytes()

    def update_from_snapshot(self, grid_bytes: bytes, positions_bytes: bytes) -> bool:
        """Update game state from received snapshot."""
        # Validate input sizes
        expected_grid_size = self.grid_size * self.grid_size
        if len(grid_bytes) != expected_grid_size:
            return False

        # Update grid
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                self.grid[i][j] = grid_bytes[i * self.grid_size + j]

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
                self.winner = self._calculate_winner()

        return True

    def get_state(self) -> dict:
        """Get current game state for client rendering."""
        return {
            'grid': [row[:] for row in self.grid],  # Deep copy
            'players': self.players.copy(),
            'scores': self.scores.copy(),
            'game_over': self.game_over,
            'winner': self.winner,
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
        """Check if all cells are claimed."""
        for row in self.grid:
            for cell in row:
                if cell == 0:
                    return False
        return True

    def _calculate_winner(self) -> int:
        """Calculate winner based on scores."""
        max_score = -1
        winner = 0
        for pid, score in self.scores.items():
            if score > max_score:
                max_score = score
                winner = pid
        return winner

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