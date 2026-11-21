"""Управление файлом подкачки — основной способ превратить SSD в «оперативку».

Windows использует файл подкачки как продолжение оперативной памяти:
сумма ОЗУ + подкачки называется commit limit. Программы (включая
нейросети) могут задействовать этот объём так же, как обычную память,
только медленнее — операции чтения/записи уходят на SSD.

Что делает AllRAM:
  1. читает текущие настройки из реестра и сохраняет их;
  2. просит Windows перенести подкачку на выбранный SSD и задать размер;
  3. после перезагрузки система сообщает увеличенный общий объём памяти.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from . import memory, registry, util

try:  # ntpath понимает Windows-пути даже когда AllRAM запущен на Linux (тесты)
    import ntpath
except ImportError:  # pragma: no cover
    ntpath = os.path  # type: ignore[assignment]

MIN_PAGEFILE_MIB = 16
MAX_PAGEFILES_PER_VOLUME = 16


class PagefileError(RuntimeError):
    pass


@dataclass
class PagefilePlan:
    drive: str  # буква диска, например "D"
    path: str  # D:\pagefile_allram.sys
    size_bytes: int  # желаемый размер в байтах
    initial_mb: int = 0
    maximum_mb: int = 0
    mode: str = "custom"  # custom | ideal
    warnings: list[str] = field(default_factory=list)

    @property
    def root(self) -> str:
        return f"{self.drive}:\\"

    def as_dict(self) -> dict[str, Any]:
        return {
            "drive": self.drive,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "initial_mb": self.initial_mb,
            "maximum_mb": self.maximum_mb,
            "mode": self.mode,
            "warnings": self.warnings,
        }


@dataclass
class ApplyResult:
    success: bool
    message: str
    reboot_required: bool
    plan: PagefilePlan | None = None
    details: list[str] = field(default_factory=list)
    previous_commit_limit: int = 0
    expected_commit_limit: int = 0


def make_plan(
    drive: str,
    size_bytes: int,
    *,
    mode: str = "custom",
    filename: str = "pagefile_allram.sys",
    min_free_reserve: int = 2 * util.GIB,
) -> PagefilePlan:
    """Создаёт и валидирует план переноса подкачки на указанный диск."""
    letter = util.normalize_drive_letter(drive)
    if not util.is_windows():
        raise PagefileError("файл подкачки настраивается только в Windows")
    if not util.drive_exists(letter):
        raise PagefileError(f"диска {letter}: не существует")

    root = f"{letter}:\\"
    free = util.free_space(root)
    if free < 0:
        raise PagefileError(f"не удалось определить свободное место на {letter}:")

    warnings: list[str] = []
    if size_bytes > free:
        raise PagefileError(
            f"на {letter}: только {util.human_bytes(free)} свободно, "
            f"а запрошено {util.human_bytes(size_bytes)}"
        )
    if size_bytes + min_free_reserve > free:
        warnings.append(
            f"после создания останется {util.human_bytes(free - size_bytes)} — "
            "SSD будет почти заполнен, это замедляет его работу"
        )

    current = memory.memory_info()
    if current.total_bytes and size_bytes > current.total_bytes * 3:
        warnings.append(
            "размер подкачки более чем в 3 раза превышает физическую ОЗУ: "
            "программы будут «тормозить» при переполнении памяти"
        )

    size_mib = max(MIN_PAGEFILE_MIB, int(size_bytes // util.MIB))
    return PagefilePlan(
        drive=letter,
        path=ntpath.join(root, filename),
        size_bytes=size_mib * util.MIB,
        initial_mb=size_mib,
        maximum_mb=size_mib,
        mode=mode,
        warnings=warnings,
    )


def recommend_size_bytes(free_bytes: int, ram_bytes: int) -> int:
    """Рекомендуемый размер «расширения ОЗУ» (примерно на 2/3 свободного SSD)."""
    by_disk = int(free_bytes * 0.66) if free_bytes > 0 else 0
    by_ram = max(int(ram_bytes * 0.5), 8 * util.GIB) if ram_bytes else 16 * util.GIB
    target = min(by_disk, by_ram) if by_disk else by_ram
    target = max(target, 4 * util.GIB)
    return util.round_down_to_mib(target)


def free_disk_estimate(letter: str) -> int:
    return util.free_space(f"{letter}:\\")


def read_current() -> registry.PagefileSettings:
    return registry.read_pagefile_settings()


def _entries_for_apply(plan: PagefilePlan, keep: list[registry.PagefileEntry]) -> list[registry.PagefileEntry]:
    """Собирает итоговый список подкачки: новый файл + пользовательские, но не сам AllRAM."""
    result: list[registry.PagefileEntry] = [
        registry.PagefileEntry(plan.path, plan.initial_mb, plan.maximum_mb)
    ]
    for entry in keep:
        if not entry.path:
            continue
        drive, _rest = ntpath.splitdrive(entry.path)
        same_volume = drive[:1].upper() == plan.drive.upper()
        if same_volume and ntpath.basename(entry.path).lower() != "pagefile_allram.sys":
            # На целевом томе оставляем только наш файл, чтобы не занимать место дважды.
            continue
        if ntpath.basename(entry.path).lower() == "pagefile_allram.sys":
            continue
        result.append(entry)
    return result


def apply_plan(
    plan: PagefilePlan,
    store,
    *,
    keep_other: bool = True,
    dry_run: bool = False,
) -> ApplyResult:
    """Записывает настройки подкачки в реестр и сохраняет бэкап."""
    if not util.is_windows():
        raise PagefileError("это действие доступно только в Windows")
    if not util.is_admin():
        raise PagefileError("нужны права администратора")

    before = memory.memory_info()
    settings = registry.read_pagefile_settings()

    existing_paths = [registry.PagefileEntry.parse(text) for text in settings.paging_files]
    if not keep_other:
        keep: list[registry.PagefileEntry] = []
    else:
        keep = [entry for entry in existing_paths if "pagefile_allram.sys" not in entry.path.lower()]

    entries = _entries_for_apply(plan, keep)
    if len(entries) > MAX_PAGEFILES_PER_VOLUME:
        raise PagefileError(f"Windows поддерживает не более {MAX_PAGEFILES_PER_VOLUME} файлов подкачки")

    backup_data = {
        "pagefile": {
            "paging_files": settings.paging_files,
            "automatic": settings.automatic,
            "existing": settings.existing_files,
        },
        "plan": plan.as_dict(),
    }
    backup = store.save_backup("pagefile", backup_data)

    details = [
        f"было: {', '.join(settings.paging_files) if settings.paging_files else 'файл подкачки отсутствовал'}",
        f"стало: {', '.join(f'{e.path} ({e.initial_mb} МБ)' for e in entries)}",
        f"резервная копия: {backup['id']}",
    ]

    if dry_run:
        return ApplyResult(
            success=True,
            message="пробный прогон: реестр не изменялся",
            reboot_required=True,
            plan=plan,
            details=details,
            previous_commit_limit=before.commit_limit,
            expected_commit_limit=before.commit_limit + plan.size_bytes,
        )

    registry.write_pagefile_settings(entries, automatic=False)
    after_settings = registry.read_pagefile_settings()

    ok = any(plan.path.lower() in text.lower() for text in after_settings.paging_files) and not after_settings.automatic
    expected = before.commit_limit + plan.size_bytes
    return ApplyResult(
        success=ok,
        message=(
            "настройки подкачки записаны в реестр"
            if ok
            else "не удалось проверить запись настроек — откройте «Проверку» после перезагрузки"
        ),
        reboot_required=True,
        plan=plan,
        details=details,
        previous_commit_limit=before.commit_limit,
        expected_commit_limit=expected,
    )


def restore(store, *, settings: registry.PagefileSettings | None = None) -> bool:
    """Возвращает исходные настройки подкачки из последней резервной копии."""
    if not util.is_windows():
        raise PagefileError("это действие доступно только в Windows")
    if not util.is_admin():
        raise PagefileError("нужны права администратора")

    backup = store.latest_backup()
    if not backup or backup.get("kind") != "pagefile":
        raise PagefileError("нет сохранённой резервной копии настроек подкачки")

    data = backup.get("data", {}).get("pagefile", {})
    paging_files = data.get("paging_files") or []
    automatic = bool(data.get("automatic"))
    if automatic:
        registry.write_pagefile_settings([], automatic=True)
        store.add_history("restore", "подкачка снова управляется автоматически", {"backup": backup["id"]})
        return True

    entries = [registry.PagefileEntry.parse(text) for text in paging_files]
    registry.write_pagefile_settings(entries, automatic=False)
    store.add_history(
        "restore",
        "восстановлены прежние настройки подкачки",
        {"backup": backup["id"], "paging_files": paging_files},
    )
    return True


def remove_allram_pagefile(plan_path: str | None = None) -> None:
    """Удаляет файл подкачки AllRAM с диска после отключения в реестре."""
    if not util.is_windows():
        return
    if not plan_path:
        settings = registry.read_pagefile_settings()
        for text in settings.paging_files:
            if "pagefile_allram.sys" in text.lower():
                plan_path = registry.PagefileEntry.parse(text).path
                break
    if not plan_path:
        return
    try:
        if os.path.exists(plan_path):
            os.remove(plan_path)
    except OSError:
        pass


def status_summary() -> dict[str, Any]:
    """Краткая сводка для `allram status`."""
    info = memory.memory_info()
    settings = registry.read_pagefile_settings() if util.is_windows() else None
    return {
        "memory": info.as_dict(),
        "pagefile": {
            "automatic": settings.automatic if settings else None,
            "paging_files": settings.paging_files if settings else [],
            "existing_files": settings.existing_files if settings else [],
        },
    }
