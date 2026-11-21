from __future__ import annotations

import json

from allram import cli, memory, pagefile, util


def _run(monkeypatch, *argv, store_dir):
    monkeypatch.setenv("ALLRAM_HOME", str(store_dir))
    return cli.main([*argv, "--no-color"])


def test_version(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "AllRAM" in out


def test_status_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        memory, "memory_info", lambda: memory.MemoryInfo(total_bytes=16 * util.GIB, avail_bytes=8 * util.GIB, commit_limit=32 * util.GIB)
    )
    monkeypatch.setattr(
        cli.report,
        "status_report",
        lambda: {"memory": memory.memory_info().as_dict(), "pagefile": None, "ramdisk": {"installed": False, "notes": []}},
    )
    code = _run(monkeypatch, "status", "--json", store_dir=tmp_path)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["memory"]["total_bytes"] == 16 * util.GIB


def test_status_threshold(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli.report,
        "status_report",
        lambda: {
            "memory": {"used_percent": 95, "total_bytes": util.GIB, "avail_bytes": 0, "commit_limit": util.GIB},
            "pagefile": None,
            "ramdisk": {"installed": False, "notes": []},
        },
    )
    code = _run(monkeypatch, "status", "--json", "--threshold", "90", store_dir=tmp_path)
    assert code == 2


def test_setup_plan_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(util, "is_windows", lambda: True)
    monkeypatch.setattr(util, "drive_exists", lambda letter: True)
    monkeypatch.setattr(util, "free_space", lambda root: 500 * util.GIB)
    monkeypatch.setattr(pagefile.memory, "memory_info", lambda: memory.MemoryInfo(total_bytes=16 * util.GIB, commit_limit=20 * util.GIB))
    code = _run(monkeypatch, "setup", "-d", "D", "-s", "64G", "--dry-run", "--json", store_dir=tmp_path)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["drive"] == "D"
    assert data["size_bytes"] == 64 * util.GIB
    assert data["expected_commit_limit"] == 84 * util.GIB


def test_setup_requires_windows(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(util, "is_windows", lambda: False)
    code = _run(monkeypatch, "setup", "-d", "D", "-s", "1G", store_dir=tmp_path)
    assert code == 1
    assert "Windows" in capsys.readouterr().out


def test_explain_contains_registry(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, "explain", store_dir=tmp_path)
    assert code == 0
    assert "PagingFiles" in capsys.readouterr().out


def test_presets_json(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, "presets", "--json", store_dir=tmp_path)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    keys = {item["key"] for item in data}
    assert {"ai", "browser", "games", "max", "ramdisk"} <= keys


def test_settings_roundtrip(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, "settings", "--default-drive", "E", "--default-size", "32G", "--json", store_dir=tmp_path)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["last_drive"] == "E"
    assert data["last_size_bytes"] == 32 * util.GIB


def test_models_known(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        memory, "memory_info", lambda: memory.MemoryInfo(total_bytes=16 * util.GIB, commit_limit=100 * util.GIB)
    )
    code = _run(monkeypatch, "models", "--model", "8b", "--json", store_dir=tmp_path)
    assert code == 0
    assert "ОЗУ" in capsys.readouterr().out


def test_history_empty(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, "history", store_dir=tmp_path)
    assert code == 0
    assert "пусто" in capsys.readouterr().out.lower()


def test_watch_once_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        memory, "memory_info", lambda: memory.MemoryInfo(total_bytes=16 * util.GIB, avail_bytes=4 * util.GIB)
    )
    monkeypatch.setattr(cli.registry, "read_pagefile_settings", lambda: cli.registry.PagefileSettings([], [], False))
    code = _run(monkeypatch, "watch", "--once", "--json", store_dir=tmp_path)
    assert code == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert "used_percent" in data
