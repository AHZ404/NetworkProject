# common.py
import struct
import zlib

# Protocol constants
PROTO_ID = b'MPGP'  # Multiplayer Game Protocol
VERSION = 1
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
    'EVENT_SPAWN': 8,  # New: Event spawn
    'EVENT_COLLECT': 9,  # New: Event collected
}

# Network settings
UPDATE_HZ = 20
UPDATE_INTERVAL = 1.0 / UPDATE_HZ
HOST = '127.0.0.1'
PORT = 1234

# Visualization
CELL_SIZE = 30  # For visualization

# Event types (only STAR now)
EVENT_TYPES = {
    'STAR': 1,      # Can steal enemy blocks by moving over them
}

def compute_checksum(payload):
    return zlib.crc32(payload) & 0xffffffff