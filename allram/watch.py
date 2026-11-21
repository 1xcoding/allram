"""Наблюдатель: следит за памятью и при нехватке сообщает о состоянии подкачки."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from . import memory, pagefile, registry, util
from .console import Console


@dataclass
class WatchOptions:
    interval: float = 10.0
    threshold: float = 90.0
    once: bool = False
    json_output: bool = False
    max_runs: int = 0


def _collect(threshold: float) -> dict[str, object]:
    info = memory.memory_info()
    settings = registry.read_pagefile_settings() if util.is_windows() else None
    return {
        "time": util.local_now_str(),
        "used_percent": round(info.used_percent, 1),
        "used_bytes": info.used_bytes,
        "avail_bytes": info.avail_bytes,
        "commit_total": info.commit_total,
        "commit_limit": info.commit_limit,
        "threshold": threshold,
        "alert": info.used_percent >= threshold,
        "pagefile": settings.paging_files if settings else [],
        "pagefile_automatic": settings.automatic if settings else None,
    }


def watch(options: WatchOptions, console: Console) -> int:
    if not util.is_windows():
        console.warn("Наблюдатель рассчитан на Windows; на других ОС показывается только статистика.")

    if not options.json_output:
        console.title("AllRAM — наблюдение за памятью")
        console.muted(
            f"порог {options.threshold:.0f} %, опрос каждые {options.interval:g} с, "
            f"Ctrl+C для остановки, данные: {util.data_dir()}"
        )

    iterations = 0
    alerted = False
    try:
        while True:
            snapshot = _collect(options.threshold)
            iterations += 1
            if options.json_output:
                import json

                print(json.dumps(snapshot, ensure_ascii=False), flush=True)
            else:
                used = float(snapshot["used_percent"])
                bar_len = 32
                filled = int(round(bar_len * used / 100))
                bar = "█" * filled + "░" * (bar_len - filled)
                color = console.color
                line = (
                    f"[{snapshot['time']}] {bar} {used:5.1f} %  "
                    f"свободно {util.human_bytes(int(snapshot['avail_bytes']))}"
                )
                if used >= options.threshold:
                    console.print(line, "\033[31m" if color else "")
                elif used >= options.threshold - 15:
                    console.print(line, "\033[33m" if color else "")
                else:
                    console.print(line, "\033[32m" if color else "")
                if snapshot["alert"] and not alerted:
                    alerted = True
                    auto = snapshot["pagefile_automatic"]
                    files = snapshot["pagefile"] or []
                    if auto:
                        console.warn("  Память почти заполнена, а подкачкой управляет Windows автоматически.")
                    elif not files:
                        console.warn("  Память почти заполнена, файла подкачки нет — приложения могут закрываться.")
                    else:
                        console.muted("  Память почти заполнена: используется расширение на SSD, это медленнее ОЗУ.")
                elif not snapshot["alert"]:
                    alerted = False

            if options.once or (options.max_runs and iterations >= options.max_runs):
                break
            time.sleep(max(0.5, options.interval))
    except KeyboardInterrupt:
        if not options.json_output:
            console.print()
            console.muted("остановлено пользователем")
        return 130
    return 0
