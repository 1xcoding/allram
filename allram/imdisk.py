"""RAM-диск на базе ImDisk (свободный драйвер, LTRData/ImDisk).

Зачем это нужно:
  • RAM-диск живёт в физической памяти и работает в разы быстрее SSD;
  • на него можно положить временные файлы, кэши, Scratch-каталог нейросетей;
  • AllRAM сам скачивает официальный ImDisk и устанавливает его.

Если ImDisk не установлен, AllRAM не ломается: команда `ramdisk` объяснит,
как включить RAM-диск, а `pagefile` работает независимо от драйвера.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import memory, registry, util

IMDISK_INSTALLER_URL = "https://www.ltr-data.se/files/imdiskinst.exe"
IMDISK_ZIP_URL = "https://www.ltr-data.se/files/imdisk.zip"
IMDISK_DOC_URL = "https://github.com/LTRData/ImDisk"
IMDISK_DEFAULT_PROGRAM = r"C:\Program Files\ImDisk\imdisk.exe"
IMDISK_TOOLKIT_PROGRAM = r"C:\Program Files\ImDisk Toolkit\imdisk.exe"

DOWNLOAD_TIMEOUT = 60


class RamdiskError(RuntimeError):
    pass


@dataclass
class ImDiskStatus:
    installed: bool
    driver_loaded: bool
    executable: str | None
    version: str | None = None
    devices: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class RamdiskResult:
    success: bool
    message: str
    letter: str = ""
    size_bytes: int = 0
    details: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Обнаружение
# ---------------------------------------------------------------------------


def find_executable() -> str | None:
    """Ищет imdisk.exe в PATH, Program Files и каталоге данных AllRAM."""
    if not util.is_windows():
        return None
    found = util.which("imdisk") or util.which("imdisk.exe")
    if found:
        return found
    candidates = [
        IMDISK_DEFAULT_PROGRAM,
        IMDISK_TOOLKIT_PROGRAM,
        str(util.data_dir() / "imdisk" / "imdisk.exe"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def driver_loaded() -> bool:
    """Проверяет, запущена ли служба драйвера ImDisk."""
    if not util.is_windows():
        return False
    if registry.imdisk_services():
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            "\\\\.\\ImDiskCtl", 0x80000000, 0x00000001 | 0x00000002, None, 3, 0, None
        )
        if handle == -1:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:  # pragma: no cover
        return False


def status() -> ImDiskStatus:
    executable = find_executable()
    services = registry.imdisk_services()
    result = ImDiskStatus(
        installed=bool(executable) or bool(services),
        driver_loaded=driver_loaded(),
        executable=executable,
        version=installed_version(),
        devices=[],
    )
    if not util.is_windows():
        result.notes.append("RAM-диск доступен только в Windows")
    elif not result.installed:
        result.notes.append("ImDisk не установлен — выполните: allram ramdisk --install-imdisk")
    elif not result.driver_loaded:
        result.notes.append("драйвер ImDisk установлен, но не загружен — нужна перезагрузка")
    if result.installed:
        result.devices = list_devices()
    return result


def installed_version() -> str | None:
    if not util.is_windows():
        return None
    for name in registry.imdisk_services():
        value = registry.read_value(
            __import__("winreg").HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{name}",
            "ImagePath",
        )
        if value:
            return name
    return None


def _run(args: list[str], *, timeout: float = 60.0) -> util.CommandResult:
    executable = find_executable()
    if not executable:
        raise RamdiskError("imdisk.exe не найден — установите ImDisk командой allram ramdisk --install-imdisk")
    return util.run_command([executable, *args], timeout=timeout)


def list_devices() -> list[dict[str, Any]]:
    if not util.is_windows() or not find_executable():
        return []
    result = _run(["-l"])
    if not result.ok:
        return []
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("---"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key in {"drive letter", "size", "device", "type", "image file"}:
                current[key] = value
                continue
        if current:
            devices.append(_normalize_device(current))
            current = {}
    if current:
        devices.append(_normalize_device(current))
    return devices


def _normalize_device(raw: dict[str, Any]) -> dict[str, Any]:
    size = raw.get("size", "")
    return {
        "letter": raw.get("drive letter", "").rstrip(":"),
        "size": size,
        "size_bytes": _parse_imdisk_size(size),
        "device": raw.get("device", ""),
        "type": raw.get("type", ""),
    }


def _parse_imdisk_size(text: str) -> int:
    match = re.search(r"([\d.,]+)\s*(TB|GB|MB|KB|T|G|M|K)", text or "", re.IGNORECASE)
    if not match:
        return 0
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return 0
    factor = {
        "t": util.TIB,
        "tb": util.TIB,
        "g": util.GIB,
        "gb": util.GIB,
        "m": util.MIB,
        "mb": util.MIB,
        "k": util.KIB,
        "kb": util.KIB,
    }[match.group(2).lower()]
    return int(value * factor)


def ramdisk_letters() -> list[str]:
    letters = []
    for device in list_devices():
        letter = device.get("letter")
        if letter and letter not in letters:
            letters.append(letter)
    return letters


# ---------------------------------------------------------------------------
# Установка драйвера
# ---------------------------------------------------------------------------


def download_imdisk(target_dir: Path | None = None) -> Path:
    """Скачивает официальный установщик ImDisk и возвращает путь к файлу."""
    if not util.is_windows():
        raise RamdiskError("ImDisk устанавливается только в Windows")
    target_dir = target_dir or (util.ensure_data_dir() / "downloads")
    target_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[tuple[str, str]] = [
        (IMDISK_INSTALLER_URL, "imdiskinst.exe"),
        (IMDISK_ZIP_URL, "imdisk.zip"),
    ]
    errors: list[str] = []
    for url, filename in attempts:
        destination = target_dir / filename
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AllRAM/1.0"})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
                data = response.read()
            if len(data) < 50_000:
                errors.append(f"{url}: слишком маленький файл ({len(data)} Б)")
                continue
            destination.write_bytes(data)
            return destination
        except Exception as exc:  # noqa: BLE001 - сообщаем пользователю все попытки
            errors.append(f"{url}: {exc}")
    raise RamdiskError(
        "не удалось скачать ImDisk. Скачайте вручную: " + IMDISK_DOC_URL + "\n  " + "\n  ".join(errors)
    )


def install_imdisk(*, silent: bool = True) -> dict[str, Any]:
    """Скачивает и ставит ImDisk. Требует прав администратора."""
    if not util.is_windows():
        raise RamdiskError("установка ImDisk доступна только в Windows")
    if not util.is_admin():
        raise RamdiskError("для установки драйвера нужны права администратора")

    existing = find_executable()
    if existing:
        return {"success": True, "message": f"ImDisk уже установлен: {existing}", "already": True}

    package = download_imdisk()
    if package.suffix.lower() == ".zip":
        extract_dir = package.parent / "imdisk_extracted"
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(package) as archive:
            archive.extractall(extract_dir)
        candidates = list(extract_dir.glob("*.exe"))
        if not candidates:
            raise RamdiskError("в архиве ImDisk не найден установщик")
        package.uninstall_hint = "удалите каталог ImDisk вручную"  # type: ignore[attr-defined]
        package = candidates[0]

    args = [str(package)]
    if silent:
        args.append("/S")
    result = util.run_command(args, timeout=300)
    if not result.ok:
        raise RamdiskError(
            "установщик ImDisk завершился с ошибкой: "
            + (result.output or f"код {result.returncode}")
        )
    return {
        "success": True,
        "message": "ImDisk установлен. Если драйвер ещё не активен — перезагрузите Windows.",
        "package": str(package),
        "output": result.output,
    }


# ---------------------------------------------------------------------------
# Создание и удаление RAM-диска
# ---------------------------------------------------------------------------


def create_ramdisk(
    letter: str,
    size_bytes: int,
    *,
    format_fs: str = "ntfs",
    label: str = "AllRAM",
    persistent: bool = True,
) -> RamdiskResult:
    """Создаёт RAM-диск указанного размера.

    Используется команда: imdisk -a -t vm -s <размер> -m X: -p "/fs:ntfs /q /y" -P
    -t vm  — виртуальный диск в оперативной памяти;
    -p     — отформатировать после создания;
    -P     — сохранить настройку в реестре, чтобы диск пересоздавался при загрузке.
    """
    if not util.is_windows():
        raise RamdiskError("RAM-диск доступен только в Windows")
    if not util.is_admin():
        raise RamdiskError("для создания RAM-диска нужны права администратора")

    letter = util.normalize_drive_letter(letter)
    if util.drive_exists(letter):
        raise RamdiskError(f"буква {letter}: уже занята другим диском")

    current = memory.memory_info()
    if size_bytes > max(512 * util.MIB, int(current.avail_bytes * 0.8)):
        raise RamdiskError(
            f"недостаточно свободной оперативной памяти: доступно "
            f"{util.human_bytes(current.avail_bytes)}, запрошено {util.human_bytes(size_bytes)}. "
            "Оставьте Windows хотя бы 4 ГиБ."
        )

    size_arg = f"{size_bytes // util.MIB}M"
    args = [
        "-a",
        "-t",
        "vm",
        "-s",
        size_arg,
        "-m",
        f"{letter}:",
        "-p",
        f"/fs:{format_fs} /q /y /v:{label}",
    ]
    if persistent:
        args.append("-P")

    result = _run(args, timeout=300)
    details = [line for line in result.output.splitlines() if line.strip()][:6]
    if not result.ok:
        raise RamdiskError("ImDisk не смог создать RAM-диск: " + (result.output or "нет вывода"))

    details.append("настройка сохранена в реестре — диск восстановится после перезагрузки")
    return RamdiskResult(
        success=True,
        message=f"RAM-диск {letter}: ({util.human_bytes(size_bytes)}) создан и отформатирован",
        letter=letter,
        size_bytes=size_bytes,
        details=details,
    )


def remove_ramdisk(letter: str) -> RamdiskResult:
    if not util.is_windows():
        raise RamdiskError("RAM-диск доступен только в Windows")
    if not util.is_admin():
        raise RamdiskError("для удаления RAM-диска нужны права администратора")
    letter = util.normalize_drive_letter(letter)
    result = _run(["-D", "-m", f"{letter}:", "-P"], timeout=120)
    details = [line for line in result.output.splitlines() if line.strip()][:6]
    if not result.ok:
        raise RamdiskError("ImDisk не смог удалить RAM-диск: " + (result.output or "нет вывода"))
    return RamdiskResult(
        success=True,
        message=f"RAM-диск {letter}: удалён",
        letter=letter,
        details=details,
    )


def set_temp_to_ramdisk(letter: str) -> dict[str, str]:
    """Переключает TEMP/TMP пользователя на RAM-диск."""
    if not util.is_windows():
        raise RamdiskError("доступно только в Windows")
    letter = util.normalize_drive_letter(letter)
    temp = f"{letter}:\\Temp"
    os.makedirs(temp, exist_ok=True)
    result = util.run_command(
        ["setx", "TEMP", temp, "/M"],
        timeout=30,
    )
    result_user = util.run_command(["setx", "TEMP", temp], timeout=30)
    util.run_command(["setx", "TMP", temp, "/M"], timeout=30)
    util.run_command(["setx", "TMP", temp], timeout=30)
    return {
        "temp": temp,
        "system": "ok" if result.ok else result.output,
        "user": "ok" if result_user.ok else result_user.output,
    }


def ramdisk_json(letter: str, size_bytes: int) -> str:
    return json.dumps(
        {"letter": letter, "size_bytes": size_bytes, "size": util.human_bytes(size_bytes)},
        ensure_ascii=False,
    )
