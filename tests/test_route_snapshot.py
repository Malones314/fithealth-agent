"""HTTP 契约快照比对（main.py 拆分：阶段 0 建立，阶段 1–7 每次跑都要绿）。

阶段 6 要把 ~2070 行 CRUD 端点按资源拆成 7 个 `routes/*.py`，用 `APIRouter` 重新
注册。这一步最容易出的两类问题都是静默的：

1. **漏搬**：按资源表搬迁时漏掉不在表里的独立端点，前端某个按钮 404，测试全绿。
2. **改序**：`include_router` 顺序变了，`/workout_state/quarantined/{name}/preview`
   之类的路径被别的模式先匹配掉。

所以这里按**注册顺序**逐条比对路径、方法、name、response class、状态码和
operation id，以及两份维护期路径白名单（那两份改一个字符就会让维护期死等）。

要**故意**改契约时（本次拆分不允许）先跑::

    python -m tests.route_snapshot --write
"""

from __future__ import annotations

import unittest

from tests.route_snapshot import (
    CONTRACT_FIELDS,
    build_snapshot_from_main,
    load_snapshot,
)


class RouteContractSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.current = build_snapshot_from_main()
        cls.expected = load_snapshot()

    def _by_key(self, snapshot: dict) -> dict[str, dict]:
        keyed: dict[str, dict] = {}
        for index, route in enumerate(snapshot["routes"]):
            methods = ",".join(route["methods"]) or "-"
            keyed[f"{methods} {route['path']}"] = {**route, "_order": index}
        return keyed

    def test_no_route_was_lost_or_added(self) -> None:
        current = self._by_key(self.current)
        expected = self._by_key(self.expected)
        missing = sorted(set(expected) - set(current))
        added = sorted(set(current) - set(expected))
        self.assertEqual(missing, [], "这些路由不见了（搬迁时漏了）：\n" + "\n".join(missing))
        self.assertEqual(added, [], "这些路由是新增的（本次拆分不应新增）：\n" + "\n".join(added))

    def test_every_route_keeps_its_contract(self) -> None:
        current = self._by_key(self.current)
        expected = self._by_key(self.expected)
        for key in sorted(set(expected) & set(current)):
            for field in CONTRACT_FIELDS:
                with self.subTest(route=key, field=field):
                    self.assertEqual(current[key][field], expected[key][field])

    def test_registration_order_is_unchanged(self) -> None:
        """顺序影响路径匹配，`include_router` 的先后必须与拆分前一致。"""
        self.assertEqual(
            [f"{','.join(r['methods'])} {r['path']}" for r in self.current["routes"]],
            [f"{','.join(r['methods'])} {r['path']}" for r in self.expected["routes"]],
        )

    def test_openapi_operations_are_unchanged(self) -> None:
        self.assertEqual(
            self.current["openapi_operations"], self.expected["openapi_operations"]
        )

    def test_maintenance_path_strings_are_byte_identical(self) -> None:
        self.assertEqual(
            self.current["maintenance_allowed_paths"],
            self.expected["maintenance_allowed_paths"],
        )
        self.assertEqual(
            self.current["maintenance_allowed_prefixes"],
            self.expected["maintenance_allowed_prefixes"],
        )
        self.assertEqual(
            self.current["maintenance_untracked_paths"],
            self.expected["maintenance_untracked_paths"],
        )


if __name__ == "__main__":
    unittest.main()
