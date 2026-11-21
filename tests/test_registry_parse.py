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
    """Собирает CM_RESOURCE_LIST по документированной раскладке x64.

    Заголовок: Count(4) InterfaceType(4) BusNumber(4) Version(2) Revision(2)
    Count дескрипторов(4) + выравнивание до 8; затем дескрипторы по 20 байт:
    Type(1) Share(1) Flags(2) + Start(8) + Length(4) + 4 байта.
    """
    body = bytearray()
    body += (1).to_bytes(4, "little")  # InterfaceType (Internal)
    body += (0).to_bytes(4, "little")  # BusNumber
    body += (1).to_bytes(2, "little")  # Version
    body += (1).to_bytes(2, "little")  # Revision
    body += len(blocks).to_bytes(4, "little")
    body += b"\x00" * 4  # выравнивание
    for kind, start, length in blocks:
        body += bytes([kind, 0, 0, 0])
        body += start.to_bytes(8, "little")
        if kind == 7:  # MemoryLarge: длина в старших 32 битах
            body += b"\x00" * 4 + (length >> 32).to_bytes(4, "little")
        else:
            body += (length & 0xFFFFFFFF).to_bytes(4, "little") + b"\x00" * 4
    header = (1).to_bytes(3, "little") + b"\x00"
    return header + body


def test_sum_resource_list_memory_large_descriptor():
    """CmResourceTypeMemoryLarge: 32-битное поле Length — в единицах 4 ГиБ."""
    blob = _resource_list((7, 0, 3 << 32))  # 3 × 4 ГиБ = 12 ГиБ
    assert memory._sum_resource_list_memory(blob) == 12 * 1024**3


def test_sum_resource_list_memory_tail_fallback():
    blob = (1).to_bytes(4, "little") + b"\x00" * 12 + (32 * 1024**3).to_bytes(8, "little")
    assert memory._sum_resource_list_memory(blob) == 32 * 1024**3


def test_sum_resource_list_memory():
    # Length у CmResourceTypeMemory — 32 бита, поэтому блоки по 3 и 2 ГиБ.
    blob = _resource_list((3, 0x100000, 3 * 1024**3), (3, 0x100000000, 2 * 1024**3))
    total = memory._sum_resource_list_memory(blob)
    assert total == 5 * 1024**3


def test_sum_resource_list_memory_skips_invalid_blocks():
    blob = _resource_list((3, 0x100000, 0), (1, 0x100, 0x100))
    assert memory._sum_resource_list_memory(blob) == 0


def test_sum_resource_list_bad_data():
    assert memory._sum_resource_list_memory(b"") == 0
    assert memory._sum_resource_list_memory(b"\x01\x00\x00\x00") == 0


def test_memory_info_percent():
    info = memory.MemoryInfo(total_bytes=100, avail_bytes=25)
    assert info.used_bytes == 75
    assert info.used_percent == 75.0
