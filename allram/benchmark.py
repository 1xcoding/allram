"""Замер скоростей: оперативная память против SSD и оценка «нейросетей».

Всё измеряется стандартными средствами Python, без прав администратора.
Результаты приблизительные — они нужны, чтобы наглядно показать разницу
между настоящей ОЗУ и памятью, расширенной файлом подкачки.
"""

from __future__ import annotations

import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable

from . import util


@dataclass
class BenchmarkResult:
    ram_write_mbps: float = 0.0
    ram_read_mbps: float = 0.0
    ram_latency_ns: float = 0.0
    disk_write_mbps: float = 0.0
    disk_read_mbps: float = 0.0
    disk_path: str = ""
    ram_test_mib: int = 0
    disk_test_mib: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def disk_used(self) -> bool:
        return self.disk_write_mbps > 0 or self.disk_read_mbps > 0

    @property
    def slowdown(self) -> float:
        """Во сколько раз последовательное чтение из ОЗУ быстрее чтения с диска."""
        if not self.disk_used:
            return 0.0
        ram = max(self.ram_read_mbps, self.ram_write_mbps)
        disk = max(self.disk_read_mbps, self.disk_write_mbps, 1.0)
        if ram <= 0:
            return 0.0
        return ram / disk

    def as_dict(self) -> dict[str, float | str | list[str]]:
        return {
            "ram_write_mbps": round(self.ram_write_mbps, 1),
            "ram_read_mbps": round(self.ram_read_mbps, 1),
            "ram_latency_ns": round(self.ram_latency_ns, 1),
            "disk_write_mbps": round(self.disk_write_mbps, 1),
            "disk_read_mbps": round(self.disk_read_mbps, 1),
            "disk_path": self.disk_path,
            "slowdown": round(self.slowdown, 1),
            "notes": self.notes,
        }


def _timed(operation: Callable[[], None]) -> float:
    start = time.perf_counter()
    operation()
    return time.perf_counter() - start


def bench_memory(size_mib: int = 64) -> tuple[float, float, float]:
    """Возвращает (запись МиБ/с, чтение МиБ/с, задержка доступа нс).

    Чтение и запись меряются встроенными сишными операциями (memcpy, crc32),
    чтобы Python-оверхед не искажал результат.
    """
    import zlib

    size = max(8, size_mib) * util.MIB
    source = bytearray(size)
    target = bytearray(size)
    payload = b"\xaa" * size

    def write_pass() -> None:
        source[:] = payload

    def copy_pass() -> None:
        target[:] = source

    def read_pass() -> int:
        checksum = 0
        for offset in range(0, size, util.MIB):
            checksum ^= zlib.crc32(source[offset : offset + util.MIB])
        return checksum

    write_time = _timed(write_pass)
    copy_time = _timed(copy_pass)
    read_time = _timed(read_pass)

    mib = size / util.MIB
    write_mbps = mib / max(write_time, 1e-9)
    read_mbps = mib / max(read_time, 1e-9)
    copy_mbps = 2 * mib / max(copy_time, 1e-9)  # копирование = чтение + запись
    read_mbps = max(read_mbps, write_mbps, copy_mbps * 0.5)

    # Оценка задержки случайного доступа (включает накладные расходы Python).
    rng = random.Random(42)
    indexes = [rng.randrange(0, size - 64, 64) for _ in range(20_000)]
    view = memoryview(source)
    start = time.perf_counter()
    total = 0
    for index in indexes:
        total += view[index]
    elapsed = time.perf_counter() - start
    latency_ns = (elapsed / len(indexes)) * 1e9

    if total < 0:  # pragma: no cover - невозможно, защита от оптимизатора
        print(total)
    return write_mbps, read_mbps, latency_ns


def bench_disk(path: str, size_mib: int = 32) -> tuple[float, float, str]:
    """Возвращает (запись МБ/с, чтение МБ/с, путь тестового файла)."""
    directory = path if os.path.isdir(path) else os.path.dirname(path) or "."
    size = max(4, min(size_mib, 256)) * util.MIB
    buffer = os.urandom(util.MIB)

    handle = tempfile.NamedTemporaryFile(
        prefix="allram-bench-", suffix=".bin", dir=directory, delete=False
    )
    file_path = handle.name
    try:
        start = time.perf_counter()
        with handle:
            for _ in range(size // util.MIB):
                handle.write(buffer)
            handle.flush()
            os.fsync(handle.fileno())
        write_time = time.perf_counter() - start

        start = time.perf_counter()
        with open(file_path, "rb", buffering=util.MIB) as reader:
            read_total = 0
            while True:
                data = reader.read(util.MIB * 4)
                if not data:
                    break
                read_total += len(data)
        read_time = time.perf_counter() - start
    finally:
        try:
            os.unlink(file_path)
        except OSError:
            file_path = ""

    mib = size / util.MIB
    return mib / max(write_time, 1e-9), mib / max(read_time, 1e-9), directory


def run(drive: str | None = None, *, ram_mib: int = 64, disk_mib: int = 32) -> BenchmarkResult:
    result = BenchmarkResult(ram_test_mib=ram_mib, disk_test_mib=disk_mib)
    try:
        result.ram_write_mbps, result.ram_read_mbps, result.ram_latency_ns = bench_memory(ram_mib)
    except Exception as exc:  # noqa: BLE001 - тест не должен ломать программу
        result.notes.append(f"не удалось замерить скорость памяти: {exc}")

    if drive:
        root = f"{util.normalize_drive_letter(drive)}:\\" if util.is_windows() else drive
    elif util.is_windows():
        root = util.drive_root(util.data_dir())
    else:
        root = tempfile.gettempdir()

    try:
        result.disk_write_mbps, result.disk_read_mbps, result.disk_path = bench_disk(root, disk_mib)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"не удалось замерить скорость диска: {exc}")

    return result


@dataclass
class ModelEstimate:
    model_bytes: int
    fits_in_ram: bool
    ram_bytes: int
    commit_bytes: int
    ram_load_seconds: float
    hybrid_load_seconds: float
    spill_bytes: int
    tokens_per_second_hint: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "model_bytes": self.model_bytes,
            "fits_in_ram": self.fits_in_ram,
            "ram_bytes": self.ram_bytes,
            "commit_bytes": self.commit_bytes,
            "ram_load_seconds": round(self.ram_load_seconds, 2),
            "hybrid_load_seconds": round(self.hybrid_load_seconds, 2),
            "spill_bytes": self.spill_bytes,
            "tokens_per_second_hint": self.tokens_per_second_hint,
            "notes": self.notes,
        }


def estimate_model(model_bytes: int, bench: BenchmarkResult, ram_bytes: int, commit_bytes: int) -> ModelEstimate:
    """Очень грубая оценка загрузки модели и скорости генерации.

    Загрузка «в ОЗУ» считается по измеренной пропускной способности памяти,
    «гибридная» — когда часть модели уходит в файл подкачки на SSD.
    Генерация токенов зависит от архитектуры модели и GPU, поэтому даём
    только порядок величины.
    """
    ram_speed = max(bench.ram_read_mbps, bench.ram_write_mbps, 1.0)
    disk_speed = max(max(bench.disk_write_mbps, bench.disk_read_mbps), 1.0)
    ram_load = (model_bytes / util.MIB) / ram_speed
    spill = max(0, model_bytes - int(ram_bytes * 0.75))
    hybrid_bytes = model_bytes - spill
    hybrid_load = (hybrid_bytes / util.MIB) / ram_speed + (spill / util.MIB) / disk_speed

    notes: list[str] = []
    fits = model_bytes <= commit_bytes
    if not fits:
        notes.append(
            "модель больше доступного объёма памяти: загрузка не удастся, "
            "увеличьте хранилище подкачки или возьмите модель меньше"
        )
    elif spill > 0:
        notes.append(
            f"{util.human_bytes(spill)} модели будут читаться с SSD — "
            "загрузка и генерация замедлятся"
        )
    else:
        notes.append("модель полностью помещается в оперативную память")

    if spill == 0:
        hint = "быстро (как обычно для этой модели)"
    elif bench.disk_used and bench.slowdown > 20:
        hint = "в 5–20 раз медленнее, чем в ОЗУ; для чата терпимо, для обучения — нет"
    else:
        hint = "в 2–5 раз медленнее, чем в ОЗУ"

    return ModelEstimate(
        model_bytes=model_bytes,
        fits_in_ram=spill == 0,
        ram_bytes=ram_bytes,
        commit_bytes=commit_bytes,
        ram_load_seconds=ram_load,
        hybrid_load_seconds=hybrid_load,
        spill_bytes=spill,
        tokens_per_second_hint=hint,
        notes=notes,
    )
