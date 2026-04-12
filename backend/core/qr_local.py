from __future__ import annotations

import io
from dataclasses import dataclass
import struct
import zlib
from typing import List


# Minimal, robust QR generator based on the well-known "qrcodegen" approach (byte mode).
# Generates a boolean module matrix, then renders either PNG (if Pillow available) or SVG.


@dataclass(frozen=True)
class QrMatrix:
    size: int
    modules: List[List[bool]]  # [y][x]


def make_qr_matrix(data: str) -> QrMatrix:
    b = (data or "").encode("utf-8")
    # Usamos ECC LOW para mantener el QR menos denso (mejor lectura en cámara),
    # especialmente en URLs largas o al escanear desde pantalla.
    qr = _QrCode.encode_bytes(b, _QrCode.Ecc.LOW)
    return QrMatrix(size=qr.get_size(), modules=[[qr.get_module(x, y) for x in range(qr.get_size())] for y in range(qr.get_size())])


def make_qr_svg(data: str, *, scale: int = 6, border: int = 4) -> bytes:
    m = make_qr_matrix(data)
    size = m.size
    dim = (size + border * 2) * scale
    # rectangles for black modules
    rects = []
    for y in range(size):
        row = m.modules[y]
        for x in range(size):
            if row[x]:
                xx = (x + border) * scale
                yy = (y + border) * scale
                rects.append(f'<rect x="{xx}" y="{yy}" width="{scale}" height="{scale}"/>')
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" viewBox="0 0 {dim} {dim}">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<g fill="black">'
        + "".join(rects)
        + "</g></svg>"
    )
    return svg.encode("utf-8")


def make_qr_png(data: str, *, scale: int = 6, border: int = 4) -> bytes:
    # Render via Pillow if available; else pure-Python PNG (no external deps).
    try:
        from PIL import Image  # type: ignore
    except Exception:
        Image = None  # type: ignore[assignment]

    m = make_qr_matrix(data)
    size = m.size
    dim = (size + border * 2) * scale
    if Image is not None:
        img = Image.new("RGB", (dim, dim), "white")
        px = img.load()
        for y in range(size):
            row = m.modules[y]
            for x in range(size):
                if row[x]:
                    xx0 = (x + border) * scale
                    yy0 = (y + border) * scale
                    for yy in range(yy0, yy0 + scale):
                        for xx in range(xx0, xx0 + scale):
                            px[xx, yy] = (0, 0, 0)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # Pure-Python grayscale PNG (color type 0).
    # Build a full pixel grid (dim x dim) with sharp edges (no antialias).
    white = 255
    black = 0
    # Initialize as white rows.
    rows = [bytearray([white]) * dim for _ in range(dim)]
    for y in range(size):
        row = m.modules[y]
        for x in range(size):
            if not row[x]:
                continue
            xx0 = (x + border) * scale
            yy0 = (y + border) * scale
            for yy in range(yy0, yy0 + scale):
                r = rows[yy]
                r[xx0 : xx0 + scale] = bytes([black]) * scale

    # PNG scanlines: filter byte (0) + pixel bytes
    raw = bytearray()
    for y in range(dim):
        raw.append(0)
        raw.extend(rows[y])
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, data_b: bytes) -> bytes:
        ln = struct.pack(">I", len(data_b))
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data_b, crc) & 0xFFFFFFFF
        return ln + tag + data_b + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", dim, dim, 8, 0, 0, 0, 0)  # 8-bit, grayscale
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", comp),
            chunk(b"IEND", b""),
        ]
    )


def make_qr_png_target(data: str, *, target_px: int = 360, border: int = 6) -> bytes:
    """
    Genera PNG con un tamaño objetivo (px) ajustando el scale automáticamente.
    """
    m = make_qr_matrix(data)
    n = (m.size + border * 2)
    scale = max(3, min(12, int(target_px // max(1, n))))
    return make_qr_png(data, scale=scale, border=border)


# -------------------------------
# Internal QR implementation
# -------------------------------


class _QrCode:
    # Error correction levels.
    class Ecc:
        LOW = 0
        MEDIUM = 1
        QUARTILE = 2
        HIGH = 3

    _ECC_FORMAT_BITS = [1, 0, 3, 2]

    def __init__(self, version: int, ecl: int, data_codewords: List[int], mask: int):
        self._version = version
        self._size = version * 4 + 17
        self._ecl = ecl
        self._mask = mask
        self._modules = [[False] * self._size for _ in range(self._size)]
        self._is_function = [[False] * self._size for _ in range(self._size)]
        self._draw_function_patterns()
        all_codewords = self._add_ecc_and_interleave(data_codewords)
        self._draw_codewords(all_codewords)
        self._apply_mask(mask)
        self._draw_format_bits(mask)

    def get_size(self) -> int:
        return self._size

    def get_module(self, x: int, y: int) -> bool:
        return self._modules[y][x]

    @staticmethod
    def encode_bytes(data: bytes, ecl: int) -> "_QrCode":
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        # Versions 1..10: enough for our mark URLs.
        for ver in range(1, 11):
            cap = _QrCode._NUM_DATA_CODEWORDS[ver][ecl]
            bb = _BitBuffer()
            bb.append(0x4, 4)  # byte mode
            ccbits = 8 if ver <= 9 else 16
            bb.append(len(data), ccbits)
            for b in data:
                bb.append(b, 8)
            rem = cap * 8 - bb.length
            if rem > 0:
                bb.append(0, min(4, rem))
            while bb.length % 8 != 0:
                bb.append(0, 1)
            pads = [0xEC, 0x11]
            i = 0
            while bb.length // 8 < cap:
                bb.append(pads[i & 1], 8)
                i += 1
            codewords = bb.to_codewords()
            if len(codewords) != cap:
                continue
            return _QrCode._encode_with_best_mask(ver, ecl, codewords)
        raise ValueError("Datos demasiado grandes para QR (v<=10).")

    @staticmethod
    def _encode_with_best_mask(version: int, ecl: int, data_cw: List[int]) -> "_QrCode":
        best = None
        best_pen = 1 << 30
        for mask in range(8):
            qr = _QrCode(version, ecl, data_cw, mask)
            pen = qr._penalty()
            if pen < best_pen:
                best_pen = pen
                best = qr
        assert best is not None
        return best

    def _set_function(self, x: int, y: int, val: bool) -> None:
        self._modules[y][x] = val
        self._is_function[y][x] = True

    def _draw_function_patterns(self) -> None:
        size = self._size
        for (x, y) in [(0, 0), (size - 7, 0), (0, size - 7)]:
            self._draw_finder(x, y)
        # timing patterns
        for i in range(8, size - 8):
            self._set_function(i, 6, i % 2 == 0)
            self._set_function(6, i, i % 2 == 0)
        # dark module
        self._set_function(8, size - 8, True)
        # alignment
        pos = self._alignment_positions()
        for i in range(len(pos)):
            for j in range(len(pos)):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                self._draw_alignment(pos[i], pos[j])

    def _draw_finder(self, x: int, y: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx = x + dx
                yy = y + dy
                if 0 <= xx < self._size and 0 <= yy < self._size:
                    dist = max(abs(dx), abs(dy))
                    self._set_function(xx, yy, dist in (0, 1, 2, 6))

    def _draw_alignment(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def _alignment_positions(self) -> List[int]:
        if self._version == 1:
            return []
        num = self._version // 7 + 2
        step = 0 if num == 2 else ((self._version * 4 + num * 2 + 1) // (2 * num - 2) * 2)
        res = [6]
        for i in range(num - 2):
            res.append(self._size - 7 - i * step)
        res.append(self._size - 7)
        return sorted(set(res))

    def _add_ecc_and_interleave(self, data: List[int]) -> List[int]:
        ver = self._version
        ecl = self._ecl
        num_blocks = self._NUM_BLOCKS[ver][ecl]
        ecc_len = self._ECC_PER_BLOCK[ver][ecl]
        total_data = self._NUM_DATA_CODEWORDS[ver][ecl]
        # split blocks
        base = total_data // num_blocks
        rem = total_data % num_blocks
        blocks = []
        k = 0
        for i in range(num_blocks):
            bs = base + (1 if i < rem else 0)
            blk = data[k : k + bs]
            k += bs
            ecc = _ReedSolomon.compute(blk, ecc_len)
            blocks.append((blk, ecc))
        res = []
        max_data = max(len(b[0]) for b in blocks)
        max_ecc = max(len(b[1]) for b in blocks)
        for i in range(max_data):
            for blk, _ in blocks:
                if i < len(blk):
                    res.append(blk[i])
        for i in range(max_ecc):
            for _, ecc in blocks:
                if i < len(ecc):
                    res.append(ecc[i])
        return res

    def _draw_codewords(self, codewords: List[int]) -> None:
        i = 0
        bit = 7
        x = self._size - 1
        y = self._size - 1
        up = True
        while x > 0:
            if x == 6:
                x -= 1
            for _ in range(self._size):
                for dx in [0, -1]:
                    xx = x + dx
                    if not self._is_function[y][xx]:
                        self._modules[y][xx] = ((codewords[i] >> bit) & 1) != 0
                        bit -= 1
                        if bit < 0:
                            i += 1
                            bit = 7
                            if i >= len(codewords):
                                return
                y += -1 if up else 1
                if y < 0 or y >= self._size:
                    y += 1 if up else -1
                    up = not up
                    break
            x -= 2

    def _apply_mask(self, mask: int) -> None:
        for y in range(self._size):
            for x in range(self._size):
                if self._is_function[y][x]:
                    continue
                inv = False
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = ((x // 3) + (y // 2)) % 2 == 0
                elif mask == 5:
                    inv = (x * y) % 2 + (x * y) % 3 == 0
                elif mask == 6:
                    inv = ((x * y) % 2 + (x * y) % 3) % 2 == 0
                elif mask == 7:
                    inv = ((x + y) % 2 + (x * y) % 3) % 2 == 0
                if inv:
                    self._modules[y][x] = not self._modules[y][x]

    def _draw_format_bits(self, mask: int) -> None:
        data = (self._ECC_FORMAT_BITS[self._ecl] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ (0x537 if (rem & (1 << 9)) else 0)
        bits = ((data << 10) | rem) ^ 0x5412
        for i in range(15):
            bit = ((bits >> i) & 1) != 0
            a = (8, i) if i < 6 else (8, i + 1) if i < 8 else (8, self._size - 15 + i)
            b = (self._size - 1 - i, 8) if i < 8 else (15 - i - 1, 8)
            self._set_function(a[0], a[1], bit)
            self._set_function(b[0], b[1], bit)

    def _penalty(self) -> int:
        m = self._modules
        size = self._size
        pen = 0
        # runs
        for y in range(size):
            run = 1
            for x in range(1, size):
                if m[y][x] == m[y][x - 1]:
                    run += 1
                    if run == 5:
                        pen += 3
                    elif run > 5:
                        pen += 1
                else:
                    run = 1
        for x in range(size):
            run = 1
            for y in range(1, size):
                if m[y][x] == m[y - 1][x]:
                    run += 1
                    if run == 5:
                        pen += 3
                    elif run > 5:
                        pen += 1
                else:
                    run = 1
        # 2x2
        for y in range(size - 1):
            for x in range(size - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    pen += 3
        return pen

    # Tables for v1..10
    _NUM_DATA_CODEWORDS = {
        1: [19, 16, 13, 9],
        2: [34, 28, 22, 16],
        3: [55, 44, 34, 26],
        4: [80, 64, 48, 36],
        5: [108, 86, 62, 46],
        6: [136, 108, 76, 60],
        7: [156, 124, 88, 66],
        8: [194, 154, 110, 86],
        9: [232, 182, 132, 100],
        10: [274, 216, 154, 122],
    }
    _ECC_PER_BLOCK = {
        1: [7, 10, 13, 17],
        2: [10, 16, 22, 28],
        3: [15, 26, 18, 22],
        4: [20, 18, 26, 16],
        5: [26, 24, 18, 22],
        6: [18, 16, 24, 28],
        7: [20, 18, 18, 26],
        8: [24, 22, 22, 26],
        9: [30, 22, 20, 24],
        10: [18, 26, 24, 28],
    }
    _NUM_BLOCKS = {
        1: [1, 1, 1, 1],
        2: [1, 1, 1, 1],
        3: [1, 1, 2, 2],
        4: [1, 2, 2, 4],
        5: [1, 2, 4, 4],
        6: [2, 4, 4, 4],
        7: [2, 4, 6, 5],
        8: [2, 4, 6, 6],
        9: [2, 5, 8, 8],
        10: [4, 5, 8, 8],
    }


class _BitBuffer:
    def __init__(self):
        self._bits: List[bool] = []
        self.length = 0

    def append(self, val: int, n: int) -> None:
        if n < 0 or val >> n != 0:
            raise ValueError("bit append")
        for i in reversed(range(n)):
            self._bits.append(((val >> i) & 1) != 0)
        self.length += n

    def to_codewords(self) -> List[int]:
        res: List[int] = []
        acc = 0
        for i, b in enumerate(self._bits):
            acc = (acc << 1) | (1 if b else 0)
            if (i & 7) == 7:
                res.append(acc)
                acc = 0
        return res


class _ReedSolomon:
    _EXP = [0] * 512
    _LOG = [0] * 256
    _INIT = False

    @classmethod
    def _init(cls) -> None:
        if cls._INIT:
            return
        x = 1
        for i in range(255):
            cls._EXP[i] = x
            cls._LOG[x] = i
            x <<= 1
            if x & 0x100:
                x ^= 0x11D
        for i in range(255, 512):
            cls._EXP[i] = cls._EXP[i - 255]
        cls._INIT = True

    @classmethod
    def _mul(cls, x: int, y: int) -> int:
        if x == 0 or y == 0:
            return 0
        return cls._EXP[cls._LOG[x] + cls._LOG[y]]

    @classmethod
    def compute(cls, data: List[int], ecc_len: int) -> List[int]:
        cls._init()
        gen = cls._generator(ecc_len)
        res = [0] * ecc_len
        for b in data:
            factor = b ^ res[0]
            res = res[1:] + [0]
            if factor != 0:
                for i in range(ecc_len):
                    res[i] ^= cls._mul(gen[i], factor)
        return res

    @classmethod
    def _generator(cls, degree: int) -> List[int]:
        cls._init()
        g = [1]
        for i in range(degree):
            g2 = [0] * (len(g) + 1)
            for j in range(len(g)):
                g2[j] ^= g[j]
                g2[j + 1] ^= cls._mul(g[j], cls._EXP[i])
            g = g2
        # drop leading 1, match compute() indexing
        return g[1:]
