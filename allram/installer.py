"""Команда install: права администратора, метка в реестре, автозапуск."""

from __future__ import annotations

import os

from . import __version__, registry, util
from .state import StateStore


def install(store: StateStore, *, autostart: bool = True) -> dict[str, object]:
    """Первичная настройка AllRAM на компьютере."""
    steps: list[str] = []
    if not util.is_windows():
        return {
            "success": False,
            "message": "установка рассчитана на Windows",
            "steps": steps,
        }

    if util.is_admin():
        steps.append("права администратора: есть")
    else:
        steps.append("права администратора: нет (записи в HKLM будут недоступны)")

    registry.write_version_marker(__version__)
    steps.append("метка AllRAM записана в реестр (HKCU\\Software\\AllRAM)")

    if autostart:
        registry.set_autostart(True)
        steps.append("добавлен автозапуск наблюдателя: HKCU\\...\\Run\\AllRAM watch")
    else:
        steps.append("автозапуск не включён (флаг --no-autostart)")

    directory = util.ensure_data_dir()
    steps.append(f"каталог данных: {directory}")
    store.save_config(store.load_config())
    steps.append("конфигурация сохранена")
    store.add_history("install", f"AllRAM {__version__} установлен", {"exe": util.executable_path()})

    exe = util.executable_path()
    return {
        "success": True,
        "message": "AllRAM готов к работе",
        "steps": steps,
        "executable": exe,
    }


def uninstall(store: StateStore, *, remove_pagefile: bool = False) -> dict[str, object]:
    """Удаляет автозапуск и метку, при желании — настройки подкачки."""
    steps: list[str] = []
    if util.is_windows():
        registry.set_autostart(False)
        steps.append("автозапуск удалён")
        registry.delete_tree(registry.winreg.HKEY_CURRENT_USER, registry.APP_REGISTRY_KEY)
        steps.append("ключ HKCU\\Software\\AllRAM удалён")
        if remove_pagefile:
            from . import pagefile

            try:
                pagefile.restore(store)
                steps.append("настройки подкачки возвращены к резервной копии")
            except pagefile.PagefileError as exc:
                steps.append(f"подкачка не изменена: {exc}")
    else:
        steps.append("Windows-специфичные действия пропущены")

    steps.append(f"данные остались в {util.data_dir()} (удалите каталог вручную при необходимости)")
    store.add_history("uninstall", "AllRAM удалён из автозапуска", {})
    return {"success": True, "message": "Готово", "steps": steps}
