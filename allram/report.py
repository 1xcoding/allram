"""Сборка рекомендаций и текстовых отчётов (общих для CLI и тестов)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import memory, pagefile, presets, registry, util


@dataclass
class Recommendation:
    title: str
    body: str
    command: str = ""


@dataclass
class PlanReport:
    drive: str
    size_bytes: int
    free_bytes: int
    ram_bytes: int
    commit_limit: int
    commit_total: int
    pagefile_path: str = ""
    pagefile_initial_mb: int = 0
    pagefile_maximum_mb: int = 0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def expected_commit_limit(self) -> int:
        return self.commit_limit + self.size_bytes

    @property
    def total_memory_gib(self) -> float:
        return self.expected_commit_limit / util.GIB

    def as_dict(self) -> dict[str, Any]:
        return {
            "drive": self.drive,
            "size_bytes": self.size_bytes,
            "free_bytes": self.free_bytes,
            "ram_bytes": self.ram_bytes,
            "commit_limit": self.commit_limit,
            "commit_total": self.commit_total,
            "expected_commit_limit": self.expected_commit_limit,
            "pagefile_path": self.pagefile_path,
            "warnings": self.warnings,
        }


def build_plan(drive: str, size_bytes: int) -> PlanReport:
    letter = util.normalize_drive_letter(drive)
    free = pagefile.free_disk_estimate(letter)
    if free < 0:
        raise pagefile.PagefileError(f"диск {letter}: недоступен")
    plan = pagefile.make_plan(letter, size_bytes)
    info = memory.memory_info()
    return PlanReport(
        drive=letter,
        size_bytes=plan.size_bytes,
        free_bytes=free,
        ram_bytes=info.total_bytes,
        commit_limit=info.commit_limit,
        commit_total=info.commit_total,
        pagefile_path=plan.path,
        pagefile_initial_mb=plan.initial_mb,
        pagefile_maximum_mb=plan.maximum_mb,
        warnings=plan.warnings,
        notes=[
            "после перезагрузки диспетчер задач покажет увеличенный файл подкачки, "
            "а суммарная доступная память возрастёт",
            "операции с этим объёмом идут через SSD, поэтому он медленнее настоящей ОЗУ",
        ],
    )


def recommendation_report() -> tuple[list[Recommendation], dict[str, Any]]:
    """Подбирает диск и размер подкачки по состоянию компьютера."""
    info = memory.memory_info()
    profile = memory.machine_profile()
    drives = util.list_drives() if util.is_windows() else []

    best_drive = ""
    best_free = 0
    for letter in drives:
        if letter in ("A", "B"):
            continue
        free = util.free_space(f"{letter}:\\")
        if free > best_free:
            best_drive, best_free = letter, free

    recommendations: list[Recommendation] = []
    if best_drive:
        preset = presets.get("ai") or presets.get("browser")
        size = preset.target_bytes(best_free) if preset else 0
        recommendations.append(
            Recommendation(
                title=f"Диск {best_drive}: — больше всего свободного места",
                body=(
                    f"{util.human_bytes(best_free)} свободно. Предлагаю расширить память "
                    f"на {util.human_bytes(size)} ({preset.title if preset else 'универсально'})."
                ),
                command=f"allram setup --drive {best_drive} --size {size // util.GIB}G --apply",
            )
        )
    recommendations.append(
        Recommendation(
            title="Оперативная память",
            body=(
                f"{util.human_bytes(info.total_bytes)} ОЗУ, занято {info.used_percent:.0f} %. "
                + ("Памяти достаточно для большинства задач." if info.used_percent < 75 else "Память почти заполнена — расширение будет полезно.")
            ),
            command="allram status",
        )
    )
    if info.commit_limit and info.total_bytes:
        ratio = info.commit_limit / max(info.total_bytes, 1)
        if ratio < 1.4:
            recommendations.append(
                Recommendation(
                    title="Подкачка невелика",
                    body=(
                        f"Доступно лишь {util.human_bytes(info.commit_limit)} памяти с учётом подкачки "
                        f"({ratio:.1f}× от ОЗУ). Нейросети и виртуальные машины могут не запускаться."
                    ),
                    command="allram auto --apply",
                )
            )

    summary = {
        "machine": profile.as_dict(),
        "memory": info.as_dict(),
        "drives": [
            {"letter": letter, "free_bytes": util.free_space(f"{letter}:\\")} for letter in drives
        ],
        "recommendations": [rec.__dict__ for rec in recommendations],
    }
    return recommendations, summary


def status_report() -> dict[str, Any]:
    info = memory.memory_info()
    out: dict[str, Any] = {
        "memory": info.as_dict(),
        "cpu": (memory.cpu_brand_from_registry() or memory.machine_profile().cpu_name),
        "windows": util.windows_build(),
    }
    if util.is_windows():
        settings = registry.read_pagefile_settings()
        out["pagefile"] = {
            "automatic": settings.automatic,
            "paging_files": settings.paging_files,
            "existing_files": settings.existing_files,
            "allram_active": any("pagefile_allram.sys" in text.lower() for text in settings.paging_files),
        }
        out["physical_memory_registry"] = memory.physical_memory_from_registry()
    from . import imdisk

    ram = imdisk.status()
    out["ramdisk"] = {
        "installed": ram.installed,
        "driver_loaded": ram.driver_loaded,
        "devices": ram.devices,
        "notes": ram.notes,
    }
    return out


def explain_text() -> str:
    return (
        "Как это работает\n"
        "────────────────\n"
        "1. Windows не даёт «дописать» физическую ОЗУ в реестр: объём памяти определяет\n"
        "   ядро при загрузке, и значение в реестре — только снимок для отображения.\n"
        "2. Зато Windows официально умеет расширять память файлом подкачки на диске.\n"
        "   Сумма «ОЗУ + файл подкачки» называется commit limit — это тот объём,\n"
        "   который программы видят как доступную память.\n"
        "3. AllRAM создаёт такой файл на выбранном SSD и прописывает его в реестр\n"
        "   (HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management,\n"
        "   значения PagingFiles и AutomaticManagedPagefile). Настройка переживает\n"
        "   перезагрузку, работает для всех программ и для нейросетей.\n"
        "4. Скорость: ОЗУ — десятки ГБ/с, SSD — сотни МБ/с…несколько ГБ/с.\n"
        "   Поэтому расширенная память работает, но медленно. AllRAM умеет это измерить\n"
        "   командой `allram bench`.\n"
        "5. Второй режим — RAM-диск ImDisk: часть настоящей ОЗУ превращается в диск.\n"
        "   Он быстрый, но данные в нём не сохраняются после выключения.\n"
    )
