"""Visual inspection tool for hysmi-top rendering.

Simulates terminal rendering of HySmiTop under configurable dimensions
(e.g., 90x16, 90x15, 80x24) with diverse workload profiles.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hysmi_top.collect import HcuStats
from hysmi_top.ui import HySmiTop

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
        # Map pair ID back to style name
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

    # Workload profile:
    # Cards 0-5 busy
    # Card 6: 0% util, 3.1% vram (real-world idle DCU state)
    # Card 7: 0% util, 0.0% vram (zero load)
    profiles = [
        (80.0, 60.0),
        (50.0, 45.0),
        (95.0, 85.0),
        (30.0, 40.0),
        (70.0, 20.0),
        (60.0, 60.0),
        (0.0, 25.0),  # HCU 6: 0% util, 25% vram (model loaded, idle compute)
        (0.0, 3.125),  # HCU 7: 0% util, 3.1% vram (real-world idle DCU state)
    ]

    for d, (u, v) in enumerate(profiles):
        top.last_stats[d] = HcuStats(
            hcu_id=d,
            util_percent=u,
            vram_used=int(v * 64 * 1024**3 / 100),
            vram_total=64 * 1024**3,
            temp_milli=45000,
            power_uw=120_000_000,
        )
        top.util[d] = deque([u] * 30, maxlen=512)
        top.vram[d] = deque([v] * 30, maxlen=512)

    scr = ScreenBuffer(maxy, maxx)

    # Mock curses.color_pair to return the pair number directly
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


if __name__ == "__main__":
    h = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    inspect_screen(h, w, ansi=True)
