from __future__ import annotations

from allram.state import Config, StateStore


def test_config_roundtrip(tmp_path):
    store = StateStore(tmp_path)
    config = store.load_config()
    assert isinstance(config, Config)
    config.last_drive = "D"
    config.last_size_bytes = 1024
    config.last_mode = "pagefile"
    store.save_config(config)
    assert store.load_config().last_drive == "D"
    assert store.load_config().last_size_bytes == 1024


def test_config_ignores_unknown_and_bad_types(tmp_path):
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"unknown": 1, "last_size_bytes": "not-a-number", "last_drive": "E"}))
    config = StateStore(tmp_path).load_config()
    assert config.last_drive == "E"
    assert config.last_size_bytes == 0


def test_history_limited(tmp_path):
    store = StateStore(tmp_path)
    for index in range(5):
        store.add_history("test", f"event {index}")
    assert len(store.history()) == 5
    assert store.history()[-1]["summary"] == "event 4"


def test_backup_and_latest(tmp_path):
    store = StateStore(tmp_path)
    store.save_backup("pagefile", {"a": 1})
    store.save_backup("pagefile", {"a": 2})
    backup = store.latest_backup()
    assert backup is not None
    assert backup["data"] == {"a": 2}


def test_watch_lock(tmp_path):
    store = StateStore(tmp_path)
    assert store.acquire_watch_lock()
    store.release_watch_lock()
    assert store.acquire_watch_lock()
    store.release_watch_lock()


def test_export_json(tmp_path):
    store = StateStore(tmp_path)
    store.add_history("test", "hello")
    out = store.export_json(tmp_path / "export.json")
    text = out.read_text(encoding="utf-8")
    assert '"hello"' in text
