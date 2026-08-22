# hysmi-top

终端 GPU 监控工具，专为**海光 Hygon DCU（hy-smi）**设计：每张卡一张图，
多条曲线（HCU 利用率、VRAM 占用）以**滚动线条**叠加显示，风格类似 nvtop。

零第三方依赖（仅 Python 标准库：`curses`），读取内核 sysfs 数据，无需
root、不轮询起子进程（进程列表除外）。

## 功能

- 多卡网格布局，每卡一个图表块，**自适应窗口尺寸**：窗口矮时自动压缩曲线高度
  （Y 方向），仍放不下则增加每行列数（X 方向），resize 后自动重排
- 叠加滚动曲线：`─HCU%`（利用率，绿）与 `─VRAM%`（显存占用，品红）画在同一张图
- 曲线用盲文点阵渲染成平滑连续线条（非实体块），UTF-8 终端下自动启用
- 每卡实时状态：利用率、显存用量/总量、温度、功耗
- 进程列表（解析 `hy-smi --showpids`，进程名取自 /proc）
- `--once` 文本快照与 `--json` 输出，便于脚本化

## 环境要求

- 海光 DCU 服务器，驱动为 `hycu` 内核模块（系统含 `/opt/hyhal/bin/hy-smi`）
- Python >= 3.9
- 终端建议 UTF-8（非 UTF-8 自动回退为 `*` 折线）

## 使用

```bash
# 直接运行（TUI）
python3 -m hysmi_top

# 指定卡、刷新间隔、图表高度
python3 -m hysmi_top -d 0-3 -r 500 -c 4

# 文本快照 / JSON
python3 -m hysmi_top --once
python3 -m hysmi_top --once --json
```

键盘：`q` 退出，`+`/`-` 调整刷新速度，`p` 开关进程列表。

## 安装（可选）

```bash
pip install .
hysmi-top
```

## 测试

```bash
python3 -m unittest discover -s tests
```

## 数据来源

- 每卡指标：`/sys/class/drm/cardN/device/` 下的 `gpu_busy_percent`、
  `mem_info_vram_used/total`、`hwmon/*/temp1_input`、`power1_average`、
  `freq1_input`、`freq2_input`（与 hy-smi 同源，直读更快）
- 进程：`hy-smi --showpids`