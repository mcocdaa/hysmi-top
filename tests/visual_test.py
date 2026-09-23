"""Visual inspection tool for hysmi-top rendering.

Simulates terminal rendering of HySmiTop under configurable dimensions
(e.g., 90x16, 90x15, 80x24) with diverse workload profiles (sine waves,
crossing curves, spike bursts, steps, exact dynamic overlaps, noise, and 0% baselines).
"""

from __future__ import annotations

import math
import random
import sys
from collections import deque
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hysmi_top.collect import HcuStats
from hysmi_top.ui import _CURVE_COLORS, MIX_OWNER, HySmiTop, render_overlay

ANSI = {
    "title": "\033[1;36m",  # Bold Cyan
    "header": "\033[1;37m",  # Bold White
    "util": "\033[32m",  # Green
    "vram": "\033[35m",  # Magenta
    "status": "\033[33m",  # Yellow
    "proc": "\033[36m",  # Cyan
    "dim": "\033[90m",  # Gray
    "err": "\033[31m",  # Red
    "mix": "\033[1;34m",  # Bold Blue
    "reset": "\033[0m",
}


class ScreenBuffer:
    def __init__(self, maxy: int, maxx: int):
        self.maxy = maxy
        self.maxx = maxx
        self.cells = [[(" ", None) for _ in range(maxx)] for _ in range(maxy)]

    def erase(self):
        self.cells = [[(" ", None) for _ in range(self.maxx)] for _ in range(self.maxy)]

    def getmaxyx(self):
        return self.maxy, self.maxx

    def addstr(self, y: int, x: int, text: str, attr_pair: int = 0):
        for i, ch in enumerate(text):
            if 0 <= y < self.maxy and 0 <= x + i < self.maxx:
                self.cells[y][x + i] = (ch, attr_pair)

    def print_plain(self):
        print(f"┌{'─' * self.maxx}┐")
        for y in range(self.maxy):
            row_str = "".join(ch for ch, _ in self.cells[y])
            print(f"│{row_str}│")
        print(f"└{'─' * self.maxx}┘")

    def print_ansi(self, colors: dict[str, int]):
        id_to_name = {v: k for k, v in colors.items()}
        print(f"┌{'─' * self.maxx}┐")
        for y in range(self.maxy):
            out = ["│"]
            cur_style = None
            for x in range(self.maxx):
                ch, pair_id = self.cells[y][x]
                style_name = id_to_name.get(pair_id)
                ansi_code = ANSI.get(style_name, "")
                if ansi_code != cur_style:
                    if cur_style:
                        out.append(ANSI["reset"])
                    if ansi_code:
                        out.append(ansi_code)
                    cur_style = ansi_code
                out.append(ch)
            if cur_style:
                out.append(ANSI["reset"])
            out.append("│")
            print("".join(out))
        print(f"└{'─' * self.maxx}┘")


def gen_curve_profiles(n: int = 35) -> list[tuple[str, deque[float], deque[float], float, float]]:
    """Generate 8 distinct real-world curve profiles for testing."""
    # 0: Sine wave intersection (util sine vs vram cosine)
    u0 = deque(
        [
            max(0.0, min(100.0, 50.0 + 40.0 * math.sin(2 * math.pi * 1.5 * i / (n - 1))))
            for i in range(n)
        ],
        maxlen=512,
    )
    v0 = deque(
        [
            max(0.0, min(100.0, 50.0 + 35.0 * math.cos(2 * math.pi * 1.5 * i / (n - 1))))
            for i in range(n)
        ],
        maxlen=512,
    )

    # 1: Linear crossing (util 10->90, vram 90->10)
    u1 = deque([10.0 + 80.0 * (i / (n - 1)) for i in range(n)], maxlen=512)
    v1 = deque([90.0 - 80.0 * (i / (n - 1)) for i in range(n)], maxlen=512)

    # 2: Burst spikes (0% baseline, burst to 95% every 8 ticks)
    u2 = deque([95.0 if (i % 8 in (0, 1)) else 0.0 for i in range(n)], maxlen=512)
    v2 = deque([45.0 for _ in range(n)], maxlen=512)

    # 3: Stepped workload (15% -> 45% -> 75% -> 90%)
    steps = [15.0, 45.0, 75.0, 90.0]
    seg = max(1, n // len(steps))
    u3 = deque([steps[min(len(steps) - 1, i // seg)] for i in range(n)], maxlen=512)
    v3 = deque([60.0 for _ in range(n)], maxlen=512)

    # 4: Exact dynamic overlap (both follow identical sine wave, tests all-blue line)
    overlap_wave = [
        max(0.0, min(100.0, 50.0 + 35.0 * math.sin(2 * math.pi * 1.2 * i / (n - 1))))
        for i in range(n)
    ]
    u4 = deque(overlap_wave, maxlen=512)
    v4 = deque(overlap_wave, maxlen=512)

    # 5: High frequency noise jitter (60% +- 18%)
    rng = random.Random(42)
    val = 60.0
    pts5 = []
    for _ in range(n):
        val = max(15.0, min(95.0, val + rng.uniform(-10.0, 10.0)))
        pts5.append(val)
    u5 = deque(pts5, maxlen=512)
    v5 = deque([50.0 + rng.uniform(-5.0, 5.0) for _ in range(n)], maxlen=512)

    # 6: 0% util + 25% vram (the user reported bug scenario: idle compute with model in VRAM)
    u6 = deque([0.0 for _ in range(n)], maxlen=512)
    v6 = deque([25.0 for _ in range(n)], maxlen=512)

    # 7: 0% util + 0% vram (absolute zero load baseline)
    u7 = deque([0.0 for _ in range(n)], maxlen=512)
    v7 = deque([0.0 for _ in range(n)], maxlen=512)

    return [
        ("Sine/Cosine Wave", u0, v0, u0[-1], v0[-1]),
        ("Linear Crossing", u1, v1, u1[-1], v1[-1]),
        ("Burst Spikes", u2, v2, u2[-1], v2[-1]),
        ("Stepped Load", u3, v3, u3[-1], v3[-1]),
        ("Exact Dynamic Overlap", u4, v4, u4[-1], v4[-1]),
        ("Noise / Jitter", u5, v5, u5[-1], v5[-1]),
        ("0% Util + 25% VRAM (Bug Case)", u6, v6, u6[-1], v6[-1]),
        ("0% Util + 0% VRAM (Zero Baseline)", u7, v7, u7[-1], v7[-1]),
    ]


def inspect_screen(maxy: int = 16, maxx: int = 90, ansi: bool = True):
    import curses

    curses.LINES = maxy
    curses.COLS = maxx

    top = HySmiTop(list(range(8)), 1000)
    colors = {
        "title": 1,
        "header": 2,
        "util": 3,
        "vram": 4,
        "status": 5,
        "proc": 6,
        "dim": 7,
        "err": 8,
        "mix": 9,
    }

    profiles = gen_curve_profiles(35)
    for d, (_name, u_series, v_series, last_u, last_v) in enumerate(profiles):
        top.last_stats[d] = HcuStats(
            hcu_id=d,
            util_percent=last_u,
            vram_used=int(last_v * 64 * 1024**3 / 100),
            vram_total=64 * 1024**3,
            temp_milli=45000,
            power_uw=120_000_000,
        )
        top.util[d] = u_series
        top.vram[d] = v_series

    scr = ScreenBuffer(maxy, maxx)

    old_color_pair = getattr(curses, "color_pair", None)
    curses.color_pair = lambda p: p

    try:
        top._draw(scr, True, colors)
    finally:
        if old_color_pair:
            curses.color_pair = old_color_pair

    per_row, nrows, base, extra = top._layout(maxy, maxx, 8)
    print(
        f"\nTerminal Size: {maxx}x{maxy} | Layout: per_row={per_row}, nrows={nrows}, base={base}, extra={extra}"
    )
    if ansi:
        scr.print_ansi(colors)
    else:
        scr.print_plain()


def inspect_patterns():
    """Print detailed isolated inspections of each curve pattern."""
    print("=" * 80)
    print("PATTERN 1: LINEAR CROSSING (HCU% 10->90 vs VRAM% 90->10)")
    print("Green is util, Magenta is VRAM, Bold Blue is intersection point")
    print("=" * 80)
    u_cross = deque([10.0 + 80.0 * (i / 29) for i in range(30)], maxlen=512)
    v_cross = deque([90.0 - 80.0 * (i / 29) for i in range(30)], maxlen=512)

    chart = render_overlay([u_cross, v_cross], 30, 4, True)
    for r, row in enumerate(chart):
        line = []
        for ch, owner in row:
            if owner == MIX_OWNER:
                line.append(f"{ANSI['mix']}{ch}{ANSI['reset']}")
            elif owner is not None:
                color_key = _CURVE_COLORS[owner]
                line.append(f"{ANSI[color_key]}{ch}{ANSI['reset']}")
            else:
                line.append(ch)
        print(f"Row {r}: " + "".join(line))

    print("\n" + "=" * 80)
    print("PATTERN 2: DYNAMIC EXACT OVERLAP (Both follow identical Sine Wave)")
    print("Every single point is recognized as MIX_OWNER and rendered in Bold Blue")
    print("=" * 80)
    wave = deque(
        [
            max(0.0, min(100.0, 50.0 + 40.0 * math.sin(2 * math.pi * 1.5 * i / 29)))
            for i in range(30)
        ],
        maxlen=512,
    )
    chart_overlap = render_overlay([wave, wave], 30, 4, True)
    for r, row in enumerate(chart_overlap):
        line = []
        for ch, owner in row:
            if owner == MIX_OWNER:
                line.append(f"{ANSI['mix']}{ch}{ANSI['reset']}")
            elif owner is not None:
                color_key = _CURVE_COLORS[owner]
                line.append(f"{ANSI[color_key]}{ch}{ANSI['reset']}")
            else:
                line.append(ch)
        print(f"Row {r}: " + "".join(line))

    print("\n" + "=" * 80)
    print("PATTERN 3: COMPACT 1-ROW vs 2-ROW COMPARISON (0% Util + 25% VRAM)")
    print("=" * 80)
    u0 = deque([0.0] * 30, maxlen=512)
    v25 = deque([25.0] * 30, maxlen=512)

    c1 = render_overlay([u0, v25], 25, 1, True)
    print("1-Row Chart (chart_h=1): Both dots preserved, alternating true colors:")
    line = []
    for ch, o in c1[0]:
        color = ANSI[_CURVE_COLORS[o]] if o is not None and o >= 0 else ANSI["dim"]
        line.append(f"{color}{ch}{ANSI['reset']}")
    print("".join(line))

    c2 = render_overlay([u0, v25], 25, 2, True)
    print("\n2-Row Chart (chart_h=2, tight screen fix):")
    for r, row in enumerate(c2):
        line = []
        for ch, o in row:
            color = ANSI[_CURVE_COLORS[o]] if o is not None and o >= 0 else ANSI["dim"]
            line.append(f"{color}{ch}{ANSI['reset']}")
        print(f"Row {r}: " + "".join(line))

    print("\n" + "=" * 80)
    print("PATTERN 4: ADJACENT HIGH CURVES (100% Util vs 90% VRAM)")
    print(
        "Top dots are Green (HCU), bottom dots are Magenta (VRAM) without mutual color swallowing"
    )
    print("=" * 80)
    u100 = deque([100.0] * 30, maxlen=512)
    v90 = deque([90.0] * 30, maxlen=512)
    c4 = render_overlay([u100, v90], 30, 4, True)
    for r, row in enumerate(c4):
        line = []
        for ch, o in row:
            if o == MIX_OWNER:
                line.append(f"{ANSI['mix']}{ch}{ANSI['reset']}")
            elif o is not None and o >= 0:
                line.append(f"{ANSI[_CURVE_COLORS[o]]}{ch}{ANSI['reset']}")
            else:
                line.append(ch)
        print(f"Row {r}: " + "".join(line))


if __name__ == "__main__":
    if "--patterns" in sys.argv:
        inspect_patterns()
    else:
        h = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 16
        w = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 90
        inspect_screen(h, w, ansi=True)
