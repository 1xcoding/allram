"""Терминальный вывод AllRAM: цвета, рамки, подтверждения (без сторонних библиотек)."""

from __future__ import annotations

import os
import sys
from typing import Iterable, Sequence

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[97m"
GREY = "\033[90m"

WIDTH = 74


def configure_stdio() -> None:
    """Включает UTF-8 в консоли Windows, чтобы кириллица не превращалась в «?»."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - зависит от окружения
            pass
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:  # pragma: no cover
        pass


def _enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # pragma: no cover
        pass


class Console:
    """Печать в stdout с поддержкой --no-color и Unicode-проверок."""

    def __init__(self, color: bool | None = None, stream=None) -> None:
        self.stream = stream or sys.stdout
        self._color = self._detect_color() if color is None else color
        self.unicode = self._detect_unicode()

    @staticmethod
    def _detect_color() -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("ALLRAM_NO_COLOR"):
            return False
        try:
            return bool(self_stream_isatty())
        except Exception:  # pragma: no cover
            return False

    @staticmethod
    def _detect_unicode() -> bool:
        encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
        if not encoding:
            return os.name == "nt"
        if os.name == "nt":
            return "utf" in encoding or encoding in ("cp65001", "utf8")
        return "utf" in encoding

    def set_color(self, enabled: bool) -> None:
        self._color = enabled

    @property
    def color(self) -> bool:
        return self._color

    def _paint(self, text: str, *codes: str) -> str:
        if not self._color or not codes:
            return text
        return "".join(codes) + text + RESET

    def print(self, text: str = "", *codes: str, end: str = "\n") -> None:
        print(self._paint(text, *codes), end=end, flush=True)

    # -- простые сообщения -------------------------------------------------
    def ok(self, text: str) -> None:
        self.print(text, GREEN)

    def warn(self, text: str) -> None:
        self.print(text, YELLOW)

    def error(self, text: str) -> None:
        self.print(text, RED)

    def info(self, text: str) -> None:
        self.print(text, CYAN)

    def muted(self, text: str, end: str = "\n") -> None:
        self.print(text, DIM, end=end)

    # -- структура ---------------------------------------------------------
    def rule(self, char: str = "─") -> None:
        self.print(char * WIDTH, DIM)

    def title(self, text: str) -> None:
        self.print()
        self.print(f" {text}", BOLD, WHITE)
        self.rule()

    def section(self, text: str) -> None:
        self.print()
        self.print(text, BOLD, CYAN)

    def banner(self, version: str, subtitle: str) -> None:
        art = [
            "  █████╗ ██╗     ██╗     ██████╗  █████╗ ███╗   ███╗",
            " ██╔══██╗██║     ██║     ██╔══██╗██╔══██╗████╗ ████║",
            " ███████║██║     ██║     ██████╔╝███████║██╔████╔██║",
            " ██╔══██║██║     ██║     ██╔══██╗██╔══██║██║╚██╔╝██║",
            " ██║  ██║███████╗███████╗██║  ██║██║  ██║██║ ╚═╝ ██║",
            " ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝",
        ]
        if self.unicode and self._color:
            for line in art:
                self.print(line, CYAN)
        self.print(f" {version}  ·  {subtitle}", DIM)

    def table(self, rows: Iterable[Sequence[str]], *, header: Sequence[str] | None = None) -> None:
        rows = [list(map(str, row)) for row in rows]
        if header:
            rows = [list(map(str, header))] + rows
        if not rows:
            return
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for index, row in enumerate(rows):
            cells = []
            for i, cell in enumerate(row):
                if i == len(row) - 1:
                    cells.append(cell)
                else:
                    cells.append(cell.ljust(widths[i]))
            line = "  ".join(cells)
            if header and index == 0:
                self.print(line, BOLD)
            else:
                self.print(line)

    def bullet(self, text: str, level: int = 0, marker: str = "•") -> None:
        symbol = marker if self.unicode else "-"
        self.print("  " * (level + 1) + f"{symbol} " + text)


def self_stream_isatty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:  # pragma: no cover
        return False


def enable_ansi() -> None:
    _enable_windows_ansi()


# ---------------------------------------------------------------------------
# Ввод
# ---------------------------------------------------------------------------


class Aborted(Exception):
    """Пользователь прервал диалог (Ctrl+C или пустой ввод там, где нужен ответ)."""


def ask(prompt: str, default: str | None = None, *, allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError as exc:
        raise Aborted from exc
    if not answer:
        if default is not None:
            return default
        if allow_empty:
            return ""
        raise Aborted
    return answer


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    marker = "Д/н" if default else "д/Н"
    while True:
        try:
            answer = input(f"{prompt} ({marker}): ").strip().lower()
        except EOFError as exc:
            raise Aborted from exc
        if not answer:
            return default
        if answer in ("д", "да", "y", "yes", "1"):
            return True
        if answer in ("н", "нет", "n", "no", "0"):
            return False
        print("Пожалуйста, ответьте «д» или «н».")


def ask_choice(prompt: str, options: Sequence[str], default_index: int | None = None) -> int:
    """Выбор из списка по номеру; возвращает индекс (0-based)."""
    print(prompt)
    for index, option in enumerate(options, start=1):
        print(f"  {index}) {option}")
    if default_index is not None:
        print(f"  Enter — вариант {default_index + 1}")
    while True:
        try:
            answer = input("> ").strip()
        except EOFError as exc:
            raise Aborted from exc
        if not answer and default_index is not None:
            return default_index
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print(f"Введите число от 1 до {len(options)}.")
