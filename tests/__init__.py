"""Process-wide data isolation for pytest and unittest package discovery."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="fithealth-tests-20260825-"))
REPO_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
if TEST_DATA_DIR.resolve() == REPO_DATA_DIR.resolve():
    raise RuntimeError("test data directory must not be the repository data directory")
os.environ["FITHEALTH_DATA_DIR"] = str(TEST_DATA_DIR)

# agent-trace 阶段 1：显式关闭 trace，并把目录也钉进临时数据目录。
#
# `off` 本来就是默认值，写出来是为了防**开发机环境变量**：谁的 shell 里开着
# FITHEALTH_TRACE，整套测试就会一边跑一边往盘上写健康数据，而且断言全绿。
# 目录一起钉住是第二道防线——万一将来有用例故意打开 trace，产物也只会落在这个
# 临时目录里，不会污染仓库（阶段 0 的 TRACE-12 就是这类问题）。
os.environ["FITHEALTH_TRACE"] = "off"
os.environ["FITHEALTH_TRACE_DIR"] = str(TEST_DATA_DIR / "traces")

atexit.register(shutil.rmtree, TEST_DATA_DIR, True)
