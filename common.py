# common.py
import struct
import zlib
import time

# Protocol constants
PROTO_ID = b'GUDP' #GAMING UDP
VERSION = 2
HEADER_FMT = '>4sBBIIQHI'  # protocol_id (4s), version (B), msg_type (B), snapshot_id (I), seq_num (I), timestamp (Q), payload_len (H), checksum (I)
HEADER_SIZE = struct.calcsize(HEADER_FMT)

MSG_TYPES = {
    'CONNECT': 0,
    'WELCOME': 1,
    'SNAPSHOT': 2,
    'MOVE': 3,
    'CLAIM': 4,
    'ACK': 5,  # For claim success
    'NACK': 6,  # For claim failure
    'GAME_OVER': 7,
    'EVENT_SPAWN': 8,  # Event spawn
    'EVENT_COLLECT': 9,  # Event collected
    'HEARTBEAT': 10,  # New: Connection keep-alive
    'ACK_SNAPSHOT': 11,  # New: Acknowledge snapshot
    'RESEND_REQUEST': 12,  # New: Request missing data
    'COMPRESSED': 13,  # New: Compressed payload flag
    'FULL_SNAPSHOT': 14, # New: Full snapshot for late-joiner/resync
}

# Network settings
UPDATE_HZ = 60
UPDATE_INTERVAL = 1.0 / UPDATE_HZ
HOST = '127.0.0.1'
PORT = 1234
MAX_PACKET_SIZE = 1200

# Visualization
CELL_SIZE = 30

# Event types
EVENT_TYPES = {
    'STAR': 1,  # Can steal enemy blocks by moving over them
}

# Game constants (moved from game.py for accessibility)
GRID_SIZE = 20
MAX_SCORE_TO_WIN = 200
EVENT_STAR = 1
EVENT_DURATION_STAR = 3.0
EVENT_SPAWN_INTERVAL = 3.0
MAX_EVENTS_ON_GRID = 5

# Snapshot history on server (Our k)
MAX_SNAPSHOT_HISTORY = 150 # Store ~5 seconds of history (150 snapshots @ 30Hz)

# Sequence window size for reliable UDP
SEQ_WINDOW_SIZE = 32
MAX_RETRANSMISSIONS = 3


def compute_checksum(payload):
    return zlib.crc32(payload) & 0xffffffff


# common.py - Fix create_header function
def create_header(msg_type, snapshot_id, seq_num, payload_len, checksum):
    """Create protocol header"""
    try:
        timestamp = int(time.time() * 1000)
        # Ensure values are within bounds
        msg_type = min(255, max(0, msg_type))
        snapshot_id = min(0xFFFFFFFF, max(0, snapshot_id))
        seq_num = min(0xFFFFFFFF, max(0, seq_num))
        # Ensure payload length fits in the 16-bit field
        payload_len = min(0xFFFF, max(0, payload_len))

        header = struct.pack(HEADER_FMT, PROTO_ID, VERSION, msg_type,
                             snapshot_id, seq_num, timestamp, payload_len, checksum)
        return header
    except Exception as e:
        print(f"ERROR creating header: {e}")
        print(f"Values: msg_type={msg_type}, snapshot_id={snapshot_id}, seq_num={seq_num}")
        print(f"payload_len={payload_len}, checksum={checksum}")
        raise


def parse_header(data):
    if len(data) < HEADER_SIZE:
        return None
    header = data[:HEADER_SIZE]
    return struct.unpack(HEADER_FMT, header)


class SequenceManager:
    """Manages packet sequencing and loss detection"""

    def __init__(self):
        self.expected_seq = 0
        self.highest_seq = 0
        self.received_bitset = 0  # Bitmask for last 32 packets
        self.lost_packets = set()
        self.sent_packets = {}  # seq_num -> (timestamp, retry_count)
        self.ack_history = {}  # seq_num -> ack_time

    def packet_sent(self, seq_num):
        self.sent_packets[seq_num] = (time.time(), 0)
        if seq_num > self.highest_seq:
            self.highest_seq = seq_num

    def packet_received(self, seq_num):
        """Mark packet as received, returns list of missing sequences"""
        if seq_num > self.expected_seq:
            # Mark all packets between expected and seq_num as potentially lost
            missing = []
            for i in range(self.expected_seq, seq_num):
                if i not in self.lost_packets:
                    self.lost_packets.add(i)
                    missing.append(i)
            self.expected_seq = seq_num + 1
        elif seq_num < self.expected_seq:
            # Out of order delivery
            if seq_num in self.lost_packets:
                self.lost_packets.remove(seq_num)

        # Update bitmask for sliding window
        bit_position = seq_num % SEQ_WINDOW_SIZE
        self.received_bitset |= (1 << bit_position)

        return self.get_missing_packets()

    def get_missing_packets(self):
        """Get list of packets that appear to be lost"""
        missing = []
        current_time = time.time()

        # Check sent packets for timeout
        for seq_num, (sent_time, retry_count) in list(self.sent_packets.items()):
            if current_time - sent_time > UPDATE_INTERVAL * 2 and retry_count < MAX_RETRANSMISSIONS:
                missing.append(seq_num)
                self.sent_packets[seq_num] = (sent_time, retry_count + 1)

        # Add explicitly lost packets
        missing.extend(list(self.lost_packets))
        return list(set(missing))

    def ack_received(self, seq_num):
        """Remove packet from sent tracking when acknowledged"""
        if seq_num in self.sent_packets:
            del self.sent_packets[seq_num]
            self.ack_history[seq_num] = time.time()

    def get_packet_loss_rate(self):
        """Calculate recent packet loss rate"""
        if not self.ack_history:
            return 0.0

        window_start = time.time() - 5.0  # Last 5 seconds
        total_sent = sum(1 for sent_time, _ in self.sent_packets.values()
                         if sent_time > window_start)
        total_acked = sum(1 for ack_time in self.ack_history.values()
                          if ack_time > window_start)

        if total_sent == 0:
            return 0.0
        return max(0.0, 1.0 - (total_acked / total_sent))