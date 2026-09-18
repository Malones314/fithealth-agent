from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_INIT = REPO_ROOT / "fithealth_agent" / "__init__.py"


def requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.add(re.split(r"[<>=!~;\s]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return names


class RuntimeEnvironmentTest(unittest.TestCase):
    def test_project_declares_supported_python_range(self) -> None:
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["requires-python"], ">=3.11,<3.13")
        self.assertEqual(project["project"]["dynamic"], ["dependencies"])
        self.assertEqual(
            project["tool"]["setuptools"]["dynamic"]["dependencies"]["file"],
            ["requirements.txt"],
        )
        self.assertEqual((REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip(), "3.12")

    def test_runtime_and_development_dependencies_are_explicit(self) -> None:
        runtime = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        development = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        self.assertIn("starlette>=0.46.0,<2.0.0", runtime)
        self.assertIn('tzdata>=2024.1; platform_system == "Windows"', runtime)
        self.assertIn("python-multipart>=0.0.18", runtime)
        self.assertIn("-r requirements.txt", development)
        self.assertIn("pytest>=7.4,<9.0", development)
        self.assertIn("pip-tools>=7.5,<8.0", development)

    def test_lockfile_pins_every_direct_dependency(self) -> None:
        direct = requirement_names(REPO_ROOT / "requirements.txt")
        lock_lines = (REPO_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        package_line = re.compile(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^=\s;\\]+)(?:\s*;[^\\]+)?\s*\\?$"
        )
        packages = [
            (index, match.group(1).lower().replace("_", "-"))
            for index, raw in enumerate(lock_lines)
            if (match := package_line.fullmatch(raw.strip()))
        ]
        locked = {name for _, name in packages}
        self.assertTrue(direct <= locked, "requirements.lock 缺少直接依赖")
        self.assertTrue(packages, "requirements.lock 没有锁定任何依赖")

        for position, (start, name) in enumerate(packages):
            end = packages[position + 1][0] if position + 1 < len(packages) else len(lock_lines)
            block = "\n".join(lock_lines[start:end])
            self.assertRegex(block, r"--hash=sha256:[0-9a-f]{64}", f"{name} 缺少 SHA256")

    def test_package_reports_an_actionable_error_on_python_310(self) -> None:
        spec = importlib.util.spec_from_file_location("runtime_guard_probe", PACKAGE_INIT)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "version_info", (3, 10, 14)):
            with self.assertRaisesRegex(RuntimeError, "requires Python 3.11 or newer"):
                spec.loader.exec_module(module)

    def test_container_and_docs_use_the_declared_default(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("ARG BASE_IMAGE=python:3.12-bookworm", dockerfile)
        self.assertIn("BASE_IMAGE: python:3.12-bookworm", compose)
        self.assertIn("COPY requirements.lock ./", dockerfile)
        self.assertIn("-r requirements.lock", dockerfile)
        self.assertIn("Python 3.11 和 3.12", readme)
        self.assertIn("requirements.lock", readme)


if __name__ == "__main__":
    unittest.main()
