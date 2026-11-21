from __future__ import annotations

from allram import memory, registry


def test_pagefile_entry_parse():
    entry = registry.PagefileEntry.parse(r"C:\pagefile.sys 2048 4096")
    assert entry.path == r"C:\pagefile.sys"
    assert entry.initial_mb == 2048
    assert entry.maximum_mb == 4096


def test_pagefile_entry_parse_zero():
    entry = registry.PagefileEntry.parse(r"C:\pagefile.sys 0 0")
    assert entry.initial_mb == 0


def test_pagefile_entry_parse_plain_path():
    entry = registry.PagefileEntry.parse(r"D:\swap.sys")
    assert entry.path == r"D:\swap.sys"
    assert entry.initial_mb == 0


def test_roundtrip_dict():
    entry = registry.PagefileEntry(r"D:\pagefile_allram.sys", 100, 200)
    assert registry.PagefileEntry.from_dict(entry.as_dict()) == entry


def _resource_list(*blocks: tuple[int, int, int]) -> bytes:
    """Собирает минимальный CM_RESOURCE_LIST с блоками памяти.

    blocks — наборы (тип, start, length) в формате 64-битных дескрипторов.
    Структура: Count(4), InterfaceType(4), BusNumber(4), Type(1), Shared(1),
    Count(2), выравнивание(2), затем дескрипторы по 20 байт.
    """
    body = bytearray()
    body += (0).to_bytes(4, "little")  # InterfaceType
    body += (0).to_bytes(4, "little")  # BusNumber
    body += b"\x00"  # Type
    body += b"\x00"  # Shared
    body += len(blocks).to_bytes(2, "little")
    body += b"\x00" * 2  # выравнивание
    for kind, start, length in blocks:
        body += bytes([kind, 0, 0, 0])
        body += start.to_bytes(8, "little")
        body += length.to_bytes(8, "little")
    header = (1).to_bytes(3, "little") + b"\x00"
    return header + body


def test_sum_resource_list_memory():
    blob = _resource_list((3, 0x100000, 4 * 1024**3), (3, 0x100000000, 8 * 1024**3))
    total = memory._sum_resource_list_memory(blob)
    assert total == 12 * 1024**3


def test_sum_resource_list_bad_data():
    assert memory._sum_resource_list_memory(b"") == 0
    assert memory._sum_resource_list_memory(b"\x01\x00\x00\x00") == 0


def test_memory_info_percent():
    info = memory.MemoryInfo(total_bytes=100, avail_bytes=25)
    assert info.used_bytes == 75
    assert info.used_percent == 75.0
