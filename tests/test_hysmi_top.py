import unittest
from collections import deque
from pathlib import Path
from unittest import mock

from hysmi_top import collect
from hysmi_top.ui import HySmiTop, MIX_OWNER, _fmt_mem, _pod_layout, render_overlay


def make_sysfs(tmp: Path, ncards: int = 2) -> None:
    (tmp / "sys" / "class" / "drm").mkdir(parents=True)
    for c in range(1, ncards + 1):
        dev = tmp / "sys" / "class" / "drm" / f"card{c}" / "device"
        (dev / "driver").mkdir(parents=True, exist_ok=True)
        # make driver a symlink named hycu, as in real sysfs
        (dev / "driver").rmdir()
        (dev / "driver").symlink_to("../../../../../bus/pci/drivers/hycu", target_is_directory=False)
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
        self.tmp = Path("/tmp/hysmi_top_test_sysfs")
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        make_sysfs(self.tmp)
        self.patcher = mock.patch.object(collect, "DRM_DIR", self.tmp / "sys" / "class" / "drm")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

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

    def test_collect_all(self):
        stats = collect.collect_all([0, 1])
        self.assertEqual([s.hcu_id for s in stats], [0, 1])

    def test_read_processes_empty(self):
        with mock.patch.object(collect.subprocess, "run", return_value=mock.Mock(stdout="No KFD PIDs currently running!")):
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
        with mock.patch.object(collect.subprocess, "run", return_value=mock.Mock(stdout=out)):
            with mock.patch("hysmi_top.collect._proc_name", return_value="python"):
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
        rows = render_overlay(
            [deque([0.0] * 8), deque([100.0] * 8)], width=4, height=4, utf8=True
        )
        owners = {rows[r][c][1] for r in range(4) for c in range(4)}
        self.assertIn(0, owners)  # low curve (vram=0) present
        self.assertIn(1, owners)  # high curve (vram=100) present

    def test_overlay_merged_mix_single_dot(self):
        # two close series landing in the same braille cell band -> blue mix
        rows = render_overlay(
            [deque([60.0] * 8), deque([65.0] * 8)], width=4, height=4, utf8=True
        )
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
    DEVICES = list(range(8))

    def make(self, chart_h: int = 4) -> HySmiTop:
        return HySmiTop(self.DEVICES, 1000, chart_h)

    def test_tall_window_grows_chart_to_fill(self):
        per_row, chart_h, nrows = self.make()._layout(40, 120, 8)
        self.assertEqual((per_row, chart_h, nrows), (4, 8, 2))
        self.assertEqual((2 + chart_h + 1) * nrows, 22)  # fits in 37 avail rows

    def test_short_window_compresses_y(self):
        per_row, chart_h, nrows = self.make()._layout(20, 80, 8)
        self.assertEqual((per_row, chart_h, nrows), (2, 1, 4))
        self.assertEqual((2 + chart_h + 1) * nrows, 16)  # fits in 17 avail rows

    def test_very_short_window_compresses_x_then_y(self):
        per_row, chart_h, nrows = self.make()._layout(12, 80, 8)
        self.assertEqual((per_row, chart_h, nrows), (4, 1, 2))
        self.assertEqual((2 + chart_h + 1) * nrows, 8)  # fits in 9 avail rows

    def test_all_in_one_row_when_wide(self):
        per_row, chart_h, nrows = self.make()._layout(10, 200, 8)
        self.assertEqual((per_row, chart_h, nrows), (8, 4, 1))
        self.assertEqual((2 + chart_h + 1) * nrows, 7)  # fits in 7 avail rows

    def test_tiny_window_reports_overflow(self):
        per_row, chart_h, nrows = self.make()._layout(6, 80, 8)
        self.assertGreater((2 + chart_h + 1) * nrows, 6 - 3)  # cannot fit

    def test_requested_chart_height_is_minimum(self):
        app = self.make(chart_h=2)
        per_row, chart_h, nrows = app._layout(40, 120, 8)
        self.assertGreaterEqual(chart_h, 2)  # grows to fill screen, never below request


if __name__ == "__main__":
    unittest.main()
