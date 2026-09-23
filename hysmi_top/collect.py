"""Data collection for Hygon DCU (hy-smi compatible) cards.

Primary source is the kernel sysfs interface exposed by the ``hycu`` driver
(an amdgpu fork), which is cheap to read in a polling loop (no subprocess).
Process information is best-effort parsed from ``hy-smi --showpids``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DRM_DIR = Path("/sys/class/drm")
HYCU_DRIVER = "hycu"

HY_SMI_BIN = os.environ.get("HYSMI_BIN") or shutil.which("hy-smi") or "/opt/hyhal/bin/hy-smi"

_PID_RE = re.compile(r"^PID:\s+(\d+)\s*$")
_HCU_INDEX_RE = re.compile(r"HCU Index:\s*\[\s*['\"]?(\d+)['\"]?\s*\]")
_VRAM_MIB_RE = re.compile(r"VRAM USED\(MiB\):\s*(\d+)")


def _proc_name(pid: int) -> str:
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except OSError:
        return "?"


@dataclass
class HcuStats:
    """One snapshot of a single HCU."""

    hcu_id: int
    name: str = "HCU"
    util_percent: float = 0.0
    vram_used: int = 0
    vram_total: int = 0
    temp_milli: int = 0
    power_uw: int = 0
    sclk_hz: int = 0
    mclk_hz: int = 0
    ok: bool = True
    error: str = ""

    @property
    def vram_percent(self) -> float:
        if self.vram_total <= 0:
            return 0.0
        return min(100.0, self.vram_used / self.vram_total * 100.0)

    @property
    def temp_c(self) -> float:
        return self.temp_milli / 1000.0

    @property
    def power_w(self) -> float:
        return self.power_uw / 1_000_000.0

    @property
    def sclk_mhz(self) -> float:
        return self.sclk_hz / 1_000_000.0

    @property
    def mclk_mhz(self) -> float:
        return self.mclk_hz / 1_000_000.0


@dataclass
class HcuProcess:
    pid: int
    device: int = -1
    name: str = "?"
    memory: int = 0

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"pid={self.pid} dev={self.device} name={self.name}"


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _read_int(path: Path, default: int = 0) -> int:
    val = _read(path)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def discover_devices() -> list[Path]:
    """Return the sysfs card dirs for Hygon DCUs, ordered by card number.

    Cards whose PCI driver is ``hycu`` are treated as Hygon DCUs; ordering by
    card number matches hy-smi output (card1 -> HCU 0, card2 -> HCU 1, ...).
    """
    cards: list[Path] = []
    if not DRM_DIR.is_dir():
        return cards
    for card in DRM_DIR.glob("card*"):
        num = card.name[4:]
        if not num.isdigit():
            continue
        driver = card / "device" / "driver"
        if driver.is_symlink() and driver.resolve().name == HYCU_DRIVER:
            cards.append(card)
    cards.sort(key=lambda p: int(p.name[4:]))
    return cards


def _card_dir_for(hcu_id: int) -> Path | None:
    """Map an HCU id back to its sysfs card directory (card1 -> HCU 0)."""
    cards = discover_devices()
    if hcu_id < 0 or hcu_id >= len(cards):
        return None
    return cards[hcu_id] / "device"


def read_device(hcu_id: int) -> HcuStats:
    """Read one HCU from sysfs."""
    stats = HcuStats(hcu_id=hcu_id, name=f"HCU {hcu_id}")
    dev = _card_dir_for(hcu_id)
    if dev is None:
        stats.ok = False
        stats.error = "no sysfs device"
        return stats
    try:
        stats.util_percent = _read_int(dev / "gpu_busy_percent")
        stats.vram_used = _read_int(dev / "mem_info_vram_used")
        stats.vram_total = _read_int(dev / "mem_info_vram_total")
        hw = next(dev.glob("hwmon/hwmon*"), None)
        if hw is not None:
            stats.temp_milli = _read_int(hw / "temp1_input")
            stats.power_uw = _read_int(hw / "power1_average")
            stats.sclk_hz = _read_int(hw / "freq1_input")
            stats.mclk_hz = _read_int(hw / "freq2_input")
    except Exception as exc:  # noqa: BLE001 - keep loop alive on any read error
        stats.ok = False
        stats.error = str(exc)
    return stats


def collect_all(device_ids: list[int] | None = None) -> list[HcuStats]:
    if device_ids is None:
        device_ids = list(range(len(discover_devices())))
    return [read_device(i) for i in device_ids]


def read_processes() -> list[HcuProcess]:
    """Best-effort parse of ``hy-smi --showpids``.

    The output is a series of ``PID:`` blocks with indented fields (HCU Index,
    VRAM USED(MiB), ...); the process name is looked up from /proc.
    """
    try:
        out = subprocess.run(
            [HY_SMI_BIN, "--showpids"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs: list[HcuProcess] = []
    cur: HcuProcess | None = None
    for line in out.splitlines():
        m = _PID_RE.match(line.strip())
        if m:
            if cur is not None:
                procs.append(cur)
            pid = int(m.group(1))
            cur = HcuProcess(pid=pid, name=_proc_name(pid))
            continue
        if cur is None:
            continue
        mi = _HCU_INDEX_RE.search(line)
        if mi:
            cur.device = int(mi.group(1))
            continue
        mv = _VRAM_MIB_RE.search(line)
        if mv:
            cur.memory = int(mv.group(1)) * 1024**2
    if cur is not None:
        procs.append(cur)
    return procs
