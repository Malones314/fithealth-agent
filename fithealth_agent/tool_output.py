"""tool_output.py — ReAct 工具输出溢出目录（agent-trace 阶段 0 补丁）。

## 这个目录里是什么

`hello_agents` 的 `ObservationTruncator` 在工具输出超过上限（本项目锁在 2000 行 /
50 KiB）时，会把**未截断的原文**整份写成 `tool_<时间戳>_<工具名>.json`，只把预览
交给模型。本项目的 `query_daily_records` / `query_health_range` / `query_sleep`
返回的正是训练与健康记录，所以这个目录会周期性地积累健康隐私。

## 原实现的两个问题

1. **路径按 CWD 解析**。框架默认 `tool_output_dir="tool-output"` 是相对路径，
   而 `ObservationTruncator.__init__` 无条件 `os.makedirs` 它。后果与 DATA-10 /
   ARCH-03 完全同型：从别的目录启动就在别处凭空建一份；容器只挂 `data/`，写在
   可写层里，**容器重建即全量丢失**；而仓库根的 `tool-output/` 同时还是人工用的
   临时目录（里面有开发脚本和日志），两种用途混在一起。
2. **不受 `/data/reset` 管辖**。"删除全部数据"清空十项 store 之后，这里的健康
   原文原样保留。

所以路径改为锚在 `settings.data_dir()` 下，并给 `/data/reset` 加一步清理。

## 为什么不进备份

这里的内容是**已经存在于各 store 里的数据的一份副本**（工具就是从 store 读出来
的），不是唯一副本。所以它不进 `LocalBackupService`，删除前也不需要恢复点——
与 `memory/traces/` 的定位一致：诊断产物，不是用户数据。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .settings import data_dir


logger = logging.getLogger(__name__)

#: 数据目录下的子目录名。
DIR_NAME = "tool-output"

#: 框架落盘的文件名前缀（`ObservationTruncator._save_full_output`）。清理只动这类
#: 文件，不递归、不删目录本身——万一有人把别的东西放进来，不该被顺手抹掉。
FILE_PREFIX = "tool_"


def tool_output_dir() -> Path:
    """返回工具输出溢出目录。

    每次调用都重新解析，不做模块级缓存——`settings.data_dir()` 的契约就是每次读
    环境变量（见 `settings.py` 的"约定"一节）。冻在 import 时会让测试切换
    `FITHEALTH_DATA_DIR` 失效，也会让"数据落在哪"取决于 import 顺序。
    """
    return data_dir() / DIR_NAME


def clear_tool_output() -> int:
    """删除溢出文件，返回删除条数。

    逐个文件独立处理：`/data/reset` 的每一步都要能报出"删了多少"，而单个文件被
    占用（Windows 上很常见）不该让整步失败。
    """
    target = tool_output_dir()
    if not target.is_dir():
        return 0
    removed = 0
    for item in sorted(target.glob(f"{FILE_PREFIX}*.json")):
        if not item.is_file():
            continue
        try:
            item.unlink()
        except OSError as exc:
            logger.warning("工具输出溢出文件删除失败：%s（%s）", item.name, exc)
        else:
            removed += 1
    return removed
