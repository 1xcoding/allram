"""Состояние AllRAM: настройки в JSON, журнал операций и резервные копии."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import util

CONFIG_FILE = "config.json"
HISTORY_FILE = "history.json"
BACKUP_FILE = "backup.json"
LOCK_FILE = "watch.lock"


@dataclass
class Config:
    version: int = 1
    last_drive: str = ""
    last_size_bytes: int = 0
    last_mode: str = "pagefile"  # pagefile | ramdisk
    ramdisk_letter: str = "R"
    ramdisk_size_bytes: int = 0
    pagefile_min_mb: int = 0
    pagefile_max_mb: int = 0
    preset: str = ""
    disable_temp_variables: bool = False
    applied_at: str = ""
    ramdisk_save_image: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        config = cls()
        for field_name, value in data.items():
            if not hasattr(config, field_name):
                continue
            current = getattr(config, field_name)
            try:
                if isinstance(current, bool):
                    setattr(config, field_name, bool(value))
                elif isinstance(current, int):
                    setattr(config, field_name, int(value))
                elif isinstance(current, str):
                    setattr(config, field_name, str(value))
                else:
                    setattr(config, field_name, value)
            except (TypeError, ValueError):
                continue
        return config


class StateStore:
    """Файловое хранилище состояния в каталоге данных AllRAM."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else util.ensure_data_dir()

    # -- пути ---------------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.directory / CONFIG_FILE

    @property
    def history_path(self) -> Path:
        return self.directory / HISTORY_FILE

    @property
    def backup_path(self) -> Path:
        return self.directory / BACKUP_FILE

    # -- конфигурация -------------------------------------------------------
    def load_config(self) -> Config:
        data = util.json_read(self.config_path, {})
        if not isinstance(data, dict):
            data = {}
        return Config.from_dict(data)

    def save_config(self, config: Config) -> None:
        util.ensure_data_dir()
        util.json_write(self.config_path, config.as_dict())

    def update_config(self, **changes: Any) -> Config:
        config = self.load_config()
        for key, value in changes.items():
            if hasattr(config, key):
                setattr(config, key, value)
        self.save_config(config)
        return config

    # -- журнал -------------------------------------------------------------
    def history(self) -> list[dict[str, Any]]:
        data = util.json_read(self.history_path, [])
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def add_history(self, kind: str, summary: str, details: dict[str, Any] | None = None) -> None:
        entries = self.history()
        entries.append(
            {
                "time": util.now_iso(),
                "kind": kind,
                "summary": summary,
                "details": details or {},
            }
        )
        util.json_write(self.history_path, entries[-200:])

    # -- резервная копия ----------------------------------------------------
    def save_backup(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        backup = {
            "id": util.now_iso().replace(":", "-"),
            "created_at": util.now_iso(),
            "kind": kind,
            "data": data,
        }
        backups = self.backups()
        backups.append(backup)
        util.json_write(self.backup_path, backups[-40:])
        return backup

    def backups(self) -> list[dict[str, Any]]:
        data = util.json_read(self.backup_path, [])
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def latest_backup(self) -> dict[str, Any] | None:
        backups = self.backups()
        return backups[-1] if backups else None

    # -- блокировка наблюдателя --------------------------------------------
    def acquire_watch_lock(self) -> bool:
        lock = self.directory / LOCK_FILE
        if lock.exists():
            try:
                pid = int(lock.read_text(encoding="utf-8").strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                return False
        self.directory.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release_watch_lock(self) -> None:
        lock = self.directory / LOCK_FILE
        try:
            if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass

    # -- экспорт ------------------------------------------------------------
    def export_json(self, path: Path) -> Path:
        payload = {
            "app": "AllRAM",
            "version": __import__("allram").__version__,
            "exported_at": util.now_iso(),
            "config": self.load_config().as_dict(),
            "history": self.history(),
            "backups": self.backups(),
        }
        util.json_write(path, payload)
        return path

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": self.load_config().as_dict(),
            "history": self.history()[-10:],
        }


def _pid_alive(pid: int) -> bool:
    if util.is_windows():
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:  # pragma: no cover
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
