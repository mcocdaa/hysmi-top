"""curses TUI: nvtop-style scrolling curves for Hygon DCU (hy-smi).

Each device gets a single chart with multiple overlaid curves (e.g. HCU
utilization and VRAM usage), drawn as smooth lines via braille dots.
"""

from __future__ import annotations

import curses
import locale
import time
from collections import deque

from .collect import HcuStats, collect_all

DEFAULT_CHART_H = 4
DEFAULT_REFRESH_MS = 1000
MIN_BLOCK_W = 28

_BRAILLE = 0x2800
_DOT_BITS = ((1, 8), (2, 16), (4, 32), (64, 128))

MIX_OWNER = -1  # owner marker: cell contains dots of two or more curves

_CURVE_COLORS = ("util", "vram", "proc", "title", "header", "status")


def _supports_utf8() -> bool:
    try:
        return "UTF-8" in (locale.nl_langinfo(locale.CODESET) or "").upper()
    except Exception:  # noqa: BLE001
        return False


def _line_owner(owner: list[list[int]], x0: int, y0: int, x1: int, y1: int, val: int) -> None:
    """Bresenham that marks grid[y][x] = val only where currently 0."""
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= y0 < len(owner) and 0 <= x0 < len(owner[0]) and owner[y0][x0] == 0:
            owner[y0][x0] = val
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def render_overlay(
    series_list: list[deque[float]], width: int, height: int, utf8: bool
) -> list[list[tuple[str, int | None]]]:
    """Overlay several 0..100 rolling series as lines on one grid.

    Returns ``height`` rows; each row is a list of ``(char, owner)`` where
    ``owner`` is the index of the curve that owns the cell (None if blank).
    Newer points are on the right; older ones scroll off to the left.
    """
    pix_w = width * 2
    pix_h = height * 4
    owner: list[list[int]] = [[0] * pix_w for _ in range(pix_h)]
    for ci, series in enumerate(series_list):
        val = ci + 1
        n = len(series)
        offset = max(0, n - width)
        pts: list[tuple[int, int]] = []
        for col in range(width):
            idx = offset + col
            if idx >= n:
                break
            value = max(0.0, min(100.0, series[idx]))
            y = round((100.0 - value) / 100.0 * (pix_h - 1))
            pts.append((col * 2, y))
        if not pts:
            continue
        if len(pts) == 1:
            x, y = pts[0]
            if 0 <= y < pix_h and 0 <= x < pix_w and owner[y][x] == 0:
                owner[y][x] = val
        else:
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                _line_owner(owner, x0, y0, x1, y1, val)
    rows: list[list[tuple[str, int | None]]] = []
    for g in range(height):
        cells: list[tuple[str, int | None]] = []
        for c in range(width):
            owners: set[int] = set()
            top_dot = 0
            for dr in range(4):
                for dc in range(2):
                    o = owner[g * 4 + dr][c * 2 + dc]
                    if o:
                        owners.add(o)
                        if top_dot == 0:
                            top_dot = _DOT_BITS[dr][dc]
            if owners:
                if len(owners) > 1:
                    # merged curves: collapse to a single dot
                    cells.append((chr(_BRAILLE + top_dot) if utf8 else "*", MIX_OWNER))
                else:
                    mask = 0
                    for dr in range(4):
                        for dc in range(2):
                            if owner[g * 4 + dr][c * 2 + dc]:
                                mask |= _DOT_BITS[dr][dc]
                    cells.append((chr(_BRAILLE + mask) if utf8 else "*", owners.pop() - 1))
            else:
                cells.append((" ", None))
        rows.append(cells)
    return rows


def _pod_layout(maxx: int, per_row: int) -> tuple[int, int]:
    """Distribute the row into pods separated by gaps.

    Returns ``(gap_w, pod_width)``: roughly 10% of the terminal width goes to
    inter-pod gaps, split evenly across the ``per_row - 1`` gaps.
    """
    if per_row <= 1:
        return 0, maxx
    gap_w = max(1, int(maxx * 0.1) // (per_row - 1))
    gap_w = min(gap_w, maxx // 2)
    width = (maxx - gap_w * (per_row - 1)) // per_row
    return gap_w, width


def _fmt_mem(bytes_: int) -> str:
    if bytes_ <= 0:
        return "0.0G"
    val = bytes_ / (1024**3)
    if val >= 100:
        return f"{val:.0f}G"
    return f"{val:.1f}G"


def _init_colors() -> dict[str, int]:
    if not curses.has_colors():
        return {}
    curses.start_color()
    curses.use_default_colors()
    pairs = {
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
    curses.init_pair(pairs["title"], curses.COLOR_CYAN, -1)
    curses.init_pair(pairs["header"], curses.COLOR_WHITE, -1)
    curses.init_pair(pairs["util"], curses.COLOR_GREEN, -1)
    curses.init_pair(pairs["vram"], curses.COLOR_MAGENTA, -1)
    curses.init_pair(pairs["status"], curses.COLOR_YELLOW, -1)
    curses.init_pair(pairs["proc"], curses.COLOR_CYAN, -1)
    curses.init_pair(pairs["dim"], curses.COLOR_BLACK, -1)
    curses.init_pair(pairs["err"], curses.COLOR_RED, -1)
    curses.init_pair(pairs["mix"], curses.COLOR_BLUE, -1)
    return pairs


class HySmiTop:
    def __init__(self, device_ids: list[int], refresh_ms: int, chart_h: int | None = None):
        self.device_ids = device_ids
        self.refresh_ms = refresh_ms
        self.chart_h = chart_h
        self.util: dict[int, deque[float]] = {d: deque(maxlen=512) for d in device_ids}
        self.vram: dict[int, deque[float]] = {d: deque(maxlen=512) for d in device_ids}
        self.last_stats: dict[int, HcuStats] = {}

    def poll(self) -> None:
        stats = collect_all(self.device_ids)
        for s in stats:
            self.last_stats[s.hcu_id] = s
            self.util[s.hcu_id].append(s.util_percent)
            self.vram[s.hcu_id].append(s.vram_percent)

    def run(self, scr) -> int:
        curses.curs_set(0)
        utf8 = _supports_utf8()
        colors = _init_colors()
        scr.nodelay(True)
        last_update = 0.0
        while True:
            now = time.time()
            key = scr.getch()
            if key in (ord("q"), ord("Q"), 27):
                return 0
            if key in (ord("+"), ord("=")):
                self.refresh_ms = max(200, self.refresh_ms - 200)
            if key in (ord("-"), ord("_")):
                self.refresh_ms = min(10000, self.refresh_ms + 200)
            if now - last_update >= self.refresh_ms / 1000.0:
                self.poll()
                self._draw(scr, utf8, colors)
                scr.refresh()
                last_update = now
            time.sleep(0.05)

    def _layout(self, maxy: int, maxx: int, ndev: int) -> tuple[int, int, int, int]:
        """Fit ``ndev`` device blocks into the terminal.

        Returns ``(per_row, nrows, base_block_h, extra_rows)``: every block
        row gets ``base_block_h`` rows plus one extra row for the first
        ``extra_rows`` block rows, so the whole height is used with no gaps.
        If blocks cannot fit even at the minimum block height, ``base_block_h``
        is below 4 and the caller shows an overflow message.
        """
        avail_h = maxy - 2
        per_row = min(ndev, max(1, maxx // MIN_BLOCK_W))
        while True:
            nrows = (ndev + per_row - 1) // per_row
            base = avail_h // nrows
            if base >= 4:
                return per_row, nrows, base, avail_h % nrows
            if per_row < ndev:
                per_row = min(ndev, per_row * 2)
                continue
            return per_row, nrows, base, 0

    def _draw(self, scr, utf8: bool, colors: dict[str, int]) -> None:
        scr.erase()
        maxy, maxx = scr.getmaxyx()
        if maxy < 6 or maxx < 12:
            scr.addstr(0, 0, "terminal too small")
            return

        def attr(name: str) -> int:
            return curses.color_pair(colors[name]) if name in colors else 0

        def put(y: int, x: int, text: str, name: str = "dim", limit: int | None = None) -> None:
            if y < 0 or y >= maxy or x >= maxx:
                return
            right = min(maxx, limit) if limit is not None else maxx
            text = text[: max(0, right - x)]
            if not text:
                return
            try:
                scr.addstr(y, x, text, attr(name))
            except curses.error:
                pass

        row = 0
        put(row, 0, "hysmi-top  -  Hygon DCU monitor", "title")
        row += 1
        put(row, 0, "q:quit  +/-:speed", "dim")
        row += 1

        devs = [s for s in self.last_stats.values()]
        if not devs:
            put(row, 0, "no HCU devices found", "err")
            return
        per_row, _nrows, base, extra = self._layout(maxy, maxx, len(devs))
        if base < 4:
            put(row, 0, f"terminal too small for {len(devs)} cards; enlarge window", "err")
            return
        gap_w, width = _pod_layout(maxx, per_row)
        stride = width + gap_w
        for i, s in enumerate(sorted(devs, key=lambda x: x.hcu_id)):
            brow = i // per_row
            block_h = base + (1 if brow < extra else 0)
            top = row + brow * base + min(brow, extra)
            chart_h = block_h - 3
            if self.chart_h is not None:
                chart_h = min(chart_h, self.chart_h)
            self._draw_block(scr, s, top, (i % per_row) * stride, width, chart_h, utf8, put)

    def _draw_block(
        self, scr, s: HcuStats, top: int, left: int, width: int, chart_h: int, utf8: bool, put
    ) -> None:
        right = left + width
        chart_w = max(4, width - 2)
        hdr = f"HCU {s.hcu_id}"
        if not s.ok:
            put(top, left, hdr, "err", right)
            put(top + 1, left, f"error: {s.error}", "err", right)
            return
        put(top, left, hdr, "header", right)
        x = left + len(hdr)
        put(top, x, "  \u2500", "util", right)
        x += 3
        put(top, x, "HCU%", "header", right)
        x += 4
        put(top, x, " \u2500", "vram", right)
        x += 2
        put(top, x, "VRAM%", "header", right)
        x += 5
        put(top, x, f" u={s.util_percent:4.1f}%", "status", right)

        chart = render_overlay([self.util[s.hcu_id], self.vram[s.hcu_id]], chart_w, chart_h, utf8)
        for r, cells in enumerate(chart):
            y = top + 1 + r
            x = left
            i = 0
            while i < len(cells) and y < curses.LINES:
                owner = cells[i][1]
                j = i
                while j < len(cells) and cells[j][1] == owner:
                    j += 1
                if owner is None:
                    color = "dim"
                elif owner == MIX_OWNER:
                    color = "mix"
                else:
                    color = _CURVE_COLORS[owner]
                put(y, x, "".join(cells[k][0] for k in range(i, j)), color, right)
                x += j - i
                i = j
        status = f"v={_fmt_mem(s.vram_used)}/{_fmt_mem(s.vram_total)}  {s.temp_c:4.1f}C {s.power_w:5.1f}W"
        put(top + 1 + chart_h, left, status, "status", right)
