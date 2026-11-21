"""Общие утилиты AllRAM: размеры, права, запуск процессов, пути данных."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

IS_WINDOWS = os.name == "nt"

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB
TIB = 1024 * GIB

_UNIT_FACTORS = {
    "b": 1,
    "k": KIB,
    "kb": KIB,
    "m": MIB,
    "mb": MIB,
    "g": GIB,
    "gb": GIB,
    "t": TIB,
    "tb": TIB,
    "к": KIB,
    "кб": KIB,
    "м": MIB,
    "мб": MIB,
    "г": GIB,
    "гб": GIB,
    "т": TIB,
    "тб": TIB,
}


# ---------------------------------------------------------------------------
# Размеры
# ---------------------------------------------------------------------------


def human_bytes(value: float | int | None, precision: int = 1) -> str:
    """Человекочитаемый размер: 1536 -> '1.5 КиБ'."""
    if value is None:
        return "—"
    value = float(value)
    if value < 0:
        return "-" + human_bytes(-value, precision)
    for unit, factor in (("ТиБ", TIB), ("ГиБ", GIB), ("МиБ", MIB), ("КиБ", KIB)):
        if value >= factor:
            return f"{value / factor:.{precision}f} {unit}"
    return f"{int(value)} Б"


def human_bytes_decimal(value: float | int | None, precision: int = 1) -> str:
    """Размер в десятичных единицах (как на коробке SSD): 32 ГБ = 32 000 000 000 Б."""
    if value is None:
        return "—"
    value = float(value)
    for unit, factor in (("ТБ", 1000**4), ("ГБ", 1000**3), ("МБ", 1000**2), ("КБ", 1000)):
        if value >= factor:
            return f"{value / factor:.{precision}f} {unit}"
    return f"{int(value)} Б"


def parse_size(text: str, *, default_unit: str = "g") -> int:
    """Разбирает '32', '32G', '512M', '1.5T' в байты.

    Число без суффикса трактуется как гигабайты (default_unit='g').
    """
    if text is None:
        raise ValueError("пустое значение размера")
    raw = str(text).strip().replace(",", ".").replace(" ", "")
    if not raw:
        raise ValueError("пустое значение размера")

    number = ""
    suffix = ""
    for index, char in enumerate(raw):
        if char.isdigit() or char == ".":
            if suffix:
                raise ValueError(f"некорректный размер: {text!r}")
            number += char
        else:
            suffix = raw[index:].lower()
            break

    if not number or number.count(".") > 1:
        raise ValueError(f"некорректный размер: {text!r}")
    if suffix and suffix not in _UNIT_FACTORS:
        raise ValueError(
            f"неизвестная единица {suffix!r}; используйте B, K, M, G или T"
        )
    factor = _UNIT_FACTORS[suffix] if suffix else _UNIT_FACTORS[default_unit]
    try:
        amount = float(number)
    except ValueError as exc:  # pragma: no cover - защита от экзотики
        raise ValueError(f"некорректный размер: {text!r}") from exc
    if amount <= 0:
        raise ValueError("размер должен быть больше нуля")
    return int(amount * factor)


def round_up_to_gib(value: int) -> int:
    """Округляет байты до целых ГиБ вверх."""
    if value <= 0:
        return GIB
    return ((value + GIB - 1) // GIB) * GIB


def round_down_to_mib(value: int) -> int:
    return max(MIB, (value // MIB) * MIB)


# ---------------------------------------------------------------------------
# Время
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Текущее время в ISO-8601 (UTC)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


# ---------------------------------------------------------------------------
# Платформа и права
# ---------------------------------------------------------------------------


def is_admin() -> bool:
    """True, если процесс запущен с правами администратора."""
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - зависит от окружения
        return False


def is_windows() -> bool:
    return IS_WINDOWS


def windows_build() -> str | None:
    """Возвращает строку версии Windows из реестра (без прав администратора)."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            def read(name: str) -> str:
                try:
                    return str(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    return ""

            product = read("ProductName")
            display = read("DisplayVersion") or read("ReleaseId")
            build = read("CurrentBuildNumber")
            ubr = read("UBR")
            parts = [part for part in (product, display) if part]
            if build:
                parts.append(f"build {build}" + (f".{ubr}" if ubr else ""))
            return " · ".join(parts) or None
    except Exception:  # pragma: no cover
        return None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> str:
    """Путь к исполняемому файлу AllRAM (exe или python)."""
    return str(Path(sys.executable).resolve()) if is_frozen() else sys.executable


def command_prefix() -> list[str]:
    """Как запустить AllRAM повторно: [exe] или [python, -m, allram]."""
    if is_frozen():
        return [sys.executable]
    return [sys.executable, "-m", "allram"]


def relaunch_elevated(extra_args: Sequence[str], *, wait: bool = False) -> bool:
    """Перезапускает AllRAM с правами администратора (UAC).

    Возвращает True, если повышенный процесс был запущен пользователем
    (или False, если UAC отменён / платформа не Windows).
    """
    if not IS_WINDOWS:
        return False
    try:
        params = subprocess.list2cmdline(list(extra_args))
        executable = executable_path()
        if is_frozen():
            target = executable
            full_params = params
        else:
            target = executable
            full_params = subprocess.list2cmdline(["-m", "allram", *extra_args])
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, full_params, None, 1 if wait else 1
        )
        return int(result) > 32
    except Exception:  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# Пути данных
# ---------------------------------------------------------------------------


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def data_dir() -> Path:
    """Каталог для конфигурации, бэкапов и логов."""
    override = os.environ.get("ALLRAM_HOME")
    if override:
        return Path(override).expanduser()

    if is_admin():
        base = _env_path("ProgramData") or _env_path("ALLUSERSPROFILE")
        if base:
            candidate = base / "AllRAM"
            if _is_writable_dir(candidate.parent):
                return candidate

    local = _env_path("LOCALAPPDATA")
    if local:
        return local / "AllRAM"
    if IS_WINDOWS:
        return Path.home() / "AppData" / "Local" / "AllRAM"
    return Path.home() / ".local" / "share" / "allram"


def ensure_data_dir() -> Path:
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(exist_ok=True)
    return path


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".allram-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def log_path() -> Path:
    return ensure_data_dir() / "logs" / "allram.log"


# ---------------------------------------------------------------------------
# Файлы и JSON
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def json_write(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def json_read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_json_safe(path: Path) -> dict[str, Any]:
    data = json_read(path, {})
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Процессы
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr.strip() else "")).strip()


def run_command(
    args: Sequence[str],
    *,
    timeout: float = 30.0,
    check: bool = False,
    input_text: str | None = None,
) -> CommandResult:
    """Запускает команду без показа окна консоли."""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "input": input_text,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(list(args), **kwargs)  # noqa: S603 - команды задаёт сам AllRAM
    except (OSError, subprocess.SubprocessError) as exc:
        if check:
            raise RuntimeError(str(exc)) from exc
        return CommandResult(False, -1, "", str(exc))
    ok = proc.returncode == 0
    if check and not ok:
        raise RuntimeError(
            f"команда {' '.join(args)} завершилась с кодом {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )
    return CommandResult(ok, proc.returncode, proc.stdout or "", proc.stderr or "")


def run_powershell(script: str, *, timeout: float = 25.0) -> CommandResult:
    """Выполняет PowerShell-скрипт (только Windows)."""
    if not IS_WINDOWS:
        return CommandResult(False, -1, "", "PowerShell недоступен вне Windows")
    args = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    return run_command(args, timeout=timeout)


def which(executable: str) -> str | None:
    return shutil.which(executable)


def free_space(path: str | os.PathLike[str]) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return -1


def drive_root(path: str) -> str:
    """'C:\\Windows' -> 'C:\\' ; для POSIX возвращает ближайший существующий корень."""
    path = os.path.abspath(path)
    drive, _ = os.path.splitdrive(path)
    if drive:
        return drive + "\\"
    return os.path.sep


def normalize_drive_letter(value: str) -> str:
    """'c', 'C:', 'C:\\' -> 'C'."""
    text = str(value).strip().upper().rstrip("\\/")
    if text.endswith(":"):
        text = text[:-1]
    if len(text) != 1 or not text.isalpha():
        raise ValueError(f"некорректная буква диска: {value!r}")
    return text


def drive_exists(letter: str) -> bool:
    if not IS_WINDOWS:
        return Path(f"{letter}:\\").exists()
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    index = ord(letter.upper()) - ord("A")
    return bool(mask & (1 << index))


def list_drives() -> list[str]:
    """Список букв логических дисков."""
    if not IS_WINDOWS:
        return []
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    return [chr(ord("A") + index) for index in range(26) if mask & (1 << index)]


def iter_batches(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
