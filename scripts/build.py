#!/usr/bin/env python3
"""Сборка одного исполняемого файла AllRAM через PyInstaller.

  python scripts/build.py            # allram.exe (Windows) / allram (Linux/macOS)
  python scripts/build.py --clean    # удалить артефакты и собрать заново

Результат: dist/allram.exe (Windows) или dist/allram, плюс dist/SHA256SUMS.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

def _utf8_console() -> None:
    """Windows-консоль по умолчанию cp1251/cp1252 — переключаем вывод на UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_utf8_console()


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def read_version() -> str:
    text = (ROOT / "allram" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("Не удалось найти __version__ в allram/__init__.py")
    return match.group(1)


def version_numbers(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in re.findall(r"\d+", version)[:4]]
    parts += [0] * (4 - len(parts))
    return parts[0], parts[1], parts[2], parts[3]


VERSION_FILE_TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={prodvers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'AllRAM'),
            StringStruct('FileDescription', 'AllRAM — SSD как дополнительная память Windows'),
            StringStruct('FileVersion', '{version}'),
            StringStruct('InternalName', 'allram'),
            StringStruct('LegalCopyright', 'MIT License'),
            StringStruct('OriginalFilename', 'allram.exe'),
            StringStruct('ProductName', 'AllRAM'),
            StringStruct('ProductVersion', '{version}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_file(version: str) -> Path:
    numbers = version_numbers(version)
    path = BUILD / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        VERSION_FILE_TEMPLATE.format(
            filevers=numbers,
            prodvers=numbers,
            version=version,
        ),
        encoding="utf-8",
    )
    return path


def is_windows() -> bool:
    return platform.system() == "Windows"


def _has_shared_python_library() -> bool:
    """PyInstaller требует libpython*.so в Linux — проверяем наличие."""
    import sysconfig

    lib_dir = sysconfig.get_config_var("LIBDIR") or ""
    library = sysconfig.get_config_var("LDLIBRARY") or ""
    if not library:
        return False
    candidates = [Path(lib_dir) / library, Path(lib_dir) / f"{library}.1.0"]
    return any(path.exists() for path in candidates)


def clean() -> None:
    for path in (DIST, BUILD):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(*, do_clean: bool = False) -> Path:
    version = read_version()
    if do_clean:
        clean()

    if not is_windows() and not _has_shared_python_library():
        raise SystemExit(
            "Для сборки в Linux нужен общий libpython (пакет python3-dev / libpython3.x). "
            "Для Windows запустите этот скрипт на Windows или в GitHub Actions "
            "(см. .github/workflows/release.yml) — получится allram.exe."
        )

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller не установлен. Выполните: python -m pip install -r requirements-dev.txt"
        )

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "allram",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "work"),
        "--specpath",
        str(BUILD),
        "--hidden-import",
        "allram",
        "--collect-submodules",
        "allram",
        str(ROOT / "allram_main.py"),
    ]

    icon = ROOT / "assets" / "allram.ico"
    if is_windows():
        if icon.exists():
            args += ["--icon", str(icon)]
        args += ["--version-file", str(write_version_file(version))]

    print("Сборка:", " ".join(args))
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller завершился с кодом {result.returncode}")

    binary = DIST / ("allram.exe" if is_windows() else "allram")
    if not binary.exists():
        raise SystemExit(f"Ожидался файл {binary}, но его нет")

    checksum = sha256(binary)
    sums = DIST / "SHA256SUMS.txt"
    sums.write_text(f"{checksum}  {binary.name}\n", encoding="utf-8")

    size_mib = binary.stat().st_size / (1024 * 1024)
    print()
    print(f"Готово: {binary} ({size_mib:.1f} МиБ)")
    print(f"SHA-256: {checksum}")
    print(f"Файл сумм: {sums}")
    return binary


def smoke_test(binary: Path) -> None:
    """Быстрая проверка, что собранный файл запускается."""
    commands = [[str(binary), "--version"], [str(binary), "presets", "--json", "--no-color"]]
    for command in commands:
        print("Проверка:", " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise SystemExit(
                f"Собранный файл не прошёл проверку {command}:\n{result.stdout}\n{result.stderr}"
            )
    print("Проверка пройдена.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка allram.exe")
    parser.add_argument("--clean", action="store_true", help="удалить dist/ и build/ перед сборкой")
    parser.add_argument("--no-smoke", action="store_true", help="не запускать собранный файл")
    args = parser.parse_args()

    binary = build(do_clean=args.clean)
    if not args.no_smoke:
        smoke_test(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
