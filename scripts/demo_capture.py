#!/usr/bin/env python3
"""Готовит скриншоты AllRAM для README.

На стенде без Windows у контейнера нет файла подкачки, поэтому для наглядных
картинок подставляется профиль типового ноутбука (16 ГиБ ОЗУ, 46 ГиБ с
подкачкой) — в README такие снимки подписаны как демонстрация.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

M = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MB = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
DOCS = ROOT / "docs"

GIB = 1024**3


def simulated_memory():
    from allram import memory

    return memory.MemoryInfo(
        total_bytes=16 * GIB,
        avail_bytes=int(6.2 * GIB),
        commit_total=int(11.4 * GIB),
        commit_limit=46 * GIB,
        commit_avail=int(34.6 * GIB),
        swap_total=30 * GIB,
        swap_free=int(23.2 * GIB),
        source="win32",
    )


def simulated_bench(*args, **kwargs):
    from allram import benchmark

    return benchmark.BenchmarkResult(
        ram_write_mbps=18452.0,
        ram_read_mbps=31570.0,
        ram_latency_ns=71.0,
        disk_write_mbps=1840.0,
        disk_read_mbps=3125.0,
        disk_path="C:\\",
        ram_test_mib=64,
        disk_test_mib=32,
    )


def capture(argv: list[str], simulate_setup: bool = False) -> list[str]:
    import allram.memory as memory
    import allram.benchmark as benchmark
    import allram.cli as cli
    import allram.util as util

    original_info = memory.memory_info
    original_profile = memory.machine_profile
    original_run = benchmark.run
    original_status = cli.report.status_report

    def fake_profile():
        profile = memory.MachineProfile(
            cpu_name="Intel(R) Core(TM) i7-1260P",
            cpu_cores=12,
            ram_bytes=16 * GIB,
        )
        return profile

    def fake_status():
        from allram import imdisk

        return {
            "memory": simulated_memory().as_dict(),
            "cpu": "Intel(R) Core(TM) i7-1260P",
            "windows": "Windows 11 Pro · 23H2 · build 22631.3880",
            "pagefile": {
                "automatic": False,
                "paging_files": [r"D:\pagefile_allram.sys 30720 30720"],
                "existing_files": [r"D:\pagefile_allram.sys"],
                "allram_active": True,
            },
            "physical_memory_registry": 16 * GIB,
            "ramdisk": {"installed": False, "driver_loaded": False, "devices": [], "notes": ["ImDisk не установлен"]},
        }

    original_is_windows = util.is_windows
    original_drive_exists = util.drive_exists
    original_free_space = util.free_space
    memory.memory_info = simulated_memory
    memory.machine_profile = fake_profile
    benchmark.run = simulated_bench
    cli.report.status_report = fake_status
    if simulate_setup:
        util.is_windows = lambda: True
        util.drive_exists = lambda letter: True
        util.free_space = lambda root: 780 * GIB
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            try:
                cli.main(argv)
            except SystemExit:
                pass
    finally:
        memory.memory_info = original_info
        memory.machine_profile = original_profile
        benchmark.run = original_run
        cli.report.status_report = original_status
        util.is_windows = original_is_windows
        util.drive_exists = original_drive_exists
        util.free_space = original_free_space
    return buffer.getvalue().splitlines()


def render(lines: list[str], path: Path, title: str, font_size: int = 15, max_lines: int = 46) -> tuple[int, int]:
    lines = lines[:max_lines]
    font = ImageFont.truetype(M, font_size)
    bold = ImageFont.truetype(MB, font_size)
    line_h = font_size + 6
    pad = 22
    title_h = 38
    width = int(pad * 2 + max(font.getlength(line) for line in lines or [""]) + 4)
    height = pad * 2 + title_h + line_h * len(lines)
    img = Image.new("RGB", (max(width, 860), height), (10, 14, 22))
    draw = ImageDraw.Draw(img)
    w, h = img.size
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=(12, 16, 25), outline=(40, 48, 64))
    draw.rounded_rectangle([0, 0, w - 1, title_h + 10], radius=12, fill=(22, 28, 40))
    draw.rectangle([0, title_h - 2, w - 1, title_h + 10], fill=(22, 28, 40))
    for index, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        draw.ellipse([16 + index * 22, 12, 29 + index * 22, 25], fill=color)
    title_width = font.getlength(title)
    draw.text(((w - title_width) / 2, 10), title, font=font, fill=(160, 172, 196))
    y = title_h + pad
    for line in lines:
        stripped = line.strip()
        color = (206, 216, 232)
        face = font
        if stripped.startswith("AllRAM"):
            color, face = (130, 225, 255), bold
        elif "МиБ/с" in line or "ГиБ" in line or "нс" in line:
            color = (185, 240, 190)
        elif stripped.startswith("!") or "Перезагруз" in stripped or "медленнее" in stripped:
            color = (255, 205, 100)
        elif stripped.startswith(("—", "─")):
            color = (72, 86, 112)
        elif stripped.startswith(("процессор", "система")) or line.startswith("  "):
            color = (140, 156, 180)
        draw.text((pad, y), line, font=face, fill=color)
        y += line_h
    img.save(path, optimize=True)
    return img.size


def main() -> int:
    os.environ.setdefault("ALLRAM_HOME", "/tmp/allram-shot")
    os.environ["ALLRAM_NO_COLOR"] = "1"
    DOCS.mkdir(exist_ok=True)
    print("status", render(capture(["status", "--no-color"]), DOCS / "screenshot.png", "allram status"))
    print("bench", render(capture(["bench", "--no-color"]), DOCS / "bench.png", "allram bench"))
    models = capture(["models", "--model", "8b", "--no-color"])
    print("models", render(models, DOCS / "models.png", "allram models"))
    print(
        "setup",
        render(
            capture(["setup", "-d", "D", "-s", "48G", "--dry-run", "--no-color"], simulate_setup=True),
            DOCS / "setup.png",
            "allram setup --dry-run",
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
