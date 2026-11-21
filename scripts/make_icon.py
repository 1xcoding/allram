#!/usr/bin/env python3
"""Рисует assets/allram.ico без сторонних библиотек.

Иконка: тёмно-синий скруглённый квадрат, планка оперативной памяти
и жёлтая молния «SSD → RAM».
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

def _utf8_console() -> None:
    """Windows-консоль по умолчанию cp1251/cp1252 — переключаем вывод на UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_utf8_console()


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)

BG_TOP = (13, 71, 161)
BG_BOTTOM = (5, 32, 74)
CHIP = (129, 212, 250)
CHIP_SHADOW = (30, 136, 199)
PIN = (227, 242, 255)
BOLT_COLOR = (255, 202, 40)


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _blend(dst: tuple[float, float, float, float], src: tuple[int, int, int], alpha: float):
    sa = alpha
    da = dst[3]
    out_a = sa + da * (1 - sa)
    if out_a <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    r = (src[0] * sa + dst[0] * da * (1 - sa)) / out_a
    g = (src[1] * sa + dst[1] * da * (1 - sa)) / out_a
    b = (src[2] * sa + dst[2] * da * (1 - sa)) / out_a
    return (r, g, b, out_a)


def _rrect_sdf(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> float:
    """Знаковая функция расстояния до скруглённого прямоугольника (<0 внутри)."""
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hx, hy = (x1 - x0) / 2.0 - r, (y1 - y0) / 2.0 - r
    px = abs(x - cx) - hx
    py = abs(y - cy) - hy
    outside = math.hypot(max(px, 0.0), max(py, 0.0))
    inside = min(max(px, py), 0.0)
    return outside + inside - r


def _coverage(sdf: float) -> float:
    return max(0.0, min(1.0, 0.5 - sdf * 256.0))


def _point_in_polygon(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > py) != (yj > py):
            x_intersect = (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi
            if px < x_intersect:
                inside = not inside
        j = i
    return inside


BOLT = [(0.565, 0.285), (0.415, 0.525), (0.500, 0.525), (0.435, 0.715), (0.590, 0.470), (0.505, 0.470)]


def _sample(x: float, y: float):
    """Цвет точки (0..1) и её альфа-покрытие."""
    bg = _rrect_sdf(x, y, 0.05, 0.05, 0.95, 0.95, 0.20)
    if bg > 0.002:
        return None

    # Фон: вертикальный градиент.
    color = [
        _lerp(BG_TOP[0], BG_BOTTOM[0], y),
        _lerp(BG_TOP[1], BG_BOTTOM[1], y),
        _lerp(BG_TOP[2], BG_BOTTOM[2], y),
    ]

    # Ножки планки: 5 сверху и 5 снизу.
    for index in range(5):
        left = 0.175 + index * 0.135
        if left <= x <= left + 0.075 and (0.155 <= y <= 0.255 or 0.745 <= y <= 0.845):
            color = list(PIN)

    # Сама планка памяти.
    bar = _rrect_sdf(x, y, 0.115, 0.255, 0.885, 0.745, 0.055)
    if bar <= 0.0:
        shade = 1.0 - max(0.0, y - 0.5) * 0.35
        color = [int(CHIP[0] * shade), int(CHIP[1] * shade), int(CHIP[2] * shade)]
        inner = _rrect_sdf(x, y, 0.145, 0.285, 0.855, 0.715, 0.035)
        if inner <= 0.0:
            color = list(CHIP_SHADOW)
            for index in range(1, 4):
                divider = 0.145 + index * 0.1775
                if abs(x - divider) <= 0.010:
                    color = [int(c * 0.78) for c in color]

    # Молния.
    if _point_in_polygon(x, y, BOLT):
        color = list(BOLT_COLOR)

    return (tuple(color), _coverage(bg))


def render(size: int, supersample: int = 4) -> bytes:
    """Возвращает RGBA-пиксели изображения size×size."""
    pixels = [[(0.0, 0.0, 0.0, 0.0) for _ in range(size)] for _ in range(size)]
    steps = supersample
    for py in range(size):
        for px in range(size):
            acc = (0.0, 0.0, 0.0, 0.0)
            for sy in range(steps):
                for sx in range(steps):
                    x = (px + (sx + 0.5) / steps) / size
                    y = (py + (sy + 0.5) / steps) / size
                    sample = _sample(x, y)
                    if sample is None:
                        continue
                    color, alpha = sample
                    acc = _blend(acc, color, alpha)
            pixels[py][px] = acc

    raw = bytearray()
    for py in range(size):
        raw.append(0)  # фильтр None
        for px in range(size):
            r, g, b, a = pixels[py][px]
            raw += bytes(
                (
                    max(0, min(255, int(r))),
                    max(0, min(255, int(g))),
                    max(0, min(255, int(b))),
                    max(0, min(255, int(a * 255))),
                )
            )
    return bytes(raw)


def png_bytes(size: int) -> bytes:
    raw = render(size)
    header = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def build_ico(path: Path, sizes=SIZES) -> Path:
    images = [(size, png_bytes(size)) for size in sizes]
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    entries = bytearray()
    offset = 6 + 16 * count
    data = bytearray()
    for size, payload in images:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            width,
            height,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        )
        data += payload
        offset += len(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(entries) + bytes(data))
    return path


def main() -> int:
    path = build_ico(ASSETS / "allram.ico")
    print(f"Иконка собрана: {path} ({path.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
