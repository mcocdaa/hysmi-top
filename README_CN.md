<div align="center">

# hysmi-top

**零依赖、盲文双曲线叠加滚动的海光 Hygon DCU (`hy-smi`) 终端监控工具。**

[![CI](https://github.com/mcocdaa/hysmi-top/actions/workflows/ci.yml/badge.svg)](https://github.com/mcocdaa/hysmi-top/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mcocdaa/hysmi-top)](https://github.com/mcocdaa/hysmi-top/releases)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[English](README.md) · [简体中文](README_CN.md)

<img src="docs/assets/demo_tui.gif" alt="hysmi-top demo" width="840">

</div>

## ✨ 核心特性

- **零第三方依赖** — 仅使用 Python 标准库（`curses`），直接读取内核 `sysfs`（`/sys/class/drm/cardN/device/`）实时指标，无需 root 权限、无子进程轮询开销。
- **平滑盲文双曲线叠加** — 同图实时绘制 `─HCU%`（算力利用率，绿）与 `─VRAM%`（显存占用，品红）滚动曲线；两线重合点自动合并为高亮蓝点（`mix`），消除双线重影。
- **自适应多列 Pod 网格** — 随终端窗口缩放自动调整曲线高度与每行多列布局（按终端宽度动态分配列间距），最大化利用屏幕空间。
- **内置无硬件 `--demo` 模式** — 无需物理海光 DCU 硬件即可一键预览 8 卡动态负载曲线（正弦、余弦、阶梯、锯齿、脉冲与重合模式）。
- **脚本化快照与 JSON 导出** — 支持 `--once` 单帧文本快照与 `--once --json` 结构化输出（含 `hy-smi --showpids` 活跃进程信息），便于集群巡检与 CI 采集。

## 🚀 快速开始

**免安装直接运行（`uvx`）：**

```bash
uvx --from git+https://github.com/mcocdaa/hysmi-top.git hysmi-top
uvx --from git+https://github.com/mcocdaa/hysmi-top.git hysmi-top --demo
```

**全局安装（`uv` / `pip`）：**

```bash
uv tool install git+https://github.com/mcocdaa/hysmi-top.git
# 或使用 pip：pip install git+https://github.com/mcocdaa/hysmi-top.git
```

**内网 / 离线 DCU 集群安装（Wheel）：**

从 [GitHub Releases](https://github.com/mcocdaa/hysmi-top/releases) 下载 `hysmi_top-0.2.3-py3-none-any.whl` 并拷贝至目标计算节点：

```bash
uv tool install ./hysmi_top-0.2.3-py3-none-any.whl
```

## ⌨️ 常用命令与快捷键

```bash
hysmi-top                      # 监控所有检测到的海光 DCU 加速卡
hysmi-top -d 0-3 -r 500 -c 6   # 指定卡 0..3，500ms 刷新间隔，限制曲线最大高度 6 行
hysmi-top --demo               # 启动 8 卡交互式模拟演示模式（无需物理硬件）
hysmi-top --once               # 输出单帧文本快照并退出
hysmi-top --once --json        # 输出单帧 JSON 指标与进程快照
```

| 按键 / 参数 | 说明 |
| :--- | :--- |
| `q` | 退出交互式终端监控界面 |
| `+` / `=` | 加快刷新频率（每次 `-100ms`，最快 `100ms`） |
| `-` / `_` | 减慢刷新频率（每次 `+100ms`，最慢 `10000ms`） |
| `-d, --devices` | 指定监控的 HCU 卡号列表或范围，如 `0,2-4`（默认：全部） |
| `-r, --refresh` | 刷新间隔毫秒数（默认：`1000`） |
| `-c, --chart-height` | 限制单个图表曲线区域的最大行数（默认：自动填满） |

## 📄 开源协议

基于 [MIT License](LICENSE) 协议开源。
