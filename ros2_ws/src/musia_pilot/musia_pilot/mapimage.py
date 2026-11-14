"""Carte d'occupation → image PNG en niveaux de gris, et angles.

La page reçoit la carte en PNG (quelques ko) plutôt qu'en JSON par rosbridge (un nombre
par case : plusieurs centaines de ko toutes les quelques secondes sur le Wi-Fi).
"""

import math
import struct
import zlib

FREE, OCCUPIED, UNKNOWN = 254, 0, 205


def _gray(cell: int) -> int:
    if cell < 0 or cell > 100:
        return UNKNOWN
    return round(FREE - cell * (FREE - OCCUPIED) / 100)


# octet de la grille (int8 lu comme uint8) → niveau de gris
_TABLE = bytes(_gray(b if b < 128 else b - 256) for b in range(256))


def occupancy_to_rows(width: int, height: int, data: bytes) -> bytes:
    """Grille ROS (ligne 0 = y minimal) → pixels ligne par ligne, du haut (y maximal) au bas."""
    if len(data) != width * height:
        raise ValueError(f'la grille fait {len(data)} cases, attendu {width}×{height}')
    gray = data.translate(_TABLE)
    return b''.join(gray[r * width:(r + 1) * width] for r in range(height - 1, -1, -1))


def png_gray(width: int, height: int, pixels: bytes) -> bytes:
    """Encode des pixels 8 bits (ligne par ligne, du haut vers le bas) en PNG."""
    if width <= 0 or height <= 0 or len(pixels) != width * height:
        raise ValueError('dimensions incohérentes')
    raw = b''.join(b'\x00' + pixels[r * width:(r + 1) * width] for r in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))

    header = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)  # 8 bits, niveaux de gris
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def occupancy_png(width: int, height: int, data: bytes) -> bytes:
    return png_gray(width, height, occupancy_to_rows(width, height, data))


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def quaternion_from_yaw(yaw: float):
    """(x, y, z, w) d'une rotation autour de z."""
    return 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)
