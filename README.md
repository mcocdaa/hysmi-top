# hysmi-top

[![CI](https://github.com/mcocdaa/hysmi-top/actions/workflows/ci.yml/badge.svg)](https://github.com/mcocdaa/hysmi-top/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](file:///home/mcocdaa/AI_CODE/hysmi-top/LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

终端 GPU 监控工具，专为**海光 Hygon DCU（hy-smi）**设计：每张卡一张图，
多条曲线（HCU 利用率、VRAM 占用）以**滚动线条**叠加显示，风格类似 nvtop。

零第三方依赖（仅 Python 标准库：`curses`），读取内核 sysfs 数据，无需
root、不轮询起子进程。

## 功能

- 多卡网格布局，每卡一个图表块，**自适应窗口尺寸**：曲线高度填满全部可用
  屏幕空间，窗口矮时自动压缩曲线高度（Y 方向），仍放不下则增加每行列数
  （X 方向），resize 后自动重排
- 叠加滚动曲线：`─HCU%`（利用率，绿）与 `─VRAM%`（显存占用，品红）画在同一张图；
  两线在同一单元格重合时合并为单个蓝色点，避免出现"双线"
- 每个设备是一个 pod（表头/图例 + 曲线 + 状态行），pod 之间按终端宽度 ~10%
  动态分配间距（`总宽×0.1 ÷ 间隙数`）
- 曲线用盲文点阵渲染成平滑连续线条（非实体块），UTF-8 终端下自动启用
- 每卡实时状态：利用率、显存用量/总量、温度、功耗
- `--once` 文本快照与 `--json` 输出（含 `hy-smi --showpids` 进程信息），便于脚本化

## 环境要求

- 海光 DCU 服务器，驱动为 `hycu` 内核模块（系统含 `/opt/hyhal/bin/hy-smi`）
- Python >= 3.9
- 终端建议 UTF-8（非 UTF-8 自动回退为 `*` 折线）

## 安装与运行

### 方式一：使用 `uv`（推荐，速度极快）

如果你只有 `uv` 没有 `pip`：

```bash
# 1. 免安装一次性运行（uvx，开箱即用）
uvx --from git+https://github.com/mcocdaa/hysmi-top.git hysmi-top

# 运行模拟 Demo 模式（无需 DCU 硬件）：
uvx --from git+https://github.com/mcocdaa/hysmi-top.git hysmi-top --demo

# 2. 全局独立安装为 CLI 命令（推荐日常使用，自动隔离环境）
uv tool install git+https://github.com/mcocdaa/hysmi-top.git

# 安装后即可在任意终端直接执行：
hysmi-top
hysmi-top --demo

# 3. 如果是内网/离线服务器（从 Releases 下载 .whl 文件后）
uv tool install ./hysmi_top-0.2.0-py3-none-any.whl
# 或者通过 uv pip：
uv pip install --system ./hysmi_top-0.2.0-py3-none-any.whl
```

### 方式二：使用 `pip`

```bash
# 在线安装
pip install git+https://github.com/mcocdaa/hysmi-top.git

# 或安装下载的 wheel 包
pip install hysmi_top-0.2.0-py3-none-any.whl

# 运行
hysmi-top
```

### 方式三：克隆源码运行

```bash
git clone https://github.com/mcocdaa/hysmi-top.git
cd hysmi-top

# 使用 uv
uv run hysmi-top
# 或直接用 python
python3 -m hysmi_top
```

## 命令行参数

```bash
# 指定卡、刷新间隔、限制最大图表高度
hysmi-top -d 0-3 -r 500 -c 4

# 模拟演示模式（无需物理硬件，内置多种动态曲线）
hysmi-top --demo

# 文本快照 / JSON 导出（便于脚本采集与 CI）
hysmi-top --once
hysmi-top --once --json
```

键盘：`q` 退出，`+`/`-` 调整刷新速度。

## 可视化曲线检查工具

针对不同终端分辨率与多种曲线形态，提供了无硬件依赖的可视化检查脚本：

```bash
# 检查终端排版效果（支持自定义宽高，如 16 行 90 列）
python3 tests/visual_test.py 16 90

# 查看各种经典曲线独立模式（交叉、动态完全重合、阶梯、紧凑高度对比等）
python3 tests/visual_test.py --patterns
```

## 测试

```bash
python3 -m unittest discover -s tests
```

## 数据来源

- 每卡指标：`/sys/class/drm/cardN/device/` 下的 `gpu_busy_percent`、
  `mem_info_vram_used/total`、`hwmon/*/temp1_input`、`power1_average`、
  `freq1_input`、`freq2_input`（与 hy-smi 同源，直读更快）
- 进程（仅 `--once`/`--json` 快照）：`hy-smi --showpids`