"""Точка входа: python -m allram."""

from __future__ import annotations

import sys

from .cli import main
from .console import configure_stdio


def run() -> int:
    configure_stdio()
    try:
        return main()
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(run())
