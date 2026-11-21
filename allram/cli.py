"""Командная строка AllRAM. Только терминал, без интерфейса.

Примеры:
    allram            — интерактивное меню
    allram status     — что видит система
    allram auto       — подобрать настройки и применить
    allram setup -d D -s 64G --apply
    allram ramdisk -l R -s 4G
    allram bench      — сравнить ОЗУ и SSD
    allram explain    — как это работает
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Any, Callable

from . import __version__, benchmark, imdisk, installer, memory, pagefile, presets, registry, report, util
from .console import Aborted, Console, ask, ask_yes_no, configure_stdio, enable_ansi
from .state import StateStore
from .watch import WatchOptions, watch

ConsoleLike = Console


# ---------------------------------------------------------------------------
# Общие помощники
# ---------------------------------------------------------------------------


def _emit_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _need_windows(console: Console, action: str) -> bool:
    if util.is_windows():
        return True
    console.error(f"{action} доступно только в Windows.")
    console.muted("Запустите AllRAM на Windows — остальные команды работают и здесь.")
    return False


def _need_admin(console: Console, action: str) -> bool:
    if util.is_admin():
        return True
    console.error(f"Для действия «{action}» нужны права администратора.")
    console.muted("Закройте AllRAM и запустите его правой кнопкой → «Запуск от имени администратора».")
    return False


def _describe_memory(info: memory.MemoryInfo) -> str:
    return (
        f"ОЗУ {util.human_bytes(info.total_bytes)} · "
        f"занято {info.used_percent:.0f} % · "
        f"всего с подкачкой {util.human_bytes(info.commit_limit)}"
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def render_status(console: Console, data: dict[str, Any]) -> None:
    mem = data.get("memory", {})
    console.title("AllRAM — состояние")
    console.table(
        [
            ("Оперативная память", util.human_bytes(mem.get("total_bytes"))),
            ("Занято", f"{mem.get('used_percent', 0):.0f} %  ({util.human_bytes(mem.get('used_bytes'))})"),
            ("Свободно ОЗУ", util.human_bytes(mem.get("avail_bytes"))),
            ("Файл подкачки сейчас", util.human_bytes(mem.get("commit_total"))),
            ("Всего памяти для программ", util.human_bytes(mem.get("commit_limit"))),
        ]
    )
    if data.get("cpu"):
        console.muted(f"процессор: {data['cpu']}")
    if data.get("windows"):
        console.muted(f"система: {data['windows']}")

    page = data.get("pagefile")
    console.section("Подкачка (расширение памяти на SSD)")
    if page:
        if page.get("automatic") is None:
            console.print("  режим: неизвестен")
        elif page.get("automatic"):
            console.print("  режим: Windows управляет автоматически")
        else:
            console.print("  режим: задан вручную")
        if page.get("allram_active"):
            console.warn("  AllRAM уже расширил память файлом подкачки")
        files = page.get("paging_files") or []
        if not files:
            console.print("  файлы: нет (подкачка отключена)")
        else:
            for item in files:
                entry = registry.PagefileEntry.parse(str(item))
                console.bullet(
                    f"{entry.path} — {entry.initial_mb} МиБ"
                    if entry.initial_mb
                    else entry.path
                )
        existing = page.get("existing_files") or []
        if existing:
            console.muted("  действуют сейчас: " + ", ".join(str(x) for x in existing))
    else:
        console.muted("  доступно только в Windows")

    ram = data.get("ramdisk", {})
    console.section("RAM-диск (ImDisk)")
    if ram.get("installed"):
        console.print("  ImDisk: установлен" + (" · драйвер активен" if ram.get("driver_loaded") else " · драйвер не загружен"))
        devices = ram.get("devices") or []
        for device in devices:
            console.bullet(f"{device.get('letter')}: — {device.get('size')} ({device.get('type') or 'RAM'})")
        if not devices:
            console.muted("  RAM-дисков пока нет")
    else:
        console.muted("  ImDisk не установлен (allram ramdisk --install-imdisk)")
    for note in ram.get("notes") or []:
        if "ImDisk не установлен" in str(note):
            continue  # уже сказали строкой выше
        console.muted("  " + str(note))

    registry_ram = data.get("physical_memory_registry")
    console.section("Реестр")
    if registry_ram:
        console.print(f"  HARDWARE\\RESOURCEMAP: ОЗУ в системе — {util.human_bytes(registry_ram)}")
    console.muted("  запись PagingFiles: HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management")


def cmd_status(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    data = report.status_report()
    threshold = getattr(args, "threshold", None)
    over_threshold = False
    if threshold is not None:
        used = float(data.get("memory", {}).get("used_percent") or 0.0)
        over_threshold = used >= float(threshold)

    if getattr(args, "json", False):
        _emit_json(data)
    else:
        render_status(console, data)
        if threshold is not None and over_threshold:
            console.error(f"Память загружена на {data['memory']['used_percent']:.0f} % — выше порога {threshold} %")
    return 2 if over_threshold else 0


# ---------------------------------------------------------------------------
# setup / auto
# ---------------------------------------------------------------------------


def _print_plan(console: Console, plan: pagefile.PagefilePlan, info: report.PlanReport) -> None:
    console.section("План")
    console.table(
        [
            ("Диск", f"{plan.drive}: (свободно {util.human_bytes(info.free_bytes)})"),
            ("Файл подкачки", plan.path),
            ("Размер", f"{util.human_bytes(plan.size_bytes)} ({plan.initial_mb} МиБ)"),
            ("Было доступно памяти", util.human_bytes(info.commit_limit)),
            ("Станет доступно памяти", util.human_bytes(info.expected_commit_limit)),
        ]
    )
    for warning in plan.warnings:
        console.warn("  ! " + warning)
    console.muted("  Изменения вступят в силу после перезагрузки Windows.")
    console.muted("  Все программы увидят увеличенный объём; скорость будет ниже, чем у ОЗУ.")


def cmd_setup(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    if not _need_windows(console, "Настройка подкачки"):
        return 1

    drive = args.drive
    size_text = args.size
    if not drive or not size_text:
        info = memory.memory_info()
        drives = [letter for letter in util.list_drives() if letter not in ("A", "B")]
        if not drives:
            console.error("Не найдено ни одного диска.")
            return 1
        best = max(drives, key=lambda letter: util.free_space(f"{letter}:\\"))
        free = util.free_space(f"{best}:\\")
        suggested = pagefile.recommend_size_bytes(free, info.total_bytes)
        if not drive:
            drive = ask("Буква диска для файла подкачки", best)
        if not size_text:
            size_text = ask("Размер расширения памяти (например 64G)", f"{suggested // util.GIB}G")

    try:
        size_bytes = util.parse_size(size_text, default_unit="g")
        plan = pagefile.make_plan(drive, size_bytes, mode="custom")
    except (ValueError, pagefile.PagefileError) as exc:
        console.error(str(exc))
        return 1

    info = report.build_plan(plan.drive, plan.size_bytes)
    quiet = bool(getattr(args, "json", False))
    if not quiet:
        _print_plan(console, plan, info)

    if not args.apply and not args.yes:
        if args.dry_run:
            if quiet:
                _emit_json(info.as_dict())
            else:
                console.muted("Пробный прогон: реестр не изменяется.")
            return 0
        if not ask_yes_no("Применить настройки?", True):
            console.muted("Отменено.")
            return 1

    if not _need_admin(console, "запись в реестр"):
        return 1

    try:
        result = pagefile.apply_plan(plan, store, dry_run=args.dry_run)
    except (pagefile.PagefileError, registry.RegistryError) as exc:
        console.error(str(exc))
        return 1

    store.update_config(
        last_drive=plan.drive,
        last_size_bytes=plan.size_bytes,
        last_mode="pagefile",
        pagefile_min_mb=plan.initial_mb,
        pagefile_max_mb=plan.maximum_mb,
        applied_at=util.now_iso(),
    )
    store.add_history(
        "pagefile",
        f"расширение памяти на {util.human_bytes(plan.size_bytes)} через {plan.path}",
        {"plan": plan.as_dict(), "result": result.message},
    )
    registry.remember("pagefile", {"drive": plan.drive, "size_mb": plan.initial_mb, "path": plan.path})

    if quiet:
        _emit_json({"plan": plan.as_dict(), "result": asdict(result)})
        return 0 if result.success else 2
    console.print()
    if result.success:
        console.ok("Готово: " + result.message)
    else:
        console.warn(result.message)
    for line in result.details:
        console.muted("  " + line)
    if result.reboot_required:
        console.warn("Перезагрузите Windows, чтобы память увеличилась.")
    return 0 if result.success else 2


def cmd_auto(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    if not _need_windows(console, "Автоподбор"):
        return 1

    info = memory.memory_info()
    drives = [letter for letter in util.list_drives() if letter not in ("A", "B")]
    if not drives:
        console.error("Не найдено ни одного диска.")
        return 1
    letter = max(drives, key=lambda drive: util.free_space(f"{drive}:\\"))
    free = util.free_space(f"{letter}:\\")
    preset_key = args.preset or "ai"
    preset = presets.get(preset_key)
    if preset is None:
        console.error(f"Неизвестный профиль {preset_key!r}. Доступно: {', '.join(presets.PRESETS)}")
        return 1
    size = preset.target_bytes(free)
    console.info(f"Профиль «{preset.title}»: диск {letter}:, {util.human_bytes(size)}")
    for note in preset.notes:
        console.muted("  · " + note)

    plan = pagefile.make_plan(letter, size, mode="ideal")
    plan_info = report.build_plan(letter, plan.size_bytes)
    _print_plan(console, plan, plan_info)

    if not args.apply:
        console.muted("Чтобы применить: allram auto --apply")
        if args.json:
            _emit_json(plan_info.as_dict())
        return 0

    if not _need_admin(console, "запись в реестр"):
        return 1
    try:
        result = pagefile.apply_plan(plan, store)
    except (pagefile.PagefileError, registry.RegistryError) as exc:
        console.error(str(exc))
        return 1
    store.update_config(
        last_drive=plan.drive,
        last_size_bytes=plan.size_bytes,
        last_mode="pagefile",
        preset=preset.key,
        pagefile_min_mb=plan.initial_mb,
        pagefile_max_mb=plan.maximum_mb,
        applied_at=util.now_iso(),
    )
    store.add_history("pagefile", f"профиль {preset.key}: +{util.human_bytes(plan.size_bytes)}", {"plan": plan.as_dict()})
    console.print()
    if result.success:
        console.ok("Готово: " + result.message)
    else:
        console.warn(result.message)
    console.warn("Перезагрузите Windows.")
    return 0 if result.success else 2


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------


def cmd_revert(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    if not _need_windows(console, "Откат подкачки"):
        return 1
    backup = store.latest_backup()
    if not backup:
        console.error("Резервных копий нет — откатывать нечего.")
        return 1
    console.info(f"Последняя резервная копия: {backup.get('created_at')} ({backup.get('kind')})")
    previous = backup.get("data", {}).get("pagefile", {})
    if previous.get("automatic"):
        console.muted("Вернём автоматическое управление подкачкой Windows.")
    else:
        console.muted("Вернём файлы подкачки: " + (", ".join(previous.get("paging_files") or []) or "нет"))
    if not args.yes and not ask_yes_no("Восстановить эти настройки?", True):
        console.muted("Отменено.")
        return 1
    if not _need_admin(console, "запись в реестр"):
        return 1
    try:
        pagefile.restore(store)
    except (pagefile.PagefileError, registry.RegistryError) as exc:
        console.error(str(exc))
        return 1
    console.ok("Настройки подкачки восстановлены. Перезагрузите Windows.")
    return 0


# ---------------------------------------------------------------------------
# ramdisk
# ---------------------------------------------------------------------------


def cmd_ramdisk(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    if not _need_windows(console, "RAM-диск"):
        return 1
    status = imdisk.status()

    if args.install_imdisk:
        if not _need_admin(console, "установка ImDisk"):
            return 1
        console.info("Скачиваю официальный установщик ImDisk…")
        try:
            result = imdisk.install_imdisk()
        except (imdisk.RamdiskError, OSError) as exc:
            console.error(str(exc))
            return 1
        console.ok(str(result.get("message") or "Готово"))
        return 0

    if args.list or (not args.size and not args.remove):
        console.title("AllRAM — RAM-диск")
        if not status.installed:
            console.warn("ImDisk не установлен.")
            console.muted("Установить: allram ramdisk --install-imdisk")
            console.muted("Подробнее: " + imdisk.IMDISK_DOC_URL)
            return 1
        if not status.driver_loaded:
            console.warn("Драйвер ImDisk не активен — перезагрузите Windows.")
        devices = status.devices
        if not devices:
            console.muted("RAM-дисков нет.")
        else:
            console.table(
                [(f"{d.get('letter')}:", d.get("size", ""), d.get("type", "")) for d in devices],
                header=("Диск", "Размер", "Тип"),
            )
        console.muted("Создать: allram ramdisk -l R -s 4G")
        return 0

    if args.remove:
        letter = str(args.remove)
        if not args.yes and not ask_yes_no(f"Удалить RAM-диск {letter}:?", False):
            console.muted("Отменено.")
            return 1
        if not _need_admin(console, "удаление RAM-диска"):
            return 1
        try:
            result = imdisk.remove_ramdisk(letter)
        except imdisk.RamdiskError as exc:
            console.error(str(exc))
            return 1
        console.ok(result.message)
        return 0

    # создание
    if not status.installed:
        console.warn("ImDisk не установлен.")
        if not args.yes and ask_yes_no("Скачать и установить ImDisk сейчас?", True):
            if not _need_admin(console, "установка ImDisk"):
                return 1
            try:
                imdisk.install_imdisk()
            except imdisk.RamdiskError as exc:
                console.error(str(exc))
                return 1
            console.ok("ImDisk установлен.")
        else:
            return 1
    if not _need_admin(console, "создание RAM-диска"):
        return 1

    letter = util.normalize_drive_letter(args.letter or store.load_config().ramdisk_letter or "R")
    try:
        size_bytes = util.parse_size(args.size, default_unit="g")
    except ValueError as exc:
        console.error(str(exc))
        return 1

    info = memory.memory_info()
    console.section("RAM-диск")
    console.table(
        [
            ("Буква", f"{letter}:"),
            ("Размер", util.human_bytes(size_bytes)),
            ("Свободно ОЗУ", util.human_bytes(info.avail_bytes)),
        ]
    )
    if size_bytes > info.avail_bytes * 0.8:
        console.warn("  RAM-диск займёт почти всю свободную память — Windows может начать тормозить.")
    if not args.yes and not ask_yes_no("Создать RAM-диск?", True):
        console.muted("Отменено.")
        return 1
    try:
        result = imdisk.create_ramdisk(letter, size_bytes)
    except imdisk.RamdiskError as exc:
        console.error(str(exc))
        return 1
    console.ok(result.message)
    for line in result.details:
        console.muted("  " + line)
    store.update_config(ramdisk_letter=letter, ramdisk_size_bytes=size_bytes)
    store.add_history("ramdisk", f"создан RAM-диск {letter}: на {util.human_bytes(size_bytes)}", {})

    if args.temp:
        try:
            temp = imdisk.set_temp_to_ramdisk(letter)
            console.ok(f"TEMP/TMP переключены на {temp['temp']}")
        except imdisk.RamdiskError as exc:
            console.warn(str(exc))
    return 0


# ---------------------------------------------------------------------------
# benchmark / models
# ---------------------------------------------------------------------------


def cmd_bench(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    drive = args.drive
    console.title("AllRAM — скорость памяти")
    console.muted("Замер ОЗУ и диска… это займёт несколько секунд")
    result = benchmark.run(drive, ram_mib=args.ram_mib, disk_mib=args.disk_mib)
    data = result.as_dict()
    if args.json:
        _emit_json(data)
        return 0
    console.table(
        [
            ("ОЗУ — запись", f"{data['ram_write_mbps']:,.1f} МиБ/с"),
            ("ОЗУ — чтение", f"{data['ram_read_mbps']:,.1f} МиБ/с"),
            ("ОЗУ — задержка", f"{data['ram_latency_ns']:.1f} нс"),
            ("Диск — запись", f"{data['disk_write_mbps']:,.1f} МиБ/с"),
            ("Диск — чтение", f"{data['disk_read_mbps']:,.1f} МиБ/с"),
        ]
    )
    if result.disk_used and result.slowdown:
        console.print()
        console.info(f"ОЗУ быстрее диска примерно в {result.slowdown:.0f}× — это и есть «медленно, но работает».")
    for note in result.notes:
        console.muted("  " + note)
    store.add_history("bench", "замер скорости", data)
    return 0


def cmd_models(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    info = memory.memory_info()
    bench = benchmark.run(ram_mib=48, disk_mib=16)
    capacity = info.commit_limit if info.commit_limit > info.total_bytes else info.total_bytes
    console.title("AllRAM — оценка для нейросетей")
    if info.commit_limit > info.total_bytes:
        console.muted(
            f"доступно памяти: ОЗУ {util.human_bytes(info.total_bytes)}, "
            f"всего с подкачкой {util.human_bytes(info.commit_limit)}"
        )
    else:
        console.muted(
            f"доступно памяти: {util.human_bytes(capacity)} (Windows не сообщает объём подкачки — "
            "после allram setup здесь появится суммарный объём)"
        )
    rows = []
    for name, size, fits in presets.models_table(capacity):
        rows.append((name, util.human_bytes(size), "поместится" if fits else "не поместится"))
    console.table(rows, header=("Модель", "Примерно нужно", "Поместится?"))
    if args.model:
        size = presets.model_size(str(args.model))
        if size is None:
            console.error(f"Не удалось определить размер модели {args.model!r}. Укажите параметры, например 13b.")
            return 1
        estimate = benchmark.estimate_model(size, bench, info.total_bytes, capacity)
        console.section(f"Модель {args.model}")
        console.table(
            [
                ("Размер (Q4)", util.human_bytes(size)),
                ("Загрузка в ОЗУ", util.format_seconds(estimate.ram_load_seconds)),
                ("Гибридная загрузка", util.format_seconds(estimate.hybrid_load_seconds)),
                ("Часть с SSD", util.human_bytes(estimate.spill_bytes)),
                ("Скорость генерации", estimate.tokens_per_second_hint),
            ]
        )
        for note in estimate.notes:
            console.muted("  " + note)
        if args.json:
            _emit_json(estimate.as_dict())
    console.muted("Оценки приблизительные: реальная скорость зависит от процессора, GPU и самой модели.")
    return 0


# ---------------------------------------------------------------------------
# explain / report / watch
# ---------------------------------------------------------------------------


def cmd_explain(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    text = report.explain_text()
    if args.json:
        _emit_json({"text": text})
        return 0
    console.title("AllRAM — как это работает")
    print(text)
    return 0


def cmd_recommend(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    recommendations, summary = report.recommendation_report()
    if args.json:
        _emit_json(summary)
        return 0
    console.title("AllRAM — рекомендации")
    for rec in recommendations:
        console.section(rec.title)
        print("  " + rec.body)
        if rec.command:
            console.print("  → " + rec.command, "\033[36m" if console.color else "")
    return 0


def cmd_watch(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    if not store.acquire_watch_lock() and not args.once:
        console.error("Наблюдатель AllRAM уже запущен (lock-файл занят).")
        console.muted(f"Если это ошибка, удалите {store.directory / 'watch.lock'}")
        return 1
    try:
        return watch(
            WatchOptions(
                interval=args.interval,
                threshold=args.threshold,
                once=args.once,
                json_output=args.json,
                max_runs=args.runs,
            ),
            console,
        )
    finally:
        store.release_watch_lock()


def cmd_install(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    result = installer.install(store, autostart=not args.no_autostart)
    if args.json:
        _emit_json(result)
        return 0 if result.get("success") else 1
    console.title("AllRAM — установка")
    for step in result.get("steps", []):
        console.bullet(str(step))
    if result.get("success"):
        console.ok(str(result.get("message")))
    else:
        console.error(str(result.get("message")))
    if not util.is_admin():
        console.warn("Запустите от администратора, чтобы настраивать подкачку и RAM-диск.")
    return 0 if result.get("success") else 1


def cmd_uninstall(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    result = installer.uninstall(store, remove_pagefile=args.restore_pagefile)
    if args.json:
        _emit_json(result)
        return 0
    console.title("AllRAM — удаление")
    for step in result.get("steps", []):
        console.bullet(str(step))
    console.ok(str(result.get("message")))
    return 0


def cmd_history(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    entries = store.history()
    if args.json:
        _emit_json(entries)
        return 0
    console.title("AllRAM — журнал")
    if not entries:
        console.muted("Пока пусто.")
        return 0
    for entry in reversed(entries[-args.limit :]):
        console.print(f"{entry.get('time', '')}  {entry.get('summary', '')}")
    return 0


def cmd_backup(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    path = util.data_dir() / "export.json"
    if args.output:
        path = util.Path(args.output) if hasattr(util, "Path") else path
    store.export_json(path)
    console.ok(f"Данные сохранены: {path}")
    return 0


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def cmd_settings(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    config = store.load_config()
    changes: dict[str, Any] = {}
    if args.autostart is not None:
        if not util.is_windows():
            console.error("Автозапуск настраивается только в Windows.")
            return 1
        registry.set_autostart(args.autostart)
        changes["autostart"] = args.autostart
    if args.default_drive:
        try:
            changes["last_drive"] = util.normalize_drive_letter(args.default_drive)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    if args.default_size:
        try:
            changes["last_size_bytes"] = util.parse_size(args.default_size)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    if args.ramdisk_letter:
        try:
            changes["ramdisk_letter"] = util.normalize_drive_letter(args.ramdisk_letter)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    quiet = bool(getattr(args, "json", False))
    if changes:
        config = store.update_config(**changes)
        if not quiet:
            console.ok("Сохранено: " + ", ".join(f"{key}={value}" for key, value in changes.items()))
    if quiet:
        _emit_json(config.as_dict())
        return 0
    console.title("AllRAM — настройки")
    console.table(
        [
            ("Диск по умолчанию", config.last_drive or "—"),
            ("Размер по умолчанию", util.human_bytes(config.last_size_bytes) if config.last_size_bytes else "—"),
            ("Режим", config.last_mode),
            ("Буква RAM-диска", config.ramdisk_letter),
            ("Размер RAM-диска", util.human_bytes(config.ramdisk_size_bytes) if config.ramdisk_size_bytes else "—"),
            ("Автозапуск", (registry.get_autostart() if util.is_windows() else None) or "выключен"),
            ("Каталог данных", str(store.directory)),
        ]
    )
    console.section("Профили")
    for preset in presets.list_presets():
        console.bullet(f"{preset.key:8s} {preset.title}")
    return 0


def cmd_presets(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    free = 0
    drives = [letter for letter in util.list_drives() if letter not in ("A", "B")] if util.is_windows() else []
    if drives:
        free = max(util.free_space(f"{letter}:\\") for letter in drives)
    rows = []
    if args.json:
        _emit_json([preset.__dict__ for preset in presets.list_presets()])
        return 0
    console.title("AllRAM — профили")
    console.table([(p.key, p.title, p.description) for p in presets.list_presets()], header=("Код", "Название", "Описание"))
    console.muted("Запуск: allram auto --preset <код> --apply")
    return 0


# ---------------------------------------------------------------------------
# Парсер
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="allram",
        description="AllRAM — используйте свободное место SSD как дополнительную память Windows.",
        epilog="Без аргументов запускается интерактивное меню. Подробнее: allram explain",
    )
    parser.add_argument("--version", action="version", version=f"AllRAM {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="вывести машинно-читаемый JSON")
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="отключить цвета")
    common.add_argument("--lang", default=argparse.SUPPRESS, help="язык интерфейса (пока поддержан ru)")
    common.add_argument(
        "--elevate",
        action="store_true",
        default=argparse.SUPPRESS,
        help="перезапустить AllRAM с правами администратора (UAC)",
    )

    sub = parser.add_subparsers(dest="command")

    def add(name: str, func: Callable[..., int], help_text: str, parents=None) -> argparse.ArgumentParser:
        sp = sub.add_parser(
            name, help=help_text, description=help_text, parents=[common], conflict_handler="resolve"
        )
        sp.set_defaults(func=func)
        return sp

    p_status = add("status", cmd_status, "показать память, подкачку и RAM-диск")
    p_status.add_argument("--threshold", type=float, default=None, help="код возврата 2, если занято больше N %%")
    
    p_setup = add("setup", cmd_setup, "настроить файл подкачки на SSD")
    p_setup.add_argument("-d", "--drive", help="буква диска, например D")
    p_setup.add_argument("-s", "--size", help="размер, например 64G")
    p_setup.add_argument("--apply", action="store_true", help="применить без вопроса")
    p_setup.add_argument("--yes", "-y", action="store_true", help="не задавать вопросов")
    p_setup.add_argument("--dry-run", action="store_true", help="только показать план")

    p_auto = add("auto", cmd_auto, "подобрать размер автоматически (профили)")
    p_auto.add_argument("--preset", default="ai", help="профиль: ai, browser, games, max, ramdisk")
    p_auto.add_argument("--apply", action="store_true", help="применить сразу")
    p_auto.add_argument("--yes", "-y", action="store_true", help="не задавать вопросов")

    p_revert = add("revert", cmd_revert, "вернуть прежние настройки подкачки")
    p_revert.add_argument("--yes", "-y", action="store_true", help="не задавать вопросов")

    p_ram = add("ramdisk", cmd_ramdisk, "RAM-диск в настоящей ОЗУ (ImDisk)")
    p_ram.add_argument("-l", "--letter", help="буква диска, по умолчанию R")
    p_ram.add_argument("-s", "--size", help="размер, например 8G")
    p_ram.add_argument("--temp", action="store_true", help="переключить TEMP/TMP на RAM-диск")
    p_ram.add_argument("--remove", metavar="LETTER", help="удалить RAM-диск")
    p_ram.add_argument("--list", action="store_true", help="список RAM-дисков")
    p_ram.add_argument("--install-imdisk", action="store_true", help="скачать и установить ImDisk")
    p_ram.add_argument("--yes", "-y", action="store_true", help="не задавать вопросов")

    p_bench = add("bench", cmd_bench, "сравнить скорость ОЗУ и SSD")
    p_bench.add_argument("-d", "--drive", help="диск для теста (по умолчанию системный)")
    p_bench.add_argument("--ram-mib", type=int, default=64, help="объём теста ОЗУ, МиБ")
    p_bench.add_argument("--disk-mib", type=int, default=32, help="объём теста диска, МиБ")

    p_models = add("models", cmd_models, "оценка, какие нейросети поместятся")
    p_models.add_argument("--model", help="имя модели или число параметров, например 13b")

    add("explain", cmd_explain, "объяснить, как работает расширение памяти")
    add("recommend", cmd_recommend, "рекомендации для этого компьютера")
    add("presets", cmd_presets, "список профилей")

    p_watch = add("watch", cmd_watch, "следить за памятью в реальном времени")
    p_watch.add_argument("--interval", type=float, default=10.0, help="секунд между опросами")
    p_watch.add_argument("--threshold", type=float, default=90.0, help="порог предупреждения, %%")
    p_watch.add_argument("--once", action="store_true", help="только один замер")
    p_watch.add_argument("--runs", type=int, default=0, help="ограничить количество опросов")
    
    p_install = add("install", cmd_install, "установить автозапуск и метку в реестре")
    p_install.add_argument("--no-autostart", action="store_true", help="не добавлять в автозапуск")

    p_uninstall = add("uninstall", cmd_uninstall, "удалить автозапуск AllRAM")
    p_uninstall.add_argument("--restore-pagefile", action="store_true", help="вернуть настройки подкачки")

    p_settings = add("settings", cmd_settings, "показать или изменить настройки")
    p_settings.add_argument("--autostart", dest="autostart", action="store_true", default=None)
    p_settings.add_argument("--no-autostart", dest="autostart", action="store_false")
    p_settings.add_argument("--default-drive", help="диск по умолчанию")
    p_settings.add_argument("--default-size", help="размер по умолчанию")
    p_settings.add_argument("--ramdisk-letter", help="буква RAM-диска")

    p_history = add("history", cmd_history, "журнал операций")
    p_history.add_argument("--limit", type=int, default=20)

    p_backup = add("backup", cmd_backup, "экспорт настроек и журнала в JSON")
    p_backup.add_argument("-o", "--output", help="куда сохранить (по умолчанию export.json)")

    return parser


def _dispatch(args: argparse.Namespace, console: Console, store: StateStore) -> int:
    func = getattr(args, "func", None)
    if func is None:
        return interactive(console, store)
    try:
        return int(func(args, console, store))
    except Aborted:
        console.print()
        console.muted("Отменено.")
        return 130
    except KeyboardInterrupt:
        console.print()
        console.muted("Прервано пользователем.")
        return 130
    except (pagefile.PagefileError, imdisk.RamdiskError, registry.RegistryError, PermissionError) as exc:
        console.error(str(exc))
        return 1


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------


def interactive(console: Console, store: StateStore) -> int:
    console.banner(f"v{__version__}", "SSD → RAM для Windows · только терминал")
    if not util.is_windows():
        console.warn("Сейчас запущена не Windows: доступны команды status, bench, explain.")
    elif not util.is_admin():
        console.warn("Нет прав администратора: настройка подкачки и RAM-диска будет недоступна.")
        console.muted("Можно перезапуститься с правами администратора прямо сейчас.")
        interactive_stdin = True
        try:
            interactive_stdin = sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            interactive_stdin = False
        try:
            if interactive_stdin and ask_yes_no("Перезапустить AllRAM от имени администратора?", True):
                if util.relaunch_elevated(sys.argv[1:]):
                    return 0
                console.error("UAC отменён — продолжаю без прав администратора.")
        except Aborted:
            pass

    while True:
        info = memory.memory_info()
        console.print()
        console.print("  " + _describe_memory(info), "\033[36m" if console.color else "")
        options = [
            "Состояние памяти и подкачки",
            "Расширить память на SSD (автоподбор)",
            "Расширить память на SSD (вручную)",
            "Сравнить скорость ОЗУ и SSD",
            "Какие нейросети поместятся",
            "RAM-диск ImDisk",
            "Вернуть прежние настройки подкачки",
            "Настройки и журнал",
            "Как это работает и выход",
        ]
        try:
            index = _menu(console, options)
        except Aborted:
            console.print()
            return 0
        console.print()
        if index == 0:
            render_status(console, report.status_report())
        elif index == 1:
            _run_menu(console, store, cmd_auto, argparse.Namespace(preset="ai", apply=True, yes=True))
        elif index == 2:
            _run_menu(console, store, cmd_setup, argparse.Namespace(drive=None, size=None, apply=False, yes=True, dry_run=False))
        elif index == 3:
            _run_menu(console, store, cmd_bench, argparse.Namespace(drive=None, ram_mib=64, disk_mib=32, json=False))
        elif index == 4:
            _run_menu(console, store, cmd_models, argparse.Namespace(model=None, json=False))
        elif index == 5:
            _ramdisk_menu(console, store)
        elif index == 6:
            _run_menu(console, store, cmd_revert, argparse.Namespace(yes=True))
        elif index == 7:
            _settings_menu(console, store)
        else:
            print(report.explain_text())
            console.muted("До встречи!")
            return 0


def _menu(console: Console, options: list[str]) -> int:
    console.print("  Выберите действие:", "\033[1m" if console.color else "")
    for index, option in enumerate(options, start=1):
        console.print(f"   {index}) {option}")
    while True:
        raw = ask("Номер (Enter — выход)", allow_empty=True)
        if not raw:
            return len(options) - 1
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        console.warn(f"Введите число от 1 до {len(options)}.")


def _run_menu(console: Console, store: StateStore, func: Callable[..., int], args: argparse.Namespace) -> None:
    try:
        func(args, console, store)
    except (pagefile.PagefileError, imdisk.RamdiskError, registry.RegistryError) as exc:
        console.error(str(exc))
    except Aborted:
        console.muted("Отменено.")


def _ramdisk_menu(console: Console, store: StateStore) -> None:
    status = imdisk.status()
    console.title("RAM-диск ImDisk")
    if not status.installed:
        console.warn("ImDisk не установлен (свободный драйвер RAM-диска).")
        if ask_yes_no("Скачать и установить?", True):
            _run_menu(console, store, cmd_ramdisk, argparse.Namespace(install_imdisk=True))
        return
    devices = status.devices
    if devices:
        console.table([(f"{d['letter']}:", str(d.get("size")), str(d.get("type"))) for d in devices])
    else:
        console.muted("RAM-дисков нет.")
    options = ["Создать RAM-диск", "Удалить RAM-диск", "Назад"]
    index = _menu(console, options)
    if index == 0:
        letter = ask("Буква", store.load_config().ramdisk_letter or "R")
        size = ask("Размер", "4G")
        _run_menu(
            console,
            store,
            cmd_ramdisk,
            argparse.Namespace(
                letter=letter,
                size=size,
                temp=False,
                remove=None,
                list=False,
                install_imdisk=False,
                yes=False,
            ),
        )
    elif index == 1:
        letter = ask("Какую букву удалить", devices[0]["letter"] if devices else "R")
        _run_menu(
            console,
            store,
            cmd_ramdisk,
            argparse.Namespace(
                letter=None,
                size=None,
                temp=False,
                remove=letter,
                list=False,
                install_imdisk=False,
                yes=False,
            ),
        )


def _settings_menu(console: Console, store: StateStore) -> None:
    config = store.load_config()
    console.title("Настройки и журнал")
    console.table(
        [
            ("Диск по умолчанию", config.last_drive or "—"),
            ("Каталог данных", str(store.directory)),
            ("Автозапуск", (registry.get_autostart() if util.is_windows() else None) or "выключен"),
        ]
    )
    if ask_yes_no("Открыть журнал операций?", False):
        _run_menu(console, store, cmd_history, argparse.Namespace(limit=20))


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "elevate", False):
        rest = [item for item in argv if item != "--elevate"]
        if util.relaunch_elevated(rest):
            print("AllRAM запущен в новом окне с правами администратора.")
            return 0
        print("Не удалось получить права администратора (UAC отменён?).")
        return 1

    configure_stdio()
    enable_ansi()
    color = None
    if getattr(args, "no_color", False) or os.environ.get("NO_COLOR"):
        color = False
    console = Console(color=color)
    store = StateStore()
    # Глобальные флаги, заданные после подкоманды, argparse не сохраняет в
    # Namespace, если атрибут объявлен с SUPPRESS — подставляем значения по умолчанию.
    for name, default in (("json", False), ("no_color", False), ("lang", None)):
        if not hasattr(args, name):
            setattr(args, name, default)
    return _dispatch(args, console, store)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
