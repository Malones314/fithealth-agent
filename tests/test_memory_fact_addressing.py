"""BUG-12 回归测试：记忆事实必须按稳定 id 寻址，不能靠列表下标。

原缺陷：接口收 `fact_index`。而每次 `/chat` 都会 `cleanup_expired` **物理删除**
过期事实，`reject_fact` 也会 pop 让后面的事实整体前移。于是一个没刷新的页面
点"确认/拒绝/编辑"，会作用到**另一条事实**上——比"旧记忆覆盖新记忆"更危险，
因为用户以为自己确认的是屏幕上看到的那条。

修法是给每条事实一个**内容派生**的稳定 id（不是随机 uuid，那样存量数据在落盘
之前每次读取都会拿到不同的 id）。本文件钉住四组不变量：
1. id 稳定：跨读取、跨无关写入、跨他条事实被删都不变；
2. 位移场景下按 id 操作命中的仍是原来那条；
3. 下标寻址作为兼容路径仍可用，但会命中位移后的那条（记录现状，说明为什么要 id）；
4. 编辑改变 id（值变了就是另一条事实），且不允许撞车。
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import module_source


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_store_module():
    path = REPO_ROOT / "fithealth_agent" / "info_store.py"
    spec = importlib.util.spec_from_file_location("info_store_bug12", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load info_store")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FactIdAddressingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_store_module()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = self.module.InfoStore(Path(self.temp_dir.name) / "info_store.json")
        self.expires = datetime.now(timezone.utc) + timedelta(days=1)

    def _entry_with_three_facts(self) -> dict:
        return self.store.add_entry(
            "三条待确认事实",
            {},
            self.expires,
            memory_type="constraint",
            importance=5,
            facts=[
                {"namespace": "health", "key": "injury_or_constraint", "value": "腰部不适"},
                {"namespace": "training", "key": "avoid_exercise", "value": "深蹲"},
                {"namespace": "training", "key": "prefer_exercise", "value": "硬拉"},
            ],
        )

    def _facts(self, entry_id: str) -> list[dict]:
        entry = next(item for item in self.store.get_all() if item["id"] == entry_id)
        return entry["facts"]

    # ------------------------------------------------------------------
    # 不变量 1：id 稳定
    # ------------------------------------------------------------------

    def test_every_fact_gets_an_id(self) -> None:
        entry = self._entry_with_three_facts()
        ids = [fact["fact_id"] for fact in entry["facts"]]
        self.assertEqual(len(ids), 3)
        self.assertTrue(all(isinstance(value, str) and value for value in ids))
        self.assertEqual(len(set(ids)), 3, "同一条目内 id 必须唯一")

    def test_id_is_stable_across_reads_without_any_write(self) -> None:
        # 用随机 uuid 就做不到这一点：存量事实在落盘之前每次读取都会换 id
        entry = self._entry_with_three_facts()
        first = [fact["fact_id"] for fact in self._facts(entry["id"])]
        second = [fact["fact_id"] for fact in self._facts(entry["id"])]
        self.assertEqual(first, second)

    def test_id_survives_deleting_an_earlier_fact(self) -> None:
        entry = self._entry_with_three_facts()
        before = {fact["value"]: fact["fact_id"] for fact in entry["facts"]}
        self.store.reject_fact(entry["id"], fact_id=before["腰部不适"])
        after = {fact["value"]: fact["fact_id"] for fact in self._facts(entry["id"])}
        self.assertEqual(after["深蹲"], before["深蹲"])
        self.assertEqual(after["硬拉"], before["硬拉"])

    def test_id_survives_cleanup_expired(self) -> None:
        entry = self._entry_with_three_facts()
        before = {fact["value"]: fact["fact_id"] for fact in entry["facts"]}
        self.store.cleanup_expired()
        after = {fact["value"]: fact["fact_id"] for fact in self._facts(entry["id"])}
        self.assertEqual(after, before)

    # ------------------------------------------------------------------
    # 不变量 2：位移之后按 id 仍然命中原来那条
    # ------------------------------------------------------------------

    def test_confirming_by_id_after_a_shift_hits_the_intended_fact(self) -> None:
        entry = self._entry_with_three_facts()
        target_id = next(f["fact_id"] for f in entry["facts"] if f["value"] == "硬拉")
        # 页面渲染时"硬拉"在下标 2；此后第一条被拒绝，它前移到下标 1
        self.store.reject_fact(entry["id"], fact_id=entry["facts"][0]["fact_id"])
        result = self.store.set_fact_confirmation(entry["id"], fact_id=target_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["fact"]["value"], "硬拉")
        self.assertTrue(result["fact"]["user_confirmed"])
        # 而且没有误伤旁边那条
        others = {f["value"]: f["user_confirmed"] for f in self._facts(entry["id"])}
        self.assertEqual(others, {"腰部不适": False, "深蹲": False, "硬拉": True})

    def test_stale_index_would_have_hit_the_wrong_fact(self) -> None:
        """软删除墓碑也保持旧下标稳定；新页面仍应优先使用 fact_id。"""
        entry = self._entry_with_three_facts()
        self.store.reject_fact(entry["id"], fact_id=entry["facts"][0]["fact_id"])
        # 墓碑保留原位置，因此旧页面的下标不会因拒绝操作而漂移。
        self.assertIsNotNone(self.store.set_fact_confirmation(entry["id"], 2))
        shifted = self.store.set_fact_confirmation(entry["id"], 1)
        self.assertEqual(shifted["fact"]["value"], "深蹲")

    def test_rejecting_by_id_removes_the_intended_fact(self) -> None:
        entry = self._entry_with_three_facts()
        target_id = next(f["fact_id"] for f in entry["facts"] if f["value"] == "深蹲")
        result = self.store.reject_fact(entry["id"], fact_id=target_id)
        self.assertEqual(result["fact"]["value"], "深蹲")
        facts = self._facts(entry["id"])
        self.assertEqual([f["value"] for f in facts], ["腰部不适", "深蹲", "硬拉"])
        rejected = next(f for f in facts if f["value"] == "深蹲")
        self.assertEqual(rejected["status"], "rejected")

    def test_unknown_id_is_not_silently_applied_to_something_else(self) -> None:
        entry = self._entry_with_three_facts()
        for method in (
            lambda: self.store.set_fact_confirmation(entry["id"], fact_id="deadbeefdeadbeef"),
            lambda: self.store.reject_fact(entry["id"], fact_id="deadbeefdeadbeef"),
            lambda: self.store.edit_fact(entry["id"], value="别的", fact_id="deadbeefdeadbeef"),
        ):
            with self.subTest(method=method):
                self.assertIsNone(method())
        # 原样未动
        self.assertEqual(len(self._facts(entry["id"])), 3)

    # ------------------------------------------------------------------
    # 不变量 3 & 4：编辑语义
    # ------------------------------------------------------------------

    def test_editing_by_id_changes_the_id_because_the_value_changed(self) -> None:
        entry = self._entry_with_three_facts()
        old_id = next(f["fact_id"] for f in entry["facts"] if f["value"] == "深蹲")
        result = self.store.edit_fact(entry["id"], value="杠铃深蹲", fact_id=old_id)
        self.assertEqual(result["fact"]["value"], "杠铃深蹲")
        self.assertNotEqual(result["fact"]["fact_id"], old_id)
        # 编辑后必须重新确认
        self.assertFalse(result["fact"]["user_confirmed"])
        # 位置不变，只是内容与 id 换了
        self.assertEqual(
            [f["value"] for f in self._facts(entry["id"])], ["腰部不适", "杠铃深蹲", "硬拉"]
        )

    def test_editing_into_a_sibling_value_is_refused(self) -> None:
        # 否则两条事实会共用一个 id，之后再也无法分别寻址
        entry = self.store.add_entry(
            "两条同类事实", {}, self.expires,
            facts=[
                {"namespace": "training", "key": "avoid_exercise", "value": "深蹲"},
                {"namespace": "training", "key": "avoid_exercise", "value": "硬拉"},
            ],
        )
        target_id = entry["facts"][1]["fact_id"]
        with self.assertRaises(ValueError):
            self.store.edit_fact(entry["id"], value="深蹲", fact_id=target_id)
        self.assertEqual(
            [f["value"] for f in self._facts(entry["id"])], ["深蹲", "硬拉"]
        )

    def test_id_depends_only_on_namespace_key_value(self) -> None:
        same = self.module.fact_identity_id("training", "avoid_exercise", "深蹲")
        self.assertEqual(same, self.module.fact_identity_id("training", "avoid_exercise", "深蹲"))
        self.assertNotEqual(same, self.module.fact_identity_id("training", "prefer_exercise", "深蹲"))
        self.assertNotEqual(same, self.module.fact_identity_id("training", "avoid_exercise", "硬拉"))

    def test_response_carries_both_id_and_index(self) -> None:
        # 前端拿 id 继续操作；index 保留只为过渡期排查
        entry = self._entry_with_three_facts()
        target_id = entry["facts"][1]["fact_id"]
        result = self.store.set_fact_confirmation(entry["id"], fact_id=target_id)
        self.assertEqual(result["fact_id"], target_id)
        self.assertEqual(result["fact_index"], 1)


class WiringTest(unittest.TestCase):
    """源码断言：接口与前端必须真的走 id，否则修了也白修。"""

    def setUp(self) -> None:
        self.main_source = module_source(consumer_home("memory_fact_routes"))

    def test_routes_accept_a_fact_reference_not_a_bare_index(self) -> None:
        self.assertIn("/data/memories/{entry_id}/facts/{fact_ref}/confirm", self.main_source)
        self.assertIn("/data/memories/{entry_id}/facts/{fact_ref}/reject", self.main_source)
        self.assertIn('@router.patch("/data/memories/{entry_id}/facts/{fact_ref}")', self.main_source)
        self.assertNotIn("facts/{fact_index}", self.main_source)

    def test_routes_pass_fact_id_through(self) -> None:
        self.assertIn("fact_id=locator[\"fact_id\"]", self.main_source)

if __name__ == "__main__":
    unittest.main()
