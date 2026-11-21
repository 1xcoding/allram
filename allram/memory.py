"""Сведения о физической памяти и свопе.

Основной источник — Win32 API через ctypes (GlobalMemoryStatusEx, GetPerformanceInfo),
резервные — PowerShell/CIM и реестр. Всё без внешних зависимостей.
"""

from __future__ import annotations

import ctypes
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import util


@dataclass
class MemoryInfo:
    total_bytes: int = 0
    avail_bytes: int = 0
    commit_total: int = 0
    commit_limit: int = 0
    commit_avail: int = 0
    swap_total: int = 0
    swap_free: int = 0
    source: str = "unknown"

    @property
    def used_bytes(self) -> int:
        return max(0, self.total_bytes - self.avail_bytes)

    @property
    def used_percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return 100.0 * self.used_bytes / self.total_bytes

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "avail_bytes": self.avail_bytes,
            "used_bytes": self.used_bytes,
            "used_percent": round(self.used_percent, 1),
            "commit_total": self.commit_total,
            "commit_limit": self.commit_limit,
            "swap_total": self.swap_total,
            "swap_free": self.swap_free,
            "source": self.source,
        }


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_ulong),
        ("ProcessCount", ctypes.c_ulong),
        ("ThreadCount", ctypes.c_ulong),
    ]


def memory_info() -> MemoryInfo:
    """Возвращает актуальные сведения о памяти, деградируя по источникам."""
    if util.is_windows():
        info = _from_win32()
        if info.total_bytes:
            return info
        info = _from_cim()
        if info.total_bytes:
            return info
    info = _from_psutil_if_available()
    if info.total_bytes:
        return info
    return _from_proc_meminfo()


def _from_win32() -> MemoryInfo:
    info = MemoryInfo(source="win32")
    try:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return info
        info.total_bytes = int(status.ullTotalPhys)
        info.avail_bytes = int(status.ullAvailPhys)
        info.commit_total = int(status.ullTotalPageFile - status.ullTotalPhys)
        info.commit_limit = int(status.ullTotalPageFile)
        info.commit_avail = int(status.ullAvailPageFile)
        info.swap_total = int(status.ullTotalPageFile - status.ullTotalPhys)
        info.swap_free = int(status.ullAvailPageFile - status.ullAvailPhys)
    except Exception:  # pragma: no cover
        return info

    perf = _performance_info()
    if perf is not None:
        page_size, commit_total, commit_limit, phys_total, phys_avail = perf
        info.commit_total = int(commit_total * page_size)
        info.commit_limit = int(commit_limit * page_size)
        info.swap_total = max(0, info.commit_limit - phys_total)
        info.swap_free = max(0, info.swap_total - max(0, info.commit_total - (phys_total - phys_avail)))
    return info


def _performance_info() -> tuple[int, int, int, int, int] | None:
    try:
        perf = PERFORMANCE_INFORMATION()
        perf.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
        if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(perf), perf.cb):
            return None
        return (
            int(perf.PageSize),
            int(perf.CommitTotal),
            int(perf.CommitLimit),
            int(perf.PhysicalTotal),
            int(perf.PhysicalAvailable),
        )
    except Exception:  # pragma: no cover
        return None


def _from_cim() -> MemoryInfo:
    info = MemoryInfo(source="cim")
    script = (
        "$o=[System.Object]::new();"
        "$c=Get-CimInstance Win32_OperatingSystem;"
        "$o.Total=$c.TotalVisibleMemorySize*1024;"
        "$o.Free=$c.FreePhysicalMemory*1024;"
        "$o.CommitLimit=$c.TotalVirtualMemorySize*1024;"
        "$o.CommitFree=$c.FreeVirtualMemory*1024;"
        "$o | ConvertTo-Json -Compress"
    )
    result = util.run_powershell(script)
    if not result.ok:
        return info
    try:
        data = json.loads(result.stdout.strip() or "{}")
        info.total_bytes = int(data.get("Total") or 0)
        info.avail_bytes = int(data.get("Free") or 0)
        info.commit_limit = int(data.get("CommitLimit") or 0)
        info.commit_avail = int(data.get("CommitFree") or 0)
        info.swap_total = max(0, info.commit_limit - info.total_bytes)
        info.swap_free = max(0, info.commit_avail - info.avail_bytes)
    except (ValueError, TypeError):
        return MemoryInfo(source="cim")
    return info


def _from_psutil_if_available() -> MemoryInfo:
    info = MemoryInfo(source="psutil")
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        info.total_bytes = int(vm.total)
        info.avail_bytes = int(vm.available)
        info.commit_limit = info.total_bytes
    except Exception:
        return MemoryInfo(source="psutil")
    return info


def _from_proc_meminfo() -> MemoryInfo:
    info = MemoryInfo(source="proc")
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            data = {}
            for line in handle:
                key, _, rest = line.partition(":")
                value = rest.strip().split(" ")[0]
                if value.isdigit():
                    data[key] = int(value) * 1024
        info.total_bytes = data.get("MemTotal", 0)
        info.avail_bytes = data.get("MemAvailable", data.get("MemFree", 0))
    except OSError:
        pass
    return info


# ---------------------------------------------------------------------------
# Сведения о машине (модель, процессор, сеть для расчёта «нейросети»)
# ---------------------------------------------------------------------------


@dataclass
class MachineProfile:
    cpu_name: str = "unknown"
    cpu_cores: int = 0
    ram_bytes: int = 0
    disk_free_bytes: int = 0
    gpu_name: str = ""
    values: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_name": self.cpu_name,
            "cpu_cores": self.cpu_cores,
            "ram_bytes": self.ram_bytes,
            "gpu_name": self.gpu_name,
            **self.values,
        }


def machine_profile() -> MachineProfile:
    profile = MachineProfile()
    profile.ram_bytes = memory_info().total_bytes
    profile.cpu_cores = _cpu_cores()
    if not util.is_windows():
        profile.cpu_name = _cpu_name_linux()
        return profile
    script = (
        "$o=[System.Object]::new();"
        "$c=Get-CimInstance Win32_Processor | Select-Object -First 1;"
        "$o.Cpu=$c.Name;"
        "$o.Cores=$c.NumberOfCores;"
        "$g=Get-CimInstance Win32_VideoController | Select-Object -First 1;"
        "$o.Gpu=$g.Name;"
        "$o | ConvertTo-Json -Compress"
    )
    result = util.run_powershell(script)
    if result.ok:
        try:
            data = json.loads(result.stdout.strip() or "{}")
            profile.cpu_name = str(data.get("Cpu") or profile.cpu_name).strip()
            if data.get("Cores"):
                profile.cpu_cores = int(data["Cores"])
            profile.gpu_name = str(data.get("Gpu") or "").strip()
        except (ValueError, TypeError):
            pass
    return profile


def _cpu_cores() -> int:
    try:
        import os

        return os.cpu_count() or 0
    except Exception:  # pragma: no cover
        return 0


def _cpu_name_linux() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.partition(":")[2].strip()
    except OSError:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Реестр: сколько «оперативы» видит система
# ---------------------------------------------------------------------------

PHYSICAL_MEMORY_KEY = r"HARDWARE\RESOURCEMAP\System Resources\Physical Memory"
MACHINE_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"


def physical_memory_from_registry() -> int | None:
    """Суммирует физическую память из значения .Translated (REG_RESOURCE_LIST).

    Это то, что Windows показывает как «установлено оперативной памяти».
    Обычно работает только для чтения под текущим пользователем.
    """
    if not util.is_windows():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PHYSICAL_MEMORY_KEY, 0, winreg.KEY_READ) as key:
            data, _kind = winreg.QueryValueEx(key, ".Translated")
        total = _sum_resource_list_memory(bytes(data))
        return total if total > 0 else None
    except Exception:
        return None


def _sum_resource_list_memory(blob: bytes) -> int:
    """Разбирает CM_RESOURCE_LIST и суммирует длины блоков физической памяти.

    Формат (Windows x64):
      заголовок списка — 4 байта Count + 3 байта выравнивания;
      CM_FULL_RESOURCE_DESCRIPTOR: InterfaceType(4) BusNumber(4) Type(1) Shared(1) + Count(2);
      CM_PARTIAL_RESOURCE_DESCRIPTOR: Type(1) ShareDisposition(1) Flags(2) + 8 байт Start + 8 байт Length.
    Тип 3 (CmResourceTypeMemory) и 7 (CmResourceTypeMemoryLarge) содержат блок ОЗУ.
    """
    try:
        if len(blob) < 4:
            return 0
        total = 0
        offset = 0
        list_count = int.from_bytes(blob[0:4], "little") or 1
        offset = 4
        for _ in range(min(list_count, 256)):
            if offset + 9 > len(blob):
                break
            offset += 8  # InterfaceType + BusNumber
            offset += 2  # Type + Shared
            count = int.from_bytes(blob[offset : offset + 2], "little")
            offset += 4  # Count + 2 байта выравнивания
            for _descriptor in range(min(count, 256)):
                if offset + 4 > len(blob):
                    return total
                kind = blob[offset]
                offset += 4
                if kind in (3, 7):
                    if offset + 16 > len(blob):
                        return total
                    length = int.from_bytes(blob[offset + 8 : offset + 16], "little")
                    if kind == 7:  # MemoryLarge: Length — младшие 32 бита в старших 4 байтах значения
                        length = int.from_bytes(blob[offset + 12 : offset + 16], "little") << 32
                    total += length
                    offset += 16
                elif kind == 1:  # Port
                    offset += 8
                elif kind in (2, 4, 5, 6, 8, 9):  # Interrupt/DMA/DeviceSpecific/BusNumber/MemoryLarge40/ConfigData
                    offset += 16
                else:
                    return total
        return total
    except Exception:  # pragma: no cover
        return 0


def cpu_brand_from_registry() -> str | None:
    if not util.is_windows():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MACHINE_KEY, 0, winreg.KEY_READ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except Exception:
        return None


def ram_string_from_os() -> str | None:
    """Строка из реестра Windows, которую видит «Система» (легаси, только чтение)."""
    info = memory_info()
    if not info.total_bytes:
        return None
    return util.human_bytes(info.total_bytes, 0)


def parse_size_label(text: str) -> int | None:
    match = re.search(r"(\d[\d\s.,]*)\s*(ГБ|Гб|GB|Гб)", text or "")
    if not match:
        return None
    try:
        return int(float(match.group(1).replace(" ", "").replace(",", ".")) * util.GIB)
    except ValueError:
        return None
