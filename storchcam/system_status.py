"""Best-effort, truthful platform system telemetry."""

from __future__ import annotations

import ctypes
import os
import platform
import socket
import time
from typing import Any

_last_cpu: tuple[int, int] | None = None
_started_at = time.monotonic()


def get_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        value = sock.getsockname()[0]
        return value if value and not value.startswith("127.") else None
    except OSError:
        try:
            value = socket.gethostbyname(socket.gethostname())
            return value if value and not value.startswith("127.") else None
        except OSError:
            return None
    finally:
        sock.close()


def get_cpu() -> int | None:
    global _last_cpu
    if not os.path.exists("/proc/stat"):
        return None
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            values = [int(item) for item in handle.readline().split()[1:]]
        idle = values[3] + values[4]
        total = sum(values)
        if _last_cpu is None:
            _last_cpu = (total, idle)
            return None
        previous_total, previous_idle = _last_cpu
        _last_cpu = (total, idle)
        delta_total = total - previous_total
        return None if delta_total <= 0 else round(100 * (delta_total - (idle - previous_idle)) / delta_total)
    except (OSError, ValueError, IndexError):
        return None


def _windows_ram() -> int | None:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
            ("avail_extended", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        windll = getattr(ctypes, "windll", None)
        if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.memory_load)
    except (AttributeError, OSError):
        pass
    return None


def get_ram() -> int | None:
    if platform.system() == "Windows":
        return _windows_ram()
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                values[parts[0].rstrip(":")] = int(parts[1])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        return round(100 * (total - available) / total) if total else None
    except (OSError, ValueError, IndexError):
        return None


def get_temp() -> int | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as handle:
            return round(int(handle.read().strip()) / 1000)
    except (OSError, ValueError):
        return None


def get_uptime_seconds() -> int:
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return int(time.monotonic() - _started_at)


def get_system_status() -> dict[str, Any]:
    uptime = get_uptime_seconds()
    return {
        "ip": get_ip(),
        "cpu": get_cpu(),
        "ram": get_ram(),
        "temp": get_temp(),
        "uptime_seconds": uptime,
        "uptime": f"{uptime // 3600}h {(uptime % 3600) // 60}m",
        "platform": platform.system(),
    }
