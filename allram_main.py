"""Точка входа для PyInstaller (allram.exe).

При сборке PyInstaller использует именно этот файл: он не зависит от
интерпретатора пользователя и работает, когда пакет упакован в один exe.
"""

from __future__ import annotations

import sys

from allram.cli import main
from allram.console import configure_stdio, enable_ansi


def run() -> int:
    configure_stdio()
    enable_ansi()
    try:
        return main()
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(run())
