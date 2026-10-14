from __future__ import annotations

import curses
import io
import json
import sys
import tempfile
import unittest
from collections import deque
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from visual_test import ANSI, ScreenBuffer  # noqa: E402

from hysmi_top import cli, collect  # noqa: E402
from hysmi_top.ui import MIX_OWNER, HySmiTop, render_multi_braille, render_overlay  # noqa: E402


class CliAndCollectRemediationTest(unittest.TestCase):
    def test_parse_devices_validation(self) -> None:
        self.assertEqual(cli._parse_devices("-1"), [])
        self.assertEqual(cli._parse_devices("abc"), [])
        self.assertEqual(cli._parse_devices("5-2"), [])
        self.assertEqual(cli._parse_devices("-1,0,abc,2-3,5-2"), [0, 2, 3])
        huge = cli._parse_devices("0-999999")
        self.assertEqual(len(huge), 256)
        self.assertEqual(huge[0], 0)
        self.assertEqual(huge[-1], 255)

    def test_multi_gpu_hcu_index_regex_and_read_processes(self) -> None:
        out = (
            "PIDs for KFD processes:\n\n"
            "PID: 24680\n"
            "\tPASID: 1\n"
            "\tHCU Index: ['0', '1']\n"
            "\tVRAM USED(MiB): 2048\n"
        )
        with (
            mock.patch.object(collect.subprocess, "run", return_value=mock.Mock(stdout=out)),
            mock.patch("hysmi_top.collect._proc_name", return_value="torchrun"),
        ):
            procs = collect.read_processes()
        self.assertEqual(len(procs), 1)
        self.assertEqual(procs[0].pid, 24680)
        self.assertEqual(procs[0].device, 0)
        self.assertEqual(procs[0].name, "torchrun")
        self.assertEqual(procs[0].memory, 2048 * 1024**2)

    def test_power1_input_fallback_and_driver_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            drm = tmp / "sys" / "class" / "drm"
            dev = drm / "card1" / "device"
            dev.mkdir(parents=True)
            (dev / "driver").symlink_to(
                "../../../../../bus/pci/drivers/hydcu", target_is_directory=False
            )
            (dev / "gpu_busy_percent").write_text("55")
            (dev / "mem_info_vram_used").write_text(str(4 * 1024**3))
            (dev / "mem_info_vram_total").write_text(str(32 * 1024**3))
            hw = dev / "hwmon" / "hwmon0"
            hw.mkdir(parents=True)
            (hw / "temp1_input").write_text("52000")
            # No power1_average file; only power1_input is present
            (hw / "power1_input").write_text("145000000")
            (hw / "freq1_input").write_text("1200000000")
            (hw / "freq2_input").write_text("1600000000")

            with mock.patch.object(collect, "DRM_DIR", drm):
                cards = collect.discover_devices()
                self.assertEqual(len(cards), 1)
                stats = collect.read_device(0)
                self.assertTrue(stats.ok)
                self.assertAlmostEqual(stats.power_w, 145.0)
                self.assertAlmostEqual(stats.sclk_mhz, 1200.0)

    def test_snapshot_process_device_and_memory_mib_fields(self) -> None:
        fake_stats = [
            collect.HcuStats(
                hcu_id=0,
                util_percent=25.0,
                vram_used=1024**3,
                vram_total=32 * 1024**3,
                temp_milli=40000,
                power_uw=100_000_000,
                sclk_hz=1000_000_000,
                mclk_hz=1600_000_000,
            )
        ]
        fake_procs = [collect.HcuProcess(pid=4321, device=1, name="python3", memory=1024)]
        with (
            mock.patch("hysmi_top.cli.collect_all", return_value=fake_stats),
            mock.patch("hysmi_top.cli.read_processes", return_value=fake_procs),
        ):
            buf_json = io.StringIO()
            with redirect_stdout(buf_json):
                rc = cli._snapshot([0], as_json=True, demo=False)
            self.assertEqual(rc, 0)
            payload = json.loads(buf_json.getvalue())
            self.assertEqual(len(payload["processes"]), 1)
            proc_obj = payload["processes"][0]
            self.assertEqual(proc_obj["pid"], 4321)
            self.assertEqual(proc_obj["name"], "python3")
            self.assertEqual(proc_obj["device"], 1)
            self.assertEqual(proc_obj["memory_mib"], 1024)

            buf_txt = io.StringIO()
            with redirect_stdout(buf_txt):
                rc_txt = cli._snapshot([0], as_json=False, demo=False)
            self.assertEqual(rc_txt, 0)
            txt_out = buf_txt.getvalue()
            self.assertIn("proc 4321: python3 (HCU 1, 1024 MiB)", txt_out)


class UiRemediationTest(unittest.TestCase):
    def test_overlapping_braille_curve_bitmask_continuity(self) -> None:
        s1 = deque([50.0, 50.0, 50.0, 50.0])
        s2 = deque([50.0, 50.0, 50.0, 50.0])
        rows = render_multi_braille([s1, s2], width=4, height=4, utf8=True)
        mix_cells = [
            (c, ch) for r in range(4) for c, (ch, owner) in enumerate(rows[r]) if owner == MIX_OWNER
        ]
        self.assertEqual(len(mix_cells), 4)
        for _c, ch in mix_cells:
            bits = ord(ch) - 0x2800
            # Both left and right sub-column bits must be set for a continuous line
            self.assertTrue(bits & (1 | 2 | 4 | 64), "left sub-column bit missing")
            self.assertTrue(bits & (8 | 16 | 32 | 128), "right sub-column bit missing")

    def test_short_series_right_alignment(self) -> None:
        s = deque([100.0, 100.0])
        rows = render_overlay([s], width=5, height=4, utf8=True)
        # First 3 columns must be empty; last 2 columns must have the curve
        for c in range(3):
            self.assertEqual(rows[0][c], (" ", None))
        for c in (3, 4):
            ch, owner = rows[0][c]
            self.assertEqual(owner, 0)
            bits = ord(ch) - 0x2800
            self.assertEqual(bin(bits).count("1"), 2)

    def test_layout_zero_devices_and_linear_stepping(self) -> None:
        top = HySmiTop([], refresh_ms=10, chart_h=0)
        self.assertEqual(top.refresh_ms, 100)
        self.assertEqual(top.chart_h, 1)
        self.assertEqual(top._layout(20, 80, 0), (1, 1, 18, 0))
        # 8 devices in 14x80 should step per_row from 2 to 3 (not jump to 4)
        per_row, nrows, base, _extra = top._layout(14, 80, 8)
        self.assertEqual((per_row, nrows, base), (3, 3, 4))

    def test_status_line_surfaces_sclk_and_visual_ansi_mix(self) -> None:
        top = HySmiTop([0], refresh_ms=50000, chart_h=-5)
        self.assertEqual(top.refresh_ms, 10000)
        self.assertEqual(top.chart_h, 1)
        top.last_stats[0] = collect.HcuStats(
            hcu_id=0,
            util_percent=50.0,
            vram_used=32 * 1024**3,
            vram_total=64 * 1024**3,
            temp_milli=45000,
            power_uw=120_000_000,
            sclk_hz=1250_000_000,
        )
        top.util[0] = deque([50.0] * 10, maxlen=512)
        top.vram[0] = deque([50.0] * 10, maxlen=512)

        scr = ScreenBuffer(10, 60)
        colors = {"title": 1, "header": 2, "util": 3, "vram": 4, "status": 5, "dim": 7, "mix": 9}
        old_color_pair = getattr(curses, "color_pair", None)
        curses.color_pair = lambda p: p
        try:
            top._draw(scr, True, colors)
        finally:
            if old_color_pair is not None:
                curses.color_pair = old_color_pair

        buf = io.StringIO()
        with redirect_stdout(buf):
            scr.print_ansi(colors)
        ansi_out = buf.getvalue()
        self.assertIn("1250MHz", ansi_out)
        self.assertIn(ANSI["mix"], ansi_out)


if __name__ == "__main__":
    unittest.main()
