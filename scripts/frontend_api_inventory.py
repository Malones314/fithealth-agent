"""Render the FastAPI route decorators as a stable frontend contract index."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    REPO_ROOT / "main.py",
    REPO_ROOT / "fithealth_agent" / "runtime" / "frontend.py",
    *sorted((REPO_ROOT / "fithealth_agent" / "routes").glob("*.py")),
)
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    handler: str
    source: str
    line: int


def inventory() -> list[Route]:
    routes: list[Route] = []
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.lower()
                if method not in HTTP_METHODS or not decorator.args:
                    continue
                route_path = decorator.args[0]
                if not isinstance(route_path, ast.Constant) or not isinstance(route_path.value, str):
                    continue
                routes.append(
                    Route(
                        method=method.upper(),
                        path=route_path.value,
                        handler=node.name,
                        source=str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                        line=node.lineno,
                    )
                )
    return sorted(routes, key=lambda item: (item.path, item.method))


def main() -> int:
    print("| Method | Path | Handler | Source |")
    print("| --- | --- | --- | --- |")
    for route in inventory():
        print(
            f"| `{route.method}` | `{route.path}` | `{route.handler}` | "
            f"`{route.source}:{route.line}` |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
