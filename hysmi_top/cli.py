"""Command line entry point for hysmi-top."""

from __future__ import annotations

import argparse
import curses
import json
import locale
import sys

from . import __version__
from .collect import collect_all, discover_devices, read_processes
from .ui import DEFAULT_REFRESH_MS, HySmiTop


def _parse_devices(text: str) -> list[int]:
    ids: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.extend(range(int(a), int(b) + 1))
        else:
            ids.append(int(part))
    seen: set[int] = set()
    result: list[int] = []
    for d in ids:
        if d >= 0 and d not in seen:
            seen.add(d)
            result.append(d)
    return result


def _snapshot(device_ids: list[int], as_json: bool, demo: bool = False) -> int:
    if demo:
        top = HySmiTop(device_ids, 1000, demo=True)
        stats = [top.last_stats[d] for d in device_ids]
        procs = []
    else:
        stats = collect_all(device_ids)
        procs = read_processes()
    if as_json:
        payload = {
            "devices": [
                {
                    "hcu_id": s.hcu_id,
                    "util_percent": s.util_percent,
                    "vram_used": s.vram_used,
                    "vram_total": s.vram_total,
                    "vram_percent": s.vram_percent,
                    "temp_c": s.temp_c,
                    "power_w": s.power_w,
                    "sclk_mhz": s.sclk_mhz,
                    "mclk_mhz": s.mclk_mhz,
                    "ok": s.ok,
                    "error": s.error,
                }
                for s in stats
            ],
            "processes": [{"pid": p.pid, "name": p.name} for p in procs],
        }
        print(json.dumps(payload, indent=2))
        return 0
    if not stats:
        print("No Hygon DCU devices found.", file=sys.stderr)
        return 0
    for s in stats:
        flag = "" if s.ok else f"  [ERROR: {s.error}]"
        print(
            f"HCU {s.hcu_id}: util={s.util_percent:5.1f}%  "
            f"vram={s.vram_percent:5.1f}% ({s.vram_used}/{s.vram_total} B)  "
            f"{s.temp_c:5.1f}C  {s.power_w:5.1f}W  "
            f"sclk={s.sclk_mhz:.0f}M mclk={s.mclk_mhz:.0f}M{flag}"
        )
    for p in procs:
        print(f"  proc {p.pid}: {p.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hysmi-top",
        description="Terminal monitor for Hygon DCU cards (hy-smi data) with scrolling curves.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-d",
        "--devices",
        default=None,
        help="comma/range list of HCU ids, e.g. 0,2-4 (default: all)",
    )
    parser.add_argument(
        "-r",
        "--refresh",
        type=int,
        default=DEFAULT_REFRESH_MS,
        help=f"refresh interval in ms (default {DEFAULT_REFRESH_MS})",
    )
    parser.add_argument(
        "-c",
        "--chart-height",
        type=int,
        default=None,
        help="cap on curve chart height in rows (default: fill available space)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run in demo mode with simulated workload curves (no DCU hardware needed)",
    )
    parser.add_argument(
        "--once", action="store_true", help="print a one-shot snapshot and exit (no TUI)"
    )
    parser.add_argument("--json", action="store_true", help="with --once, emit JSON")
    args = parser.parse_args(argv)

    device_ids = _parse_devices(args.devices) if args.devices else None
    if args.demo and device_ids is None:
        device_ids = list(range(8))
    elif device_ids is None:
        device_ids = list(range(len(discover_devices())))

    if args.once:
        return _snapshot(device_ids, args.json, demo=args.demo)

    if not sys.stdout.isatty():
        print("hysmi-top needs a TTY; use --once for a text snapshot", file=sys.stderr)
        return 1

    locale.setlocale(locale.LC_ALL, "")
    app = HySmiTop(device_ids, args.refresh, args.chart_height, demo=args.demo)
    return curses.wrapper(app.run)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
