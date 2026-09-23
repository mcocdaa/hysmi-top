from __future__ import annotations

import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

from hysmi_top import collect
from hysmi_top.ui import MIX_OWNER, HySmiTop, _fmt_mem, _pod_layout, render_overlay


def make_sysfs(tmp: Path, ncards: int = 2) -> None:
    (tmp / "sys" / "class" / "drm").mkdir(parents=True)
    for c in range(1, ncards + 1):
        dev = tmp / "sys" / "class" / "drm" / f"card{c}" / "device"
        (dev / "driver").mkdir(parents=True, exist_ok=True)
        # make driver a symlink named hycu, as in real sysfs
        (dev / "driver").rmdir()
        (dev / "driver").symlink_to(
            "../../../../../bus/pci/drivers/hycu", target_is_directory=False
        )
        (dev / "gpu_busy_percent").write_text("12")
        (dev / "mem_info_vram_used").write_text(str(2 * 1024**3))
        (dev / "mem_info_vram_total").write_text(str(64 * 1024**3))
        hw = dev / "hwmon" / "hwmon0"
        hw.mkdir(parents=True)
        (hw / "temp1_input").write_text("45000")
        (hw / "power1_average").write_text("132000000")
        (hw / "freq1_input").write_text("592000000")
        (hw / "freq2_input").write_text("1800000000")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._temp_dir.name)
        make_sysfs(self.tmp)
        self.patcher = mock.patch.object(collect, "DRM_DIR", self.tmp / "sys" / "class" / "drm")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self._temp_dir.cleanup()

    def test_discover_devices(self):
        cards = collect.discover_devices()
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].name, "card1")

    def test_read_device(self):
        s = collect.read_device(0)
        self.assertTrue(s.ok)
        self.assertEqual(s.util_percent, 12.0)
        self.assertEqual(s.vram_used, 2 * 1024**3)
        self.assertEqual(s.vram_total, 64 * 1024**3)
        self.assertAlmostEqual(s.vram_percent, 3.125, places=3)
        self.assertAlmostEqual(s.temp_c, 45.0)
        self.assertAlmostEqual(s.power_w, 132.0)
        self.assertAlmostEqual(s.sclk_mhz, 592.0)
        self.assertAlmostEqual(s.mclk_mhz, 1800.0)

    def test_read_device_missing(self):
        s = collect.read_device(99)
        self.assertFalse(s.ok)
        self.assertIn("no sysfs device", s.error)

    def test_read_device_negative_index(self):
        s = collect.read_device(-1)
        self.assertFalse(s.ok)
        self.assertIn("no sysfs device", s.error)

    def test_collect_all(self):
        stats = collect.collect_all([0, 1])
        self.assertEqual([s.hcu_id for s in stats], [0, 1])

    def test_read_processes_empty(self):
        with mock.patch.object(
            collect.subprocess,
            "run",
            return_value=mock.Mock(stdout="No KFD PIDs currently running!"),
        ):
            self.assertEqual(collect.read_processes(), [])

    def test_read_processes_parse(self):
        out = (
            "PIDs for KFD processes:\n\n"
            "PID: 12345\n"
            "\tPASID: 1\n"
            "\tHCU Index: ['2']\n"
            "\tGPUID: ['42']\n"
            "\tVRAM USED(MiB): 512\n"
            "\tSDMA USED: 0\n"
            "PID: 67890\n"
            "\tHCU Index: ['5']\n"
            "\tVRAM USED(MiB): 1024\n"
        )
        with (
            mock.patch.object(collect.subprocess, "run", return_value=mock.Mock(stdout=out)),
            mock.patch("hysmi_top.collect._proc_name", return_value="python"),
        ):
            procs = collect.read_processes()
        self.assertEqual(len(procs), 2)
        self.assertEqual(procs[0].pid, 12345)
        self.assertEqual(procs[0].device, 2)
        self.assertEqual(procs[0].memory, 512 * 1024**2)
        self.assertEqual(procs[1].pid, 67890)
        self.assertEqual(procs[1].device, 5)


class UiTest(unittest.TestCase):
    def test_overlay_scrolling(self):
        series = deque([0.0, 50.0, 100.0])
        rows = render_overlay([series], width=3, height=4, utf8=True)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(rows[0]), 3)
        self.assertEqual(len(rows[3]), 3)
        # 100% point at the last column should draw dots in the top cell
        top_cell = rows[0][2]
        self.assertNotEqual(top_cell[0], " ")
        self.assertEqual(top_cell[1], 0)

    def test_overlay_empty(self):
        rows = render_overlay([deque(), deque()], width=5, height=4, utf8=True)
        for row in rows:
            for ch, owner in row:
                self.assertEqual(ch, " ")
                self.assertIsNone(owner)

    def test_overlay_ascii_fallback(self):
        rows = render_overlay([deque([100.0])], width=3, height=4, utf8=False)
        self.assertEqual(rows[0][0][0], "*")

    def test_overlay_two_curves(self):
        rows = render_overlay([deque([0.0] * 8), deque([100.0] * 8)], width=4, height=4, utf8=True)
        owners = {rows[r][c][1] for r in range(4) for c in range(4)}
        self.assertIn(0, owners)  # low curve (vram=0) present
        self.assertIn(1, owners)  # high curve (vram=100) present

    def test_overlay_merged_mix_single_dot(self):
        # exactly identical series (complete overlap) -> single dot and blue mix
        rows = render_overlay([deque([60.0] * 8), deque([60.0] * 8)], width=4, height=4, utf8=True)
        owners = {rows[r][c][1] for r in range(4) for c in range(4)}
        self.assertIn(MIX_OWNER, owners)
        self.assertNotIn(0, owners)
        self.assertNotIn(1, owners)
        # every merged cell must be a single dot, not a double dot
        for r in range(4):
            for c in range(4):
                ch, owner = rows[r][c]
                if owner == MIX_OWNER:
                    self.assertEqual(bin(ord(ch) - 0x2800).count("1"), 1)

    def test_overlay_adjacent_curves_not_blue(self):
        # adjacent but non-identical curves (60.0% vs 65.0%) must NOT be blue
        rows = render_overlay([deque([60.0] * 8), deque([65.0] * 8)], width=4, height=4, utf8=True)
        owners = {rows[r][c][1] for r in range(4) for c in range(4)}
        self.assertNotIn(MIX_OWNER, owners)
        self.assertIn(0, owners)

    def test_overlay_exact_identical_curves_trigger_mix(self):
        # exactly identical series (e.g. both 50.0% or both 0.0%) must trigger MIX_OWNER (blue)
        rows = render_overlay([deque([50.0] * 8), deque([50.0] * 8)], width=4, height=4, utf8=True)
        owners = {rows[r][c][1] for r in range(4) for c in range(4)}
        self.assertIn(MIX_OWNER, owners)
        self.assertNotIn(0, owners)
        self.assertNotIn(1, owners)

    def test_overlay_compressed_chart_preserves_both_curves(self):
        # In a 1-row chart, 0% util and 20% vram share the same braille cell.
        # Both dots must be preserved, and non-overlapping curves must NOT be blue.
        rows = render_overlay([deque([0.0] * 8), deque([20.0] * 8)], width=4, height=1, utf8=True)
        self.assertEqual(len(rows), 1)
        for c in range(4):
            ch, owner = rows[0][c]
            self.assertEqual(owner, 0)
            dots = bin(ord(ch) - 0x2800).count("1")
            self.assertGreaterEqual(dots, 2, f"Both curves should be visible in cell: {ch}")

    def test_overlay_linear_crossing(self):
        # Crossing series with exact 50.0% overlap at center index 10
        u = deque([10.0 + 80.0 * (i / 20) for i in range(21)], maxlen=512)
        v = deque([90.0 - 80.0 * (i / 20) for i in range(21)], maxlen=512)
        chart = render_overlay([u, v], width=21, height=4, utf8=True)
        owners = {chart[r][c][1] for r in range(4) for c in range(21)}
        self.assertIn(0, owners)  # util present
        self.assertIn(1, owners)  # vram present
        self.assertIn(MIX_OWNER, owners)  # intersection detected as mix

    def test_demo_mode_poll(self):
        top = HySmiTop(list(range(8)), 1000, demo=True)
        self.assertEqual(len(top.last_stats), 8)
        self.assertEqual(len(top.util[0]), 35)
        # Ensure cards have varying workloads
        self.assertEqual(top.last_stats[6].util_percent, 0.0)
        self.assertEqual(top.last_stats[6].vram_percent, 25.0)
        self.assertEqual(top.last_stats[7].util_percent, 0.0)
        self.assertEqual(top.last_stats[7].vram_percent, 0.0)

    def test_pod_layout(self):
        self.assertEqual(_pod_layout(80, 1), (0, 80))
        self.assertEqual(_pod_layout(80, 2), (8, 36))
        self.assertEqual(_pod_layout(120, 3), (6, 36))
        self.assertEqual(_pod_layout(50, 2), (5, 22))
        self.assertEqual(_pod_layout(24, 4), (1, 5))

    def test_fmt_mem(self):
        self.assertEqual(_fmt_mem(64 * 1024**3), "64.0G")
        self.assertEqual(_fmt_mem(2 * 1024**3), "2.0G")
        self.assertEqual(_fmt_mem(0), "0.0G")


class LayoutTest(unittest.TestCase):
    DEVICES: tuple[int, ...] = tuple(range(8))

    def make(self, chart_h: int | None = None) -> HySmiTop:
        return HySmiTop(list(self.DEVICES), 1000, chart_h)

    def used_rows(self, maxy: int, maxx: int, ndev: int) -> int:
        _per_row, nrows, base, extra = self.make()._layout(maxy, maxx, ndev)
        return 2 + nrows * base + extra if base >= 4 else -1

    def test_layout_fills_every_row(self):
        for maxy in range(6, 60):
            for maxx in (60, 80, 120, 200):
                used = self.used_rows(maxy, maxx, 8)
                if used != -1:
                    self.assertEqual(used, maxy, f"gap at {maxy}x{maxx}")

    def test_tall_window_spreads_extra_rows(self):
        self.assertEqual(self.make()._layout(40, 120, 8), (4, 2, 19, 0))
        self.assertEqual(self.make()._layout(45, 120, 8), (4, 2, 21, 1))

    def test_short_window_compresses_y(self):
        self.assertEqual(self.make()._layout(20, 80, 8), (2, 4, 4, 2))

    def test_very_short_window_compresses_x_then_y(self):
        self.assertEqual(self.make()._layout(12, 80, 8), (4, 2, 5, 0))

    def test_tiny_window_reports_overflow(self):
        _per_row, _nrows, base, _extra = self.make()._layout(5, 80, 8)
        self.assertLess(base, 4)  # cannot fit even at minimum block height

    def test_draw_tight_layout_bottom_row_chart_height(self):
        top = self.make()
        for d in range(8):
            top.last_stats[d] = collect.HcuStats(
                hcu_id=d,
                util_percent=0.0 if d >= 6 else 50.0,
                vram_used=int(0.25 * 64 * 1024**3 if d == 6 else 0),
                vram_total=64 * 1024**3,
                temp_milli=45000,
                power_uw=120_000_000,
            )
            top.util[d] = deque([0.0 if d >= 6 else 50.0] * 20, maxlen=512)
            top.vram[d] = deque([25.0 if d == 6 else 0.0] * 20, maxlen=512)

        blocks_drawn = []
        orig_draw_block = top._draw_block

        def mock_draw_block(scr, s, top_y, left_x, width, chart_h, utf8, put, maxy=None):
            blocks_drawn.append((s.hcu_id, chart_h))
            orig_draw_block(scr, s, top_y, left_x, width, chart_h, utf8, put, maxy)

        top._draw_block = mock_draw_block

        scr = mock.Mock()
        scr.getmaxyx.return_value = (16, 90)
        top._draw(scr, True, {})
        for hcu_id, chart_h in blocks_drawn:
            self.assertEqual(chart_h, 2, f"HCU {hcu_id} chart_h should be 2, got {chart_h}")


if __name__ == "__main__":
    unittest.main()
