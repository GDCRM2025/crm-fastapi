from __future__ import annotations

import io
from typing import List

from PIL import Image  # Pillow (ya está en requirements)


class _QrCode:
    """
    QR Code generator (byte mode) basado en el algoritmo estándar.

    Implementación compacta derivada del enfoque clásico de Nayuki/qrcodegen
    (misma idea/estructura), adaptada para este proyecto para generar QR
    de forma 100% local (sin llamadas externas).
    """

    # ECC levels
    _ECC_L = 0
    _ECC_M = 1
    _ECC_Q = 2
    _ECC_H = 3

    _ECC_FORMAT_BITS = [1, 0, 3, 2]

    def __init__(self, version: int, ecl: int, data_codewords: List[int], mask: int):
        self.version = version
        self.size = version * 4 + 17
        self.ecl = ecl
        self.mask = mask
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.is_function = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        all_codewords = self._add_ecc_and_interleave(data_codewords)
        self._draw_codewords(all_codewords)
        self._apply_mask(mask)
        self._draw_format_bits(mask)

    @staticmethod
    def encode_bytes(data: bytes, ecl: int) -> "_QrCode":
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)

        # Choose minimal version that fits, up to version 10 (suficiente para URLs).
        for ver in range(1, 11):
            cap = _QrCode._get_num_data_codewords(ver, ecl)
            bits = _BitBuffer()
            # Mode: byte (0100)
            bits.append(0x4, 4)
            # Char count
            ccbits = 8 if ver <= 9 else 16
            bits.append(len(data), ccbits)
            for b in data:
                bits.append(b, 8)
            # Terminator
            bits.append(0, min(4, cap * 8 - bits.length))
            # Pad to byte
            while bits.length % 8 != 0:
                bits.append(0, 1)
            # Pad codewords
            pad_bytes = [0xEC, 0x11]
            i = 0
            while bits.length // 8 < cap:
                bits.append(pad_bytes[i & 1], 8)
                i += 1
            data_cw = bits.to_codewords()
            if len(data_cw) == cap:
                return _QrCode._encode_with_best_mask(ver, ecl, data_cw)

        raise ValueError("Datos demasiado grandes para QR local (v<=10).")

    @staticmethod
    def _encode_with_best_mask(version: int, ecl: int, data_cw: List[int]) -> "_QrCode":
        best = None
        best_pen = 10**9
        for mask in range(8):
            qr = _QrCode(version, ecl, data_cw, mask)
            pen = qr._get_penalty_score()
            if pen < best_pen:
                best_pen = pen
                best = qr
        assert best is not None
        return best

    @staticmethod
    def _get_num_data_codewords(version: int, ecl: int) -> int:
        # Tabla parcial (v1..10) para Byte mode. (Total data codewords por ECC level)
        # Fuente: especificación QR / tablas estándar.
        table = {
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
        return table[version][ecl]

    @staticmethod
    def _get_num_ecc_codewords_per_block(version: int, ecl: int) -> int:
        # v1..10 (ECC per block). Tabla estándar.
        table = {
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
        return table[version][ecl]

    @staticmethod
    def _get_num_blocks(version: int, ecl: int) -> int:
        # v1..10 blocks count (total blocks). Tabla estándar.
        table = {
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
        return table[version][ecl]

    def _draw_function_patterns(self) -> None:
        size = self.size
        # Finder patterns
        for (x, y) in [(0, 0), (size - 7, 0), (0, size - 7)]:
            self._draw_finder(x, y)
        # Separators
        for i in range(8):
            self._set_function_module(7, i, False)
            self._set_function_module(i, 7, False)
            self._set_function_module(size - 8, i, False)
            self._set_function_module(size - 8 + i - 7, 7, False)
            self._set_function_module(i, size - 8, False)
            self._set_function_module(7, size - 8 + i - 7, False)
        # Timing patterns
        for i in range(8, size - 8):
            self._set_function_module(i, 6, i % 2 == 0)
            self._set_function_module(6, i, i % 2 == 0)
        # Dark module
        self._set_function_module(8, size - 8, True)

        # Alignment patterns
        pos = self._alignment_pattern_positions()
        for i in range(len(pos)):
            for j in range(len(pos)):
                if (i, j) in [(0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)]:
                    continue
                self._draw_alignment(pos[i], pos[j])

    def _alignment_pattern_positions(self) -> List[int]:
        if self.version == 1:
            return []
        # Simple formula for v<=10
        num = self.version // 7 + 2
        step = 0 if num == 2 else ((self.version * 4 + num * 2 + 1) // (2 * num - 2) * 2)
        res = [6]
        for i in range(num - 2):
            res.append(self.size - 7 - i * step)
        res.append(self.size - 7)
        return sorted(set(res))

    def _draw_finder(self, x: int, y: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx = x + dx
                yy = y + dy
                if 0 <= xx < self.size and 0 <= yy < self.size:
                    dist = max(abs(dx), abs(dy))
                    self._set_function_module(xx, yy, dist in (0, 1, 2, 6))

    def _draw_alignment(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function_module(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def _set_function_module(self, x: int, y: int, val: bool) -> None:
        self.modules[y][x] = val
        self.is_function[y][x] = True

    def _add_ecc_and_interleave(self, data: List[int]) -> List[int]:
        ver = self.version
        ecl = self.ecl
        num_blocks = self._get_num_blocks(ver, ecl)
        ecc_len = self._get_num_ecc_codewords_per_block(ver, ecl)
        total_data = self._get_num_data_codewords(ver, ecl)

        # Split into blocks (simple even split; ok for v<=10 with our tables)
        block_sizes = [total_data // num_blocks] * num_blocks
        for i in range(total_data % num_blocks):
            block_sizes[i] += 1

        blocks = []
        k = 0
        for bs in block_sizes:
            blk = data[k : k + bs]
            k += bs
            ecc = _ReedSolomon.compute_remainder(blk, _ReedSolomon.generator_poly(ecc_len))
            blocks.append((blk, ecc))

        # Interleave
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
        direction_up = True
        x = self.size - 1
        y = self.size - 1
        while x > 0:
            if x == 6:
                x -= 1
            for _ in range(self.size):
                for dx in [0, -1]:
                    xx = x + dx
                    if not self.is_function[y][xx]:
                        val = ((codewords[i] >> bit) & 1) != 0
                        self.modules[y][xx] = val
                        bit -= 1
                        if bit < 0:
                            i += 1
                            bit = 7
                            if i >= len(codewords):
                                return
                y += -1 if direction_up else 1
                if y < 0 or y >= self.size:
                    y += 1 if direction_up else -1
                    direction_up = not direction_up
                    break
            x -= 2

    def _apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.is_function[y][x]:
                    continue
                invert = False
                if mask == 0:
                    invert = (x + y) % 2 == 0
                elif mask == 1:
                    invert = y % 2 == 0
                elif mask == 2:
                    invert = x % 3 == 0
                elif mask == 3:
                    invert = (x + y) % 3 == 0
                elif mask == 4:
                    invert = ((x // 3) + (y // 2)) % 2 == 0
                elif mask == 5:
                    invert = (x * y) % 2 + (x * y) % 3 == 0
                elif mask == 6:
                    invert = ((x * y) % 2 + (x * y) % 3) % 2 == 0
                elif mask == 7:
                    invert = ((x + y) % 2 + (x * y) % 3) % 2 == 0
                if invert:
                    self.modules[y][x] = not self.modules[y][x]

    def _draw_format_bits(self, mask: int) -> None:
        # Format bits BCH(15,5)
        data = (self._ECC_FORMAT_BITS[self.ecl] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ (0x537 if (rem & (1 << 9)) else 0)
        bits = ((data << 10) | rem) ^ 0x5412
        # Draw
        for i in range(15):
            bit = ((bits >> i) & 1) != 0
            a = [
                (8, i) if i < 6 else (8, i + 1) if i < 8 else (8, self.size - 15 + i),
                (self.size - 1 - i, 8) if i < 8 else (15 - i - 1, 8),
            ]
            self._set_function_module(a[0][0], a[0][1], bit)
            self._set_function_module(a[1][0], a[1][1], bit)

    def _get_penalty_score(self) -> int:
        # Penalty rules (approx; enough for mask selection)
        m = self.modules
        size = self.size
        pen = 0
        # Adjacent in rows/cols
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
        # 2x2 blocks
        for y in range(size - 1):
            for x in range(size - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    pen += 3
        return pen


class _BitBuffer:
    def __init__(self):
        self.data = []
        self.length = 0

    def append(self, val: int, n: int) -> None:
        if n < 0 or val >> n != 0:
            raise ValueError("append")
        for i in reversed(range(n)):
            self.data.append(((val >> i) & 1) != 0)
        self.length += n

    def to_codewords(self) -> List[int]:
        res = []
        acc = 0
        for i, b in enumerate(self.data):
            acc = (acc << 1) | (1 if b else 0)
            if (i & 7) == 7:
                res.append(acc)
                acc = 0
        return res


class _ReedSolomon:
    # GF(2^8) with primitive polynomial 0x11D
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
    def generator_poly(cls, degree: int) -> List[int]:
        cls._init()
        g = [1]
        for i in range(degree):
            g = cls._poly_mul(g, [1, cls._EXP[i]])
        return g

    @classmethod
    def compute_remainder(cls, data: List[int], gen: List[int]) -> List[int]:
        cls._init()
        res = [0] * (len(gen) - 1)
        for b in data:
            factor = b ^ res[0]
            res = res[1:] + [0]
            if factor != 0:
                for i in range(len(res)):
                    res[i] ^= cls._gf_mul(gen[i + 1], factor)
        return res

    @classmethod
    def _gf_mul(cls, x: int, y: int) -> int:
        if x == 0 or y == 0:
            return 0
        return cls._EXP[cls._LOG[x] + cls._LOG[y]]

    @classmethod
    def _poly_mul(cls, p: List[int], q: List[int]) -> List[int]:
        res = [0] * (len(p) + len(q) - 1)
        for i, a in enumerate(p):
            for j, b in enumerate(q):
                res[i + j] ^= cls._gf_mul(a, b)
        return res


def make_qr_png(data: str, *, box: int = 6, border: int = 4) -> bytes:
    qr = _QrCode.encode_bytes(data.encode("utf-8"), _QrCode._ECC_M)
    size = qr.size
    dim = (size + border * 2) * box
    img = Image.new("RGB", (dim, dim), "white")
    px = img.load()
    for y in range(size):
        for x in range(size):
            if qr.modules[y][x]:
                xx0 = (x + border) * box
                yy0 = (y + border) * box
                for yy in range(yy0, yy0 + box):
                    for xx in range(xx0, xx0 + box):
                        px[xx, yy] = (0, 0, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

