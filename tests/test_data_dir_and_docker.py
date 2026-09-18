"""DATA-10 / ARCH-03 回归测试：数据目录必须可配置，且容器必须挂卷。

原缺陷：
* `docker-compose.yml` 没有任何数据卷，`Dockerfile` 是 `WORKDIR /app` + `COPY . .`。
  数据能留下来纯粹是因为开发时顺手挂了 `./:/app`（那是为了源码热重载）。
  按正常方式跑镜像——不挂源码——所有健康数据都写在容器可写层，**容器重建即
  全量丢失**。DATA-02 里那些 `/opt/project/...` 的绝对路径就是实证。
* 各 store 硬编码 `Path("data")`（相对当前工作目录），容器里既不可配置，
  从非项目根启动还会在别处凭空建一个 data/（ARCH-03）。
* `Dockerfile` 还 `FROM fit_health_agent:dev` —— 以自己的产物为基础镜像，
  全新克隆根本构建不起来。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
# 注释里会引用旧写法用于说明，指令级断言只看非注释行
DOCKERFILE_CODE = "\n".join(
    line for line in DOCKERFILE.splitlines() if not line.strip().startswith("#")
)
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DEV_COMPOSE = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")


def load_settings():
    spec = importlib.util.spec_from_file_location(
        "settings_data10", PACKAGE_DIR / "settings.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


settings = load_settings()


class DataDirResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get(settings.DATA_DIR_ENV)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self.previous is None:
            os.environ.pop(settings.DATA_DIR_ENV, None)
        else:
            os.environ[settings.DATA_DIR_ENV] = self.previous

    def test_default_is_repository_absolute_path(self) -> None:
        os.environ.pop(settings.DATA_DIR_ENV, None)
        expected = Path(settings.__file__).resolve().parents[1] / "data"
        self.assertEqual(settings.data_dir(), expected)
        self.assertTrue(settings.data_dir().is_absolute())

    def test_environment_variable_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            os.environ[settings.DATA_DIR_ENV] = temp
            self.assertEqual(settings.data_dir(), Path(temp))
            self.assertEqual(settings.data_path("health.db"), Path(temp) / "health.db")

    def test_blank_value_falls_back_to_the_default(self) -> None:
        os.environ[settings.DATA_DIR_ENV] = "   "
        self.assertEqual(
            settings.data_dir(), Path(settings.__file__).resolve().parents[1] / "data"
        )

    def test_value_is_read_on_every_call(self) -> None:
        # 不做模块级缓存，测试才能在运行中切目录
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            os.environ[settings.DATA_DIR_ENV] = first
            self.assertEqual(settings.data_dir(), Path(first))
            os.environ[settings.DATA_DIR_ENV] = second
            self.assertEqual(settings.data_dir(), Path(second))


class StoresFollowTheDataDirTest(unittest.TestCase):
    """每个 store 的默认路径都必须落在配置的数据目录下。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous = os.environ.get(settings.DATA_DIR_ENV)
        os.environ[settings.DATA_DIR_ENV] = self.temp.name
        self.addCleanup(self._restore)
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
        self.addCleanup(self._unload)
        self._loaded: list[str] = []

    def _restore(self) -> None:
        if self.previous is None:
            os.environ.pop(settings.DATA_DIR_ENV, None)
        else:
            os.environ[settings.DATA_DIR_ENV] = self.previous

    def _unload(self) -> None:
        for name in self._loaded + ["fithealth_agent"]:
            sys.modules.pop(name, None)

    def load(self, name: str):
        full = f"fithealth_agent.{name}"
        spec = importlib.util.spec_from_file_location(full, PACKAGE_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        self._loaded.append(full)
        spec.loader.exec_module(module)
        return module

    def test_every_store_writes_under_the_configured_directory(self) -> None:
        root = Path(self.temp.name)
        self.load("json_file_lock")
        self.load("settings")
        cases = {
            "daily_records.json": self.load("storage").DailyRecordStore().db_path,
            "user_profile.json": self.load("storage").UserProfileStore().profile_path,
            "info_store.json": self.load("info_store").InfoStore().path,
            "training_plans.json": self.load("plan_store").TrainingPlanStore().path,
            "external_model_settings.json": self.load(
                "external_model_settings"
            ).ExternalModelSettingsStore().path,
        }
        self.load("fit_parser")
        cases["pending_workout.json"] = self.load("workout_store")._PERSIST_PATH
        cases["hr_streams"] = self.load("hr_stream_store").HRStreamStore().stream_dir
        for label, path in cases.items():
            with self.subTest(store=label):
                self.assertEqual(Path(path).parent, root, f"{label} 落在了数据目录之外")

    def test_no_store_hardcodes_the_data_directory_anymore(self) -> None:
        offenders = []
        for path in PACKAGE_DIR.glob("*.py"):
            if path.name == "settings.py":
                continue  # 文档里会提到旧写法
            text = path.read_text(encoding="utf-8")
            code = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("#")
            )
            if 'Path("data")' in code:
                offenders.append(path.name)
        self.assertEqual(offenders, [])


class DockerfileTest(unittest.TestCase):
    def base_image_version(self) -> tuple[int, int]:
        """从 `ARG BASE_IMAGE=python:X.Y-...` 里取出 (major, minor)。"""
        match = re.search(r"ARG BASE_IMAGE=python:(\d+)\.(\d+)", DOCKERFILE_CODE)
        self.assertIsNotNone(
            match, "基础镜像必须是可覆盖的官方 python 镜像（ARG BASE_IMAGE=python:X.Y-...）"
        )
        return int(match.group(1)), int(match.group(2))

    def test_does_not_build_from_its_own_output_image(self) -> None:
        # DATA-10 的根因：镜像以自己的产物为基础镜像，全新克隆构建不起来。
        # 这里只钉"必须是可覆盖的官方 python 镜像"，不钉具体 tag——
        # 换 tag 是运维决策（当前是 python:3.12-bookworm），不该每次都让测试变红。
        self.assertNotIn("FROM fit_health_agent:dev", DOCKERFILE_CODE)
        self.assertIn("FROM ${BASE_IMAGE}", DOCKERFILE_CODE)
        self.base_image_version()

    def test_pins_a_python_version_that_supports_strenum(self) -> None:
        # plan_workflow.py 用了 StrEnum，3.10 上直接 ImportError（ARCH-05）。
        # 断言的是这条真实约束，而不是某个具体版本号。
        self.assertGreaterEqual(
            self.base_image_version(), (3, 11), "基础镜像的 Python 版本低于 3.11，StrEnum 会 ImportError"
        )

    def test_sets_the_data_dir_env(self) -> None:
        self.assertIn("FITHEALTH_DATA_DIR=/app/data", DOCKERFILE_CODE)

    def test_dockerignore_keeps_personal_data_out_of_the_image(self) -> None:
        ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in ignore.splitlines() if not line.strip().startswith("#")
        )
        # memory/ 下这四个目录是 hello-agents 可选子系统的落盘产物，内容是对话
        # 历史与健康细节。agent-trace 阶段 0 之前 devlogs/ 漏在清单外，`COPY . .`
        # 会把它固化进镜像层——与 data/ 完全同一类问题（DATA-10）。
        for pattern in (
            "data/",
            ".env",
            ".git/",
            "memory/traces/",
            "memory/sessions/",
            "memory/todos/",
            "memory/devlogs/",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, code)

    def test_vendor_directory_exists_so_the_copy_never_breaks_the_build(self) -> None:
        # COPY 一个不存在的目录会让构建直接失败
        self.assertTrue((REPO_ROOT / "vendor" / ".gitkeep").exists())
        self.assertIn("COPY vendor/ /tmp/vendor/", DOCKERFILE_CODE)


class ComposeTest(unittest.TestCase):
    def test_data_volume_is_declared(self) -> None:
        """DATA-10 的核心断言。"""
        self.assertIn("./data:/app/data", COMPOSE)

    def test_data_volume_does_not_depend_on_the_source_mount(self) -> None:
        # 默认配置不挂整个项目目录——数据能否留下来不该取决于源码挂没挂
        volumes = COMPOSE.split("volumes:", 1)[1].split("working_dir:", 1)[0]
        code = "\n".join(
            line for line in volumes.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("./:/app", code)

    def test_data_dir_env_matches_the_mount_point(self) -> None:
        # 两处对不上等于白挂
        self.assertIn("FITHEALTH_DATA_DIR: /app/data", COMPOSE)
        self.assertIn("./data:/app/data", COMPOSE)

    def test_dev_override_keeps_the_data_volume(self) -> None:
        self.assertIn("./:/app", DEV_COMPOSE)
        self.assertIn("./data:/app/data", DEV_COMPOSE)

    def test_both_compose_files_parse(self) -> None:
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML 未安装")
        for text in (COMPOSE, DEV_COMPOSE):
            parsed = yaml.safe_load(text)
            self.assertIn("fithealth", parsed["services"])


if __name__ == "__main__":
    unittest.main()
