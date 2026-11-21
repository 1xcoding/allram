from __future__ import annotations

import pytest

from allram import pagefile, util
from allram.state import StateStore


def test_make_plan_rejects_missing_drive(monkeypatch):
    monkeypatch.setattr(pagefile.util, "drive_exists", lambda letter: False)
    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    with pytest.raises(pagefile.PagefileError):
        pagefile.make_plan("Q", 4 * util.GIB)


def test_make_plan_too_big(monkeypatch):
    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    monkeypatch.setattr(pagefile.util, "drive_exists", lambda letter: True)
    monkeypatch.setattr(pagefile.util, "free_space", lambda root: 10 * util.GIB)
    monkeypatch.setattr(pagefile.memory, "memory_info", lambda: pagefile.memory.MemoryInfo(total_bytes=16 * util.GIB))
    with pytest.raises(pagefile.PagefileError):
        pagefile.make_plan("D", 20 * util.GIB)


def test_make_plan_ok(monkeypatch):
    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    monkeypatch.setattr(pagefile.util, "drive_exists", lambda letter: True)
    monkeypatch.setattr(pagefile.util, "free_space", lambda root: 200 * util.GIB)
    monkeypatch.setattr(pagefile.memory, "memory_info", lambda: pagefile.memory.MemoryInfo(total_bytes=16 * util.GIB))
    plan = pagefile.make_plan("d", 64 * util.GIB + 12345)
    assert plan.drive == "D"
    assert plan.path.endswith("pagefile_allram.sys")
    assert plan.initial_mb == plan.maximum_mb
    assert plan.size_bytes % util.MIB == 0
    assert all("останется" not in warning for warning in plan.warnings)


def test_recommend_size_bytes():
    size = pagefile.recommend_size_bytes(100 * util.GIB, 32 * util.GIB)
    assert size == 16 * util.GIB  # min(66 ГиБ, 16 ГиБ)
    assert size % util.MIB == 0


def test_entries_for_apply_keeps_other_volumes(monkeypatch):
    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    plan = pagefile.PagefilePlan(drive="D", path=r"D:\pagefile_allram.sys", size_bytes=8 * util.GIB, initial_mb=8192, maximum_mb=8192)
    keep = [
        pagefile.registry.PagefileEntry(r"C:\pagefile.sys", 2000, 3000),
        pagefile.registry.PagefileEntry(r"D:\pagefile.sys", 1000, 1000),
        pagefile.registry.PagefileEntry(r"D:\pagefile_allram.sys", 8192, 8192),
    ]
    entries = pagefile._entries_for_apply(plan, keep)
    paths = [entry.path for entry in entries]
    assert r"C:\pagefile.sys" in paths
    assert r"D:\pagefile.sys" not in paths
    assert paths.count(r"D:\pagefile_allram.sys") == 1


def test_apply_plan_requires_admin(monkeypatch):
    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    monkeypatch.setattr(pagefile.util, "is_admin", lambda: False)
    plan = pagefile.PagefilePlan(drive="D", path=r"D:\pagefile_allram.sys", size_bytes=8 * util.GIB, initial_mb=8192, maximum_mb=8192)
    with pytest.raises(pagefile.PagefileError):
        pagefile.apply_plan(plan, StateStore())


def test_apply_plan_writes_registry(monkeypatch, tmp_path):
    written = {}

    def fake_write(entries, automatic=False):
        written["entries"] = [(e.path, e.initial_mb, e.maximum_mb) for e in entries]
        written["automatic"] = automatic

    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    monkeypatch.setattr(pagefile.util, "is_admin", lambda: True)
    monkeypatch.setattr(pagefile.registry, "write_pagefile_settings", fake_write)
    monkeypatch.setattr(
        pagefile.registry,
        "read_pagefile_settings",
        lambda: pagefile.registry.PagefileSettings(
            [r"D:\pagefile_allram.sys 8192 8192"], [r"D:\pagefile_allram.sys"], False
        ),
    )
    monkeypatch.setattr(
        pagefile.memory, "memory_info", lambda: pagefile.memory.MemoryInfo(total_bytes=16 * util.GIB, commit_limit=32 * util.GIB)
    )
    store = StateStore(tmp_path / "data")
    plan = pagefile.PagefilePlan(drive="D", path=r"D:\pagefile_allram.sys", size_bytes=8 * util.GIB, initial_mb=8192, maximum_mb=8192)
    result = pagefile.apply_plan(plan, store)
    assert result.success
    assert result.reboot_required
    assert written["automatic"] is False
    assert written["entries"][0][0].endswith("pagefile_allram.sys")
    assert store.latest_backup()["kind"] == "pagefile"


def test_dry_run_does_not_write(monkeypatch, tmp_path):
    called = {"write": False}
    monkeypatch.setattr(pagefile.util, "is_windows", lambda: True)
    monkeypatch.setattr(pagefile.util, "is_admin", lambda: True)
    monkeypatch.setattr(pagefile.registry, "write_pagefile_settings", lambda *a, **k: called.update(write=True))
    monkeypatch.setattr(
        pagefile.registry, "read_pagefile_settings", lambda: pagefile.registry.PagefileSettings([], [], True)
    )
    store = StateStore(tmp_path / "data")
    plan = pagefile.PagefilePlan(drive="D", path=r"D:\pagefile_allram.sys", size_bytes=8 * util.GIB, initial_mb=8192, maximum_mb=8192)
    result = pagefile.apply_plan(plan, store, dry_run=True)
    assert result.success
    assert called["write"] is False
