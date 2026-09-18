"""BUG-08 回归测试：一次上传多个 ZIP 时，第 2 个之后的活动不能被静默丢弃。

原缺陷：`upload_health` 里的判断是 `len(activities) == 1 and workout is None`，
其中 `activities` 是**单个 ZIP 内**的列表、`workout` 是整个循环共用的单变量。
于是一次上传两个各含一个活动的 ZIP 时，第 1 个被载入编辑区，第 2 个只把名字
塞进 `activity_files` 就没了；前端又在 `data.workout` 非空时直接走第一分支，
ZIP#2 的训练既不进选择器也没有任何提示——用户以为两个都导入了。

修法：全局收集所有活动，只有**恰好一个**时才自动载入，其余一律交给前端选择器；
响应里新增 `activities: [{zip, name}]`，让选择器知道每个活动来自哪个 ZIP
（`/upload_health/activity` 需要重新上传对应的那个 ZIP 才能取出 FIT）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import load_symbol, module_source


REPO_ROOT = Path(__file__).resolve().parents[1]


# 抽出来单独执行，避免 import main 触发整条 LLM 依赖链（ARCH-02）。
select_autoload_activity = load_symbol("select_autoload_activity")


class AutoloadSelectionTest(unittest.TestCase):
    def test_single_activity_is_autoloaded(self) -> None:
        activities = [("a.zip", "1_ACTIVITY.fit", b"x")]
        self.assertEqual(select_autoload_activity(activities), activities[0])

    def test_two_activities_in_one_zip_are_left_to_the_picker(self) -> None:
        activities = [
            ("a.zip", "1_ACTIVITY.fit", b"x"),
            ("a.zip", "2_ACTIVITY.fit", b"y"),
        ]
        self.assertIsNone(select_autoload_activity(activities))

    def test_one_activity_in_each_of_two_zips_is_the_reported_bug(self) -> None:
        # 原实现在这里会载入 ZIP#1 的活动，ZIP#2 的被静默丢弃
        activities = [
            ("a.zip", "1_ACTIVITY.fit", b"x"),
            ("b.zip", "2_ACTIVITY.fit", b"y"),
        ]
        self.assertIsNone(select_autoload_activity(activities))

    def test_no_activity_means_nothing_to_load(self) -> None:
        self.assertIsNone(select_autoload_activity([]))


class BackendWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        upload_source = module_source(consumer_home("upload_health"))
        self.body = upload_source.split("async def upload_health(", 1)[1].split(
            "\n@app.", 1
        )[0]
        # 注释里会引用旧写法用于说明，断言只看代码
        self.code = "\n".join(
            line for line in self.body.splitlines() if not line.strip().startswith("#")
        )

    def test_activities_are_collected_before_deciding(self) -> None:
        # 关键：收集与"决定载入哪个"分成两步，后者在循环之外
        collect_at = self.code.index("pending_activities.append(")
        decide_at = self.code.index("select_autoload_activity(pending_activities)")
        self.assertLess(collect_at, decide_at)

    def test_the_old_per_zip_condition_is_gone(self) -> None:
        self.assertNotIn("len(activities) == 1 and workout is None", self.code)

    def test_response_exposes_the_source_zip_of_each_activity(self) -> None:
        self.assertIn('"zip": filename, "name": activity_name', self.code)
        self.assertIn('"activities": activities_index', self.code)
        # 扁平名字列表保留，避免旧前端拿不到任何东西
        self.assertIn('"activity_files": activity_files', self.code)

    def test_a_failed_activity_parse_does_not_fail_the_whole_import(self) -> None:
        # 健康数据本身已经导入成功了，不该因为一个活动 FIT 坏掉而整体报错
        tail = self.code.split("select_autoload_activity(pending_activities)", 1)[1]
        self.assertIn("except Exception", tail)
        self.assertIn("活动 FIT 解析失败，其余健康数据已导入", tail)


if __name__ == "__main__":
    unittest.main()
