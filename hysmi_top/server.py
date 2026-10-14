"""Zero-dependency HTTP API, Prometheus exporter, and live Web Dashboard for hysmi-top.

Follows the "Human via UI, AI via API, Single Canonical API" principle:
- AI orchestrators (AutoFlow / TermFlow) query ``GET /api/v1/snapshot``
- Prometheus scrapers query ``GET /metrics``
- Health checks query ``GET /api/v1/health``
- Humans open ``GET /`` in a browser, which polls ``GET /api/v1/snapshot``
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from . import collect
from .collect import HcuProcess, HcuStats, collect_all, read_processes
from .ui import DEFAULT_REFRESH_MS, HySmiTop


def _proc_mem_mib(raw_memory: int) -> float:
    if raw_memory >= 1024**2:
        return round(raw_memory / (1024**2), 2)
    return float(raw_memory)


def build_snapshot(stats: list[HcuStats], procs: list[HcuProcess]) -> dict[str, Any]:
    """Build the canonical JSON telemetry snapshot for both AI agents and the Web UI."""
    devices_payload: list[dict[str, Any]] = []
    idle_devices: list[int] = []
    total_vram_used_mib = 0.0
    total_vram_total_mib = 0.0

    for s in stats:
        used_mib = round(s.vram_used / (1024**2), 2)
        total_mib = round(s.vram_total / (1024**2), 2)
        total_vram_used_mib += used_mib
        total_vram_total_mib += total_mib
        if s.ok and s.util_percent < 10.0 and used_mib < 1024.0:
            idle_devices.append(s.hcu_id)
        devices_payload.append(
            {
                "hcu_id": s.hcu_id,
                "name": s.name,
                "util_percent": s.util_percent,
                "gfx_util": s.util_percent,
                "vram_used": s.vram_used,
                "vram_total": s.vram_total,
                "vram_used_mib": used_mib,
                "vram_total_mib": total_mib,
                "vram_percent": round(s.vram_percent, 2),
                "temp_c": s.temp_c,
                "power_w": s.power_w,
                "sclk_mhz": s.sclk_mhz,
                "mclk_mhz": s.mclk_mhz,
                "ok": s.ok,
                "error": s.error,
            }
        )

    avg_gfx_util = round(sum(s.util_percent for s in stats) / len(stats), 2) if stats else 0.0

    processes_payload = [
        {
            "pid": p.pid,
            "name": p.name,
            "device": p.device,
            "memory_mib": _proc_mem_mib(p.memory),
        }
        for p in procs
    ]

    return {
        "timestamp": time.time(),
        "devices": devices_payload,
        "processes": processes_payload,
        "summary": {
            "total_devices": len(stats),
            "avg_gfx_util": avg_gfx_util,
            "total_vram_used_mib": round(total_vram_used_mib, 2),
            "total_vram_total_mib": round(total_vram_total_mib, 2),
            "idle_devices": idle_devices,
        },
    }


def format_prometheus_metrics(stats: list[HcuStats]) -> str:
    """Render device telemetry in Prometheus text exposition format."""
    lines: list[str] = [
        "# HELP hysmi_gfx_util_percent DCU/GPU graphics utilization percentage.",
        "# TYPE hysmi_gfx_util_percent gauge",
        "# HELP hysmi_mem_used_mib DCU/GPU VRAM used in MiB.",
        "# TYPE hysmi_mem_used_mib gauge",
        "# HELP hysmi_mem_total_mib DCU/GPU total VRAM in MiB.",
        "# TYPE hysmi_mem_total_mib gauge",
        "# HELP hysmi_temp_c DCU/GPU temperature in degrees Celsius.",
        "# TYPE hysmi_temp_c gauge",
        "# HELP hysmi_power_w DCU/GPU power draw in Watts.",
        "# TYPE hysmi_power_w gauge",
        "# HELP hysmi_sclk_mhz DCU/GPU system/core clock in MHz.",
        "# TYPE hysmi_sclk_mhz gauge",
    ]
    for s in stats:
        dev = str(s.hcu_id)
        used_mib = round(s.vram_used / (1024**2), 2)
        total_mib = round(s.vram_total / (1024**2), 2)
        lines.append(f'hysmi_gfx_util_percent{{device="{dev}"}} {s.util_percent}')
        lines.append(f'hysmi_mem_used_mib{{device="{dev}"}} {used_mib}')
        lines.append(f'hysmi_mem_total_mib{{device="{dev}"}} {total_mib}')
        lines.append(f'hysmi_temp_c{{device="{dev}"}} {s.temp_c}')
        lines.append(f'hysmi_power_w{{device="{dev}"}} {s.power_w}')
        lines.append(f'hysmi_sclk_mhz{{device="{dev}"}} {s.sclk_mhz}')
    return "\n".join(lines) + "\n"


def render_dashboard_html(interval_ms: int = DEFAULT_REFRESH_MS) -> str:
    """Return the responsive dark-mode single-page Web Dashboard HTML."""
    poll_ms = max(200, int(interval_ms))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hysmi-top — Hygon DCU Live Telemetry &amp; AI API</title>
<style>
  :root {{
    --bg: #0b0f17;
    --panel: #131a27;
    --border: #233047;
    --text: #e6edf3;
    --muted: #8b9bb4;
    --gfx: #2ea043;
    --mem: #a371f7;
    --accent: #58a6ff;
    --warn: #d29922;
    --danger: #f85149;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 20px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
  }}
  header {{
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 16px;
  }}
  .title-group h1 {{
    margin: 0;
    font-size: 1.35rem;
    color: var(--accent);
  }}
  .title-group p {{
    margin: 4px 0 0;
    font-size: 0.85rem;
    color: var(--muted);
  }}
  .api-bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 0.82rem;
  }}
  .api-bar span {{
    color: var(--muted);
    font-weight: 600;
  }}
  .api-bar a {{
    color: var(--accent);
    text-decoration: none;
    background: rgba(88, 166, 255, 0.1);
    padding: 3px 8px;
    border-radius: 4px;
    border: 1px solid rgba(88, 166, 255, 0.25);
  }}
  .api-bar a:hover {{
    background: rgba(88, 166, 255, 0.2);
  }}
  .summary-strip {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 12px;
    margin-bottom: 18px;
  }}
  .summary-card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }}
  .summary-card .label {{
    font-size: 0.75rem;
    color: var(--muted);
    text-transform: uppercase;
  }}
  .summary-card .value {{
    font-size: 1.25rem;
    font-weight: 700;
    margin-top: 4px;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
    gap: 14px;
    margin-bottom: 22px;
  }}
  .card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
  }}
  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }}
  .card-title {{
    font-weight: 700;
    font-size: 1rem;
    color: var(--accent);
  }}
  .badges {{
    display: flex;
    gap: 6px;
    font-size: 0.76rem;
    color: var(--muted);
  }}
  .badge {{
    background: rgba(255, 255, 255, 0.05);
    padding: 2px 6px;
    border-radius: 4px;
  }}
  .bar-row {{
    margin-bottom: 8px;
  }}
  .bar-label {{
    display: flex;
    justify-content: space-between;
    font-size: 0.8rem;
    margin-bottom: 3px;
  }}
  .bar-track {{
    width: 100%;
    height: 8px;
    background: #1d2738;
    border-radius: 4px;
    overflow: hidden;
  }}
  .bar-fill-gfx {{
    height: 100%;
    background: var(--gfx);
    transition: width 0.2s ease;
  }}
  .bar-fill-mem {{
    height: 100%;
    background: var(--mem);
    transition: width 0.2s ease;
  }}
  svg.sparkline {{
    width: 100%;
    height: 64px;
    background: #0d131d;
    border-radius: 6px;
    border: 1px solid #1c2638;
    margin-top: 6px;
  }}
  .section-title {{
    font-size: 1rem;
    margin: 0 0 10px;
    color: var(--text);
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    font-size: 0.85rem;
  }}
  th, td {{
    text-align: left;
    padding: 9px 12px;
    border-bottom: 1px solid var(--border);
  }}
  th {{
    color: var(--muted);
    font-weight: 600;
    background: rgba(255, 255, 255, 0.02);
  }}
</style>
</head>
<body>
<header>
  <div class="title-group">
    <h1>hysmi-top Web Dashboard</h1>
    <p>Human via UI &bull; AI via API &bull; Single Canonical Telemetry Snapshot</p>
  </div>
  <div class="api-bar">
    <span>AI / Prometheus API Endpoints:</span>
    <a href="/api/v1/snapshot" target="_blank">/api/v1/snapshot</a>
    <a href="/metrics" target="_blank">/metrics</a>
    <a href="/api/v1/health" target="_blank">/api/v1/health</a>
  </div>
</header>

<section class="summary-strip">
  <div class="summary-card">
    <div class="label">Total DCUs / GPUs</div>
    <div class="value" id="sum-devices">-</div>
  </div>
  <div class="summary-card">
    <div class="label">Avg GFX% Utilization</div>
    <div class="value" id="sum-gfx">-</div>
  </div>
  <div class="summary-card">
    <div class="label">Total MEM% / VRAM</div>
    <div class="value" id="sum-vram">-</div>
  </div>
  <div class="summary-card">
    <div class="label">Idle Devices (AI Ready)</div>
    <div class="value" id="sum-idle">-</div>
  </div>
</section>

<div class="grid" id="devices-grid"></div>

<h2 class="section-title">Active Compute Processes</h2>
<table>
  <thead>
    <tr>
      <th>PID</th>
      <th>Name</th>
      <th>HCU</th>
      <th>VRAM MiB</th>
    </tr>
  </thead>
  <tbody id="proc-body">
    <tr><td colspan="4">Waiting for telemetry...</td></tr>
  </tbody>
</table>

<script>
const INTERVAL_MS = {poll_ms};
const MAX_HISTORY = 60;
const history = {{}};

function sparkPoints(arr, w, h) {{
  if (!arr || arr.length === 0) return "";
  return arr.map((v, i) => {{
    const x = (i / Math.max(1, MAX_HISTORY - 1)) * w;
    const y = h - (Math.max(0, Math.min(100, v)) / 100.0) * (h - 6) - 3;
    return `${{x.toFixed(1)}},${{y.toFixed(1)}}`;
  }}).join(" ");
}}

async function pollSnapshot() {{
  try {{
    const res = await fetch('/api/v1/snapshot');
    if (!res.ok) return;
    const data = await res.json();
    const sum = data.summary || {{}};
    document.getElementById('sum-devices').textContent = sum.total_devices ?? 0;
    document.getElementById('sum-gfx').textContent = `${{(sum.avg_gfx_util ?? 0).toFixed(1)}}%`;
    document.getElementById('sum-vram').textContent =
      `${{Math.round(sum.total_vram_used_mib ?? 0)}} / ${{Math.round(sum.total_vram_total_mib ?? 0)}} MiB`;
    document.getElementById('sum-idle').textContent =
      (sum.idle_devices && sum.idle_devices.length) ? sum.idle_devices.map(d => `HCU ${{d}}`).join(', ') : 'None';

    const grid = document.getElementById('devices-grid');
    grid.innerHTML = '';
    for (const d of (data.devices || [])) {{
      if (!history[d.hcu_id]) history[d.hcu_id] = {{ gfx: [], mem: [] }};
      const h = history[d.hcu_id];
      h.gfx.push(d.util_percent || 0);
      h.mem.push(d.vram_percent || 0);
      if (h.gfx.length > MAX_HISTORY) h.gfx.shift();
      if (h.mem.length > MAX_HISTORY) h.mem.shift();

      const card = document.createElement('div');
      card.className = 'card';
      const gfxPts = sparkPoints(h.gfx, 300, 64);
      const memPts = sparkPoints(h.mem, 300, 64);
      card.innerHTML = `
        <div class="card-header">
          <span class="card-title">HCU ${{d.hcu_id}}</span>
          <div class="badges">
            <span class="badge">${{(d.temp_c || 0).toFixed(1)}}&deg;C</span>
            <span class="badge">${{(d.power_w || 0).toFixed(1)}}W</span>
            <span class="badge">${{Math.round(d.sclk_mhz || 0)}}MHz</span>
          </div>
        </div>
        <div class="bar-row">
          <div class="bar-label"><span>GFX%</span><span>${{(d.util_percent || 0).toFixed(1)}}%</span></div>
          <div class="bar-track"><div class="bar-fill-gfx" style="width:${{Math.min(100, d.util_percent || 0)}}%"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-label">
            <span>MEM%</span>
            <span>${{Math.round(d.vram_used_mib || 0)}} / ${{Math.round(d.vram_total_mib || 0)}} MiB (${{(d.vram_percent || 0).toFixed(1)}}%)</span>
          </div>
          <div class="bar-track"><div class="bar-fill-mem" style="width:${{Math.min(100, d.vram_percent || 0)}}%"></div></div>
        </div>
        <svg class="sparkline" viewBox="0 0 300 64" preserveAspectRatio="none">
          <polyline fill="none" stroke="#2ea043" stroke-width="2" points="${{gfxPts}}" />
          <polyline fill="none" stroke="#a371f7" stroke-width="2" points="${{memPts}}" />
        </svg>
      `;
      grid.appendChild(card);
    }}

    const tbody = document.getElementById('proc-body');
    const procs = data.processes || [];
    if (procs.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="4">No active compute processes</td></tr>';
    }} else {{
      tbody.innerHTML = procs.map(p => `
        <tr>
          <td>${{p.pid}}</td>
          <td>${{p.name}}</td>
          <td>HCU ${{p.device}}</td>
          <td>${{p.memory_mib}}</td>
        </tr>
      `).join('');
    }}
  }} catch (err) {{
    console.error('Failed to fetch snapshot:', err);
  }}
}}

pollSnapshot();
setInterval(pollSnapshot, INTERVAL_MS);
</script>
</body>
</html>
"""


class HySmiHttpServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying hysmi-top configuration and demo state."""

    def __init__(
        self,
        server_address: tuple[str, int],
        device_ids: list[int] | None = None,
        refresh_ms: int = DEFAULT_REFRESH_MS,
        demo: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(server_address, HySmiRequestHandler)
        self.device_ids = device_ids
        self.refresh_ms = max(100, min(10000, refresh_ms))
        self.demo = demo
        self.quiet = quiet
        self._lock = threading.Lock()
        self._demo_top: HySmiTop | None = None
        if self.demo:
            ids = device_ids if device_ids is not None else list(range(8))
            self._demo_top = HySmiTop(ids, self.refresh_ms, demo=True)

    def collect_telemetry(self) -> tuple[list[HcuStats], list[HcuProcess]]:
        if self.demo and self._demo_top is not None:
            with self._lock:
                self._demo_top.poll()
                stats = [self._demo_top.last_stats[d] for d in self._demo_top.device_ids]
            return stats, []
        collect_fn = collect_all if collect_all is not collect.collect_all else collect.collect_all
        procs_fn = (
            read_processes
            if read_processes is not collect.read_processes
            else collect.read_processes
        )
        return collect_fn(self.device_ids), procs_fn()


class HySmiRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving canonical JSON API, Prometheus metrics, and Web UI."""

    server: HySmiHttpServer  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/v1/health":
            self._send_json({"status": "ok", "service": "hysmi-top"})
            return
        if path == "/api/v1/snapshot":
            stats, procs = self.server.collect_telemetry()
            self._send_json(build_snapshot(stats, procs))
            return
        if path == "/metrics":
            stats, _ = self.server.collect_telemetry()
            body = format_prometheus_metrics(stats).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/", "/index.html"):
            body = render_dashboard_html(self.server.refresh_ms).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)


def create_server(
    host: str = "127.0.0.1",
    port: int = 9120,
    device_ids: list[int] | None = None,
    refresh_ms: int = DEFAULT_REFRESH_MS,
    demo: bool = False,
    quiet: bool = True,
) -> HySmiHttpServer:
    """Create a configured HySmiHttpServer instance."""
    return HySmiHttpServer(
        (host, port),
        device_ids=device_ids,
        refresh_ms=refresh_ms,
        demo=demo,
        quiet=quiet,
    )


def serve(
    host: str = "127.0.0.1",
    port: int = 9120,
    device_ids: list[int] | None = None,
    refresh_ms: int = DEFAULT_REFRESH_MS,
    demo: bool = False,
) -> int:
    """Run the HTTP API & Web Dashboard server until interrupted."""
    if port < 0 or port > 65535:
        print(f"Invalid port: {port} (must be 0-65535)", file=sys.stderr)
        return 2
    httpd = create_server(
        host=host,
        port=port,
        device_ids=device_ids,
        refresh_ms=refresh_ms,
        demo=demo,
        quiet=False,
    )
    actual_port = httpd.server_port
    print(
        f"hysmi-top serving Web UI & AI API at http://{host}:{actual_port} "
        f"(snapshot: /api/v1/snapshot, metrics: /metrics)"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
