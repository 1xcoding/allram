"""Работа с реестром Windows, относящимся к памяти и подкачке.

Здесь всё, что AllRAM читает и (по команде пользователя) меняет в реестре:
  • HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management
      PagingFiles (REG_MULTI_SZ) — какие файлы подкачки Windows создаёт при старте;
      ExistingPageFiles (REG_MULTI_SZ) — какие действуют сейчас;
      AutomaticManagedPagefile (REG_DWORD) — автоматическое управление;
  • HKLM\\SYSTEM\\CurrentControlSet\\Services\\ImDisk* — настройки RAM-диска ImDisk;
  • HKCU\\...\\Run — автозапуск AllRAM при входе в систему;
  • HARDWARE\\RESOURCEMAP\\...\\Physical Memory — фактический объём ОЗУ (только чтение);
  • HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0 — модель процессора (только чтение).

ВАЖНО: Windows не позволяет «дописать» физическую оперативную память в реестр —
объём ОЗУ определяет ядро при загрузке. Поэтому AllRAM прописывает в реестр
корректные настройки подкачки и RAM-диска: система действительно начинает
использовать SSD как дополнительную память, а «Система → ОЗУ» растёт за счёт
файла подкачки, который добавляется к общему объёму памяти (commit limit).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from . import util

try:  # pragma: no cover - импортируется только на Windows
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

MEMORY_MANAGEMENT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
SESSION_MANAGER_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager"
IMDISK_SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REGISTRY_KEY = r"Software\AllRAM"
APP_REGISTRY_KEY_HKLM = r"SOFTWARE\AllRAM"


class RegistryError(RuntimeError):
    pass


def require_windows() -> None:
    if not util.is_windows() or winreg is None:
        raise RegistryError("реестр Windows доступен только на Windows")


# ---------------------------------------------------------------------------
# Низкоуровневые помощники
# ---------------------------------------------------------------------------


def read_value(root: int, path: str, name: str):
    require_windows()
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)
    except OSError:
        return None


def write_value(root: int, path: str, name: str, value, kind: int) -> None:
    require_windows()
    try:
        with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            winreg.SetValueEx(key, name, 0, kind, value)
    except PermissionError as exc:
        raise RegistryError(
            f"нет прав на запись в HKEY_LOCAL_MACHINE\\{path} — запустите AllRAM от администратора"
        ) from exc
    except OSError as exc:
        raise RegistryError(f"не удалось записать реестр: {exc}") from exc


def delete_value(root: int, path: str, name: str) -> bool:
    require_windows()
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def delete_tree(root: int, path: str) -> None:
    require_windows()
    try:
        winreg.DeleteKeyEx(root, path, winreg.KEY_WOW64_64KEY, 0)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Файл подкачки
# ---------------------------------------------------------------------------


@dataclass
class PagefileSettings:
    paging_files: list[str]
    existing_files: list[str]
    automatic: bool | None

    def summary(self) -> str:
        if self.automatic:
            return "автоматически (Windows сам выбирает размер подкачки)"
        if not self.paging_files:
            return "файл подкачки отключён"
        return ", ".join(self.paging_files)


def read_pagefile_settings() -> PagefileSettings:
    if not util.is_windows():
        return PagefileSettings([], [], None)
    paging = read_value(winreg.HKEY_LOCAL_MACHINE, MEMORY_MANAGEMENT_KEY, "PagingFiles")
    existing = read_value(winreg.HKEY_LOCAL_MACHINE, MEMORY_MANAGEMENT_KEY, "ExistingPageFiles")
    automatic = read_value(winreg.HKEY_LOCAL_MACHINE, MEMORY_MANAGEMENT_KEY, "AutomaticManagedPagefile")
    return PagefileSettings(
        paging_files=_as_list(paging[0]) if paging else [],
        existing_files=_as_list(existing[0]) if existing else [],
        automatic=bool(automatic[0]) if automatic else None,
    )


def _as_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [value]
    return []


def write_pagefile_settings(entries: Iterable["PagefileEntry"], *, automatic: bool = False) -> None:
    """Записывает список файлов подкачки в реестр.

    Каждая запись — "C:\\pagefile.sys начальный_МБ максимальный_МБ".
    Пустой список = подкачка отключена.
    """
    require_windows()
    normalized = []
    for entry in entries:
        normalized.append(f"{entry.path} {entry.initial_mb} {entry.maximum_mb}")
    write_value(
        winreg.HKEY_LOCAL_MACHINE,
        MEMORY_MANAGEMENT_KEY,
        "AutomaticManagedPagefile",
        1 if automatic else 0,
        winreg.REG_DWORD,
    )
    write_value(
        winreg.HKEY_LOCAL_MACHINE,
        MEMORY_MANAGEMENT_KEY,
        "PagingFiles",
        normalized,
        winreg.REG_MULTI_SZ,
    )


@dataclass(frozen=True)
class PagefileEntry:
    path: str
    initial_mb: int
    maximum_mb: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "initial_mb": self.initial_mb, "maximum_mb": self.maximum_mb}

    @classmethod
    def from_dict(cls, data: dict) -> "PagefileEntry":
        return cls(
            path=str(data["path"]),
            initial_mb=int(data.get("initial_mb", 0)),
            maximum_mb=int(data.get("maximum_mb", 0)),
        )

    @classmethod
    def parse(cls, text: str) -> "PagefileEntry":
        """Разбирает строку реестра 'C:\\pagefile.sys 4096 8192'."""
        parts = text.rsplit(" ", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return cls(parts[0], int(parts[1]), int(parts[2]))
        return cls(text.strip(), 0, 0)


class PagefileRestore:
    """Снимок настроек подкачки для отката."""

    def __init__(self, settings: PagefileSettings | None = None) -> None:
        self.settings = settings or read_pagefile_settings()

    def as_dict(self) -> dict[str, object]:
        return {
            "paging_files": self.settings.paging_files,
            "automatic": self.settings.automatic,
        }

    def restore(self) -> str:
        if not util.is_windows():
            return "пропущено: не Windows"
        if self.settings.automatic:
            write_value(
                winreg.HKEY_LOCAL_MACHINE,
                MEMORY_MANAGEMENT_KEY,
                "AutomaticManagedPagefile",
                1,
                winreg.REG_DWORD,
            )
            return "возвращено автоматическое управление подкачкой"
        entries = [PagefileEntry.parse(text) for text in self.settings.paging_files]
        write_pagefile_settings(entries)
        return "восстановлен исходный список файлов подкачки"


def pagefile_path_on_drive(root: str, name: str = "pagefile.sys") -> str:
    return os.path.join(root, name)


# ---------------------------------------------------------------------------
# RAM-диск ImDisk
# ---------------------------------------------------------------------------


def imdisk_services() -> list[str]:
    """Список имён служб ImDisk, найденных в реестре."""
    if not util.is_windows():
        return []
    found: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, IMDISK_SERVICES_KEY, 0, winreg.KEY_READ) as services:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(services, index)
                except OSError:
                    break
                index += 1
                if name.lower().startswith("imdisk"):
                    found.append(name)
    except OSError:
        return []
    return found


def pagefile_drives() -> list[str]:
    """Буквы дисков, на которых сейчас есть записи о файле подкачки."""
    settings = read_pagefile_settings()
    drives: list[str] = []
    for text in settings.paging_files:
        path = PagefileEntry.parse(text).path
        drive, _ = os.path.splitdrive(path)
        if drive:
            letter = drive[0].upper()
            if letter not in drives:
                drives.append(letter)
    return drives


# ---------------------------------------------------------------------------
# Автозапуск
# ---------------------------------------------------------------------------


def autostart_command() -> str:
    quote = '"{}"' if " " in util.executable_path() else "{}"
    exe = quote.format(util.executable_path())
    if util.is_frozen():
        return f"{exe} watch"
    return f'{exe} -m allram watch'


def set_autostart(enabled: bool) -> bool:
    require_windows()
    if enabled:
        write_value(winreg.HKEY_CURRENT_USER, RUN_KEY, "AllRAM", autostart_command(), winreg.REG_SZ)
    else:
        delete_value(winreg.HKEY_CURRENT_USER, RUN_KEY, "AllRAM")
    return enabled


def get_autostart() -> str | None:
    if not util.is_windows():
        return None
    value = read_value(winreg.HKEY_CURRENT_USER, RUN_KEY, "AllRAM")
    return str(value[0]) if value else None


# ---------------------------------------------------------------------------
# Своя ветка AllRAM (журнал последних действий)
# ---------------------------------------------------------------------------


def remember(kind: str, payload: dict[str, object]) -> None:
    """Сохраняет сведения о последней операции в HKCU\\Software\\AllRAM."""
    if not util.is_windows() or winreg is None:
        return
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, f"last_{kind}", 0, winreg.REG_SZ, util.now_iso())
            for name, value in payload.items():
                winreg.SetValueEx(
                    key,
                    f"{kind}_{name}",
                    0,
                    winreg.REG_SZ if isinstance(value, str) else winreg.REG_QWORD if isinstance(value, int) else winreg.REG_SZ,
                    str(value) if not isinstance(value, int) else value,
                )
    except OSError:
        pass


def version_marker() -> str | None:
    if not util.is_windows():
        return None
    value = read_value(winreg.HKEY_CURRENT_USER, APP_REGISTRY_KEY, "version")
    return str(value[0]) if value else None


def write_version_marker(version: str) -> None:
    if not util.is_windows() or winreg is None:
        return
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "version", 0, winreg.REG_SZ, version)
            winreg.SetValueEx(key, "installed_at", 0, winreg.REG_SZ, util.now_iso())
    except OSError:
        pass
