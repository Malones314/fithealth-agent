"""BUG-09 / BUG-10 / BUG-11 的回归测试。

三条都是"用户看到成功、实际数据没了或改不动"的形态：

* **BUG-09**：`/data/checkins` 只走 `add_record`，同一天点两次保存就并列两条
  `daily_checkin`，而 `PATCH /data/checkins/{record_id}` 前端零调用，是完全不可达
  的死端点——"改一下今天的体重"这件事根本做不到。
* **BUG-10**：清单说"缺 session 会抛 AttributeError 被吞成 500"。**实测这个前提
  是错的**——`parse_fit_file` 取的是 `raw_msgs.get("session", [{}])[0]`，session 恒
  非 None，不会崩。真正的缺陷更糟：路由拿不到 sport → FallbackParser → 没有 lap
  → `_from_session()` 因 `start_time is None` 返回 `[]`，**整场训练被静默丢光**，
  而用户看到的是一句"[FIT 解析完成]"。附带确有其事的一条：`except Exception` 把
  带精确中文原因的 `HealthImportError` 吞成通用 500。
* **BUG-11**：`editorBusy` 置位 2 处、复位只在"保存/合并/切换记录成功之后"，而
  合并按钮的 disabled 把它算了进去 → 改了任一输入框，合并按钮永久变灰且无提示。
  但它当初拦住的是真问题：合并只发 indices 不发 updates，服务端拿旧数据合并、
  前端随后重渲染，未保存的编辑被静默丢弃。所以修法是让合并把草稿一起提交，
  而不是单纯把 editorBusy 从 disabled 里删掉。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from fithealth_agent.runtime import deps

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# 假 fitparse：这些解析器都是纯函数，构造消息流即可测，不需要真实 .fit 夹具
# ══════════════════════════════════════════════════════════════════════════
class _Field:
    def __init__(self, name: str, value: object) -> None:
        self.name, self.value = name, value


class _Message:
    def __init__(self, name: str, data: dict) -> None:
        self.name = name
        self.fields = [_Field(key, value) for key, value in data.items()]


def strength_sets(count: int = 4) -> list[_Message]:
    """count 组真实形状的力量训练 set 消息。"""
    messages, moment = [], BASE
    for _ in range(count):
        messages.append(
            _Message(
                "set",
                {
                    "start_time": moment,
                    "timestamp": moment,
                    "duration": 40.0,
                    "repetitions": 10,
                    "weight": 60.0,
                    "category": [0],  # bench_press
                    "set_type": "active",
                },
            )
        )
        moment = moment.replace(minute=moment.minute + 2)
    return messages


def hr_stream(count: int = 30) -> list[_Message]:
    return [
        _Message("record", {"timestamp": BASE.replace(second=i), "heart_rate": 120 + i % 20})
        for i in range(count)
    ]


def session_message(**overrides: object) -> _Message:
    payload = {
        "sport": "strength_training",
        "start_time": BASE,
        "total_elapsed_time": 600.0,
    }
    payload.update(overrides)
    return _Message("session", payload)


def install_fake_fitparse(messages: list[_Message]) -> None:
    """让 `fitparse.FitFile(...)` 吐出给定的消息流。

    **改的是模块对象的属性，而不是 `sys.modules["fitparse"]`**：`fit_parser` 在
    自己被导入时就执行过 `import fitparse`，手里攥着的是那个模块对象；事后替换
    `sys.modules` 里的条目对它毫无影响，测试会静静地跑在真实解析器上。
    """

    class _FakeFitFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def get_messages(self):
            return iter(messages)

    module = sys.modules.get("fitparse")
    if module is None:
        module = types.ModuleType("fitparse")
        sys.modules["fitparse"] = module
    module.FitFile = _FakeFitFile
    module.StandardUnitsDataProcessor = lambda *a, **k: None


def load_fit_parser(messages: list[_Message]):
    """在假 fitparse 下重新加载一份 fit_parser，避免污染其他用例。"""
    install_fake_fitparse(messages)
    path = REPO_ROOT / "fithealth_agent" / "fit_parser.py"
    spec = importlib.util.spec_from_file_location("fit_parser_bug10", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ══════════════════════════════════════════════════════════════════════════
# BUG-10：解析层
# ══════════════════════════════════════════════════════════════════════════
class SessionlessFitParsingTest(unittest.TestCase):
    """缺 session 消息的 FIT（中断的活动）不得丢数据。"""

    def parse(self, messages: list[_Message]):
        return load_fit_parser(messages).parse_fit_file("activity.fit")

    def test_session_is_never_none_so_the_doc_premise_is_wrong(self) -> None:
        """清单说会抛 AttributeError；`raw_msgs.get("session", [{}])[0]` 决定了不会。"""
        parsed = self.parse(hr_stream() + strength_sets())
        self.assertIsNotNone(parsed.session)
        self.assertEqual(parsed.session.sport_raw, "unknown")

    def test_sets_survive_when_the_session_message_is_missing(self) -> None:
        """核心：4 组真实卧推数据在缺 session 时曾被整段丢光。"""
        parsed = self.parse(hr_stream() + strength_sets(4))
        active = [segment for segment in parsed.segments if not segment.is_rest]
        self.assertEqual(len(active), 4)
        self.assertEqual(active[0].weight_kg, 60.0)
        self.assertEqual(active[0].repetitions, 10)

    def test_missing_session_matches_the_result_with_a_session(self) -> None:
        """同一份数据，有没有 session 消息不该改变解析出的组数。"""
        without = self.parse(hr_stream() + strength_sets(4))
        with_session = self.parse(hr_stream() + strength_sets(4) + [session_message()])
        self.assertEqual(
            [(s.category, s.weight_kg, s.repetitions) for s in without.segments],
            [(s.category, s.weight_kg, s.repetitions) for s in with_session.segments],
        )

    def test_a_bare_record_stream_still_produces_one_segment(self) -> None:
        """既无 session 也无 lap，但 record 流自带时间窗——不该整场丢掉。"""
        parsed = self.parse(hr_stream(30))
        self.assertEqual(len(parsed.segments), 1)
        segment = parsed.segments[0]
        self.assertEqual(segment.lap_trigger, "records")
        self.assertGreater(segment.duration_s, 0)
        self.assertIsNotNone(segment.avg_hr)

    def test_lap_files_are_unaffected(self) -> None:
        messages = hr_stream() + [
            _Message(
                "lap",
                {"start_time": BASE, "total_elapsed_time": 60.0, "timestamp": BASE.replace(minute=1)},
            )
        ]
        parsed = self.parse(messages)
        self.assertEqual(len(parsed.segments), 1)
        self.assertEqual(parsed.segments[0].segment_type, "lap")

    def test_an_empty_file_still_yields_nothing(self) -> None:
        """没数据就是没数据——不能为了"有输出"造出一个空壳分段。"""
        self.assertEqual(self.parse([]).segments, [])

    def test_fallback_prefers_set_messages_over_lap(self) -> None:
        fit = load_fit_parser([])
        raw = {"set": [{"start_time": BASE, "duration": 40.0, "repetitions": 8,
                        "weight": 50.0, "category": [0], "set_type": "active"}]}
        session = fit.SessionSummary(sport="未知运动", sport_raw="unknown")
        segments = fit.FallbackParser().parse(raw, [], session)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].segment_type, "set_active")


# ══════════════════════════════════════════════════════════════════════════
# 端点层：三条都在这里过一遍真实 HTTP
# ══════════════════════════════════════════════════════════════════════════
#: `main` 首次被 import 时会**在模块级**建好各个 store，路径由当时的
#: FITHEALTH_DATA_DIR 定下，此后不再改变。这个文件按字母序排在很前面，于是整套
#: 测试跑下来往往是**它**第一个 import main——如果那次 import 用的是某个用例自己
#: 的临时目录，用例结束删掉目录之后，main 的 store 就永久指向一个不存在的路径，
#: 后面任何一条读档案/记录的测试都会 FileNotFoundError（实测让
#: test_journal_backup_and_restore_safety 里一条无关的中间件测试变红）。
#: 所以专门留一个**进程级**目录给这次 import，由解释器退出时回收。
_IMPORT_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


class EndpointTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_fake_fitparse([])
        cls._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls._directory.name)
        previous = os.environ.get("FITHEALTH_DATA_DIR")
        os.environ["FITHEALTH_DATA_DIR"] = _IMPORT_DIR.name
        try:
            from fastapi.testclient import TestClient

            cls.main = importlib.import_module("main")
        finally:
            if previous is None:
                os.environ.pop("FITHEALTH_DATA_DIR", None)
            else:
                os.environ["FITHEALTH_DATA_DIR"] = previous

        # main 的 store 是模块级单例，谁先 import main 谁定下数据目录；整套测试
        # 跑下来时先 import 的可能是别的模块，它的临时目录还会被自己删掉。
        #
        # **profile_store 必须一起换掉**，哪怕这个文件根本不测档案：本用例结束时
        # 会删掉自己的临时目录，若 main 是在这里首次 import 的，profile_store 就
        # 指着一个已经不存在的路径，之后任何一条读档案的测试都会 FileNotFoundError
        # ——实测就是这样让 test_journal_backup_and_restore_safety 里一条无关的
        # 中间件测试变红的。
        from fithealth_agent import workout_store
        from fithealth_agent.storage import DailyRecordStore, UserProfileStore

        cls._originals = {
            "daily_record_store": deps.daily_record_store,
            "profile_store": deps.profile_store,
        }
        deps.daily_record_store = DailyRecordStore(cls.root / "daily_records.json")
        deps.profile_store = UserProfileStore(cls.root / "user_profile.json")

        # workout_store 把落盘路径存成模块级常量，同样是在首次 import 时定下的。
        # 不改它的话 _persist_meta 会写向一个早已被删掉的临时目录并抛
        # FileNotFoundError。
        cls._workout_store = workout_store
        cls._persist_path = workout_store._PERSIST_PATH
        workout_store._PERSIST_PATH = cls.root / "pending_workout.json"
        cls.client = TestClient(cls.main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        for name, value in cls._originals.items():
            setattr(deps, name, value)
        cls._workout_store._PERSIST_PATH = cls._persist_path
        cls._directory.cleanup()


class DailyCheckinUpsertTest(EndpointTestBase):
    """BUG-09：同一天保存两次是更新，不是再加一条。"""

    def setUp(self) -> None:
        deps.daily_record_store.clear()
        deps.profile_store.reset()

    def checkins(self, day: str = "2026-08-21") -> list[dict]:
        return [
            item
            for item in deps.daily_record_store.list_records()
            if item.get("category") == "daily_checkin" and item.get("date") == day
        ]

    def save(self, **payload: object):
        payload.setdefault("date", "2026-08-21")
        for meal in payload.get("meal_estimates", []) if isinstance(payload.get("meal_estimates"), list) else []:
            if isinstance(meal, dict) and meal.get("source") == "food_photo_estimate" and not meal.get("analysis_token"):
                confidence = str(meal.get("confidence") or "low")
                meal["analysis_token"] = self.main._sign_analysis_confidence(confidence)
        return self.client.post("/data/checkins", json=payload)

    @staticmethod
    def nutrition_group(calories_kcal: int) -> dict:
        protein_g = round(calories_kcal / 50, 1)
        carbs_g = round(calories_kcal / 10, 1)
        fat_g = round(calories_kcal / 100, 1)
        return {
            "source": "food_photo_estimate", "confidence": "high", "user_confirmed": True,
            "items": [{
                "name": "meal", "portion": "1 serving", "calories_kcal": calories_kcal,
                "protein_g": protein_g, "carbs_g": carbs_g, "fat_g": fat_g,
            }],
            "total_kcal": calories_kcal, "protein_g": protein_g,
            "carbs_g": carbs_g, "fat_g": fat_g,
            "range_low_kcal": calories_kcal, "range_high_kcal": calories_kcal,
            "assumptions": [],
        }

    def test_saving_twice_on_one_day_updates_instead_of_appending(self) -> None:
        first = self.save(weight_kg=70.5).json()
        second = self.save(weight_kg=71.0).json()

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["updated"])
        self.assertEqual(first["id"], second["id"], "必须落在同一条记录上")
        self.assertEqual(len(self.checkins()), 1)
        self.assertEqual(self.checkins()[0]["record"]["weight_kg"], 71.0)
        self.assertEqual(self.checkins()[0]["revision"], 2)
        self.assertEqual(deps.profile_store.get_profile()["weekly_weight_kg"], [71.0])

    def test_weights_from_the_same_week_are_synchronized_to_profile(self) -> None:
        self.save(date="2026-08-17", weight_kg=70.5)
        self.save(date="2026-08-19", weight_kg=70.2)
        self.save(date="2026-08-17", weight_kg=70.4)
        self.assertEqual(
            deps.profile_store.get_profile()["weekly_weight_kg"],
            [70.4, 70.2],
        )

    def test_fields_merge_so_a_later_save_does_not_wipe_an_earlier_one(self) -> None:
        """早上记体重、晚上记热量是正常用法，整体替换会把早上那条抹掉。"""
        self.save(weight_kg=70.0)
        self.save(calories_kcal=2200)
        record = self.checkins()[0]["record"]
        self.assertEqual(record["weight_kg"], 70.0)
        self.assertEqual(record["calories_kcal"], 2200)

    def test_other_days_are_untouched(self) -> None:
        self.save(date="2026-08-21", weight_kg=70.0)
        self.save(date="2026-08-22", weight_kg=71.0)
        self.assertEqual(self.checkins("2026-08-21")[0]["record"]["weight_kg"], 70.0)
        self.assertEqual(self.checkins("2026-08-22")[0]["record"]["weight_kg"], 71.0)

    def test_pre_existing_duplicates_are_folded_without_losing_fields(self) -> None:
        """磁盘上已有旧缺陷留下的重复记录，折叠而不是丢弃。"""
        store = deps.daily_record_store
        store.add_record("2026-08-23", "daily_checkin", {"weight_kg": 80.0, "note": "早"})
        store.add_record("2026-08-23", "daily_checkin", {"weight_kg": 82.0})
        store.add_record("2026-08-23", "daily_checkin", {"energy_level": 7})
        self.assertEqual(len(self.checkins("2026-08-23")), 3)

        body = self.save(date="2026-08-23", fatigue_level=3).json()
        self.assertEqual(body["folded_duplicates"], 2)
        self.assertIn("合并", body["notice"])

        self.assertEqual(len(self.checkins("2026-08-23")), 1)
        record = self.checkins("2026-08-23")[0]["record"]
        # 旧→新依次合并：新值覆盖旧值，没被覆盖的字段一个都不能丢
        self.assertEqual(record["weight_kg"], 82.0)
        self.assertEqual(record["note"], "早")
        self.assertEqual(record["energy_level"], 7)
        self.assertEqual(record["fatigue_level"], 3)

    def test_concurrent_saves_on_one_day_still_produce_one_record(self) -> None:
        """查重与写入必须同锁，否则并发请求会同时判定"当日无记录"。"""
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.save(date="2026-08-24", weight_kg=70 + i * 0.1), range(8)))
        self.assertEqual(len(self.checkins("2026-08-24")), 1)

    def test_the_prefill_endpoint_reports_absence_as_data_not_error(self) -> None:
        self.save(weight_kg=70.5)
        found = self.client.get("/data/checkins/2026-08-21")
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["checkin"]["weight_kg"], 70.5)

        # "这天还没打卡"是正常状态，不是错误
        missing = self.client.get("/data/checkins/2026-01-01")
        self.assertEqual(missing.status_code, 200)
        self.assertIsNone(missing.json()["checkin"])

        self.assertEqual(self.client.get("/data/checkins/notadate").status_code, 400)

    def test_the_unreachable_patch_endpoint_is_gone(self) -> None:
        """刻意删除而不是接线：它能把记录改到另一个已有打卡的日期上，正好又造出重复。"""
        response = self.client.patch("/data/checkins/whatever", json={"date": "2026-08-21"})
        self.assertIn(response.status_code, (404, 405))
        # 断言路由**注册**没了，而不是"源码里不许出现这个字符串"——解释为什么
        # 删掉它的注释本身就会提到这个路径。
        self.assertNotIn(
            ("PATCH", "/data/checkins/{record_id}"),
            {
                (method, route.path)
                for route in self.main.app.routes
                for method in getattr(route, "methods", ()) or ()
            },
        )

    def test_validation_still_rejects_bad_payloads_before_writing(self) -> None:
        self.assertEqual(self.save(pain_level=11).status_code, 400)
        self.assertEqual(self.save(date="8/21/2026", weight_kg=70).status_code, 400)
        self.assertEqual(self.checkins(), [])

    def test_health_rows_never_enter_training_endpoints(self) -> None:
        store = deps.daily_record_store
        nutrition = store.add_record(
            "2026-08-23", "nutrition", {"source": "imported", "protein_g": 172}
        )
        workout = store.add_record("2026-08-23", "training", {
            "sport": "strength_training", "segments": [], "session": {},
        })

        overview_ids = {item["id"] for item in self.client.get("/data/overview").json()["records"]}
        training_ids = {item["id"] for item in self.client.get("/data/training-records").json()["items"]}
        self.assertEqual(overview_ids, {workout["id"]})
        self.assertEqual(training_ids, {workout["id"]})
        self.assertEqual(
            self.client.get(f"/data/training-records/{nutrition['id']}").status_code, 404
        )
        self.assertEqual(
            self.client.patch(
                f"/data/training-records/{nutrition['id']}",
                json={"revision": 1, "updates": []},
            ).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/data/records/{nutrition['id']}").status_code, 200)

    def test_nutrition_endpoint_returns_each_meal_as_a_selectable_group(self) -> None:
        self.save(
            date="2026-08-23", calories_kcal=700, protein_g=45, carbs_g=80, fat_g=20,
            meal_estimates=[{
                "source": "food_photo_estimate", "confidence": "medium", "user_confirmed": True,
                "items": [{"name": "rice", "portion": "1 bowl", "calories_kcal": 230,
                           "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5}],
                "total_kcal": 230, "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5,
                "range_low_kcal": 180, "range_high_kcal": 280, "assumptions": [],
            }, {
                "source": "food_photo_estimate", "confidence": "high", "user_confirmed": True,
                "items": [{"name": "chicken", "portion": "200g", "calories_kcal": 330,
                           "protein_g": 40, "carbs_g": 0, "fat_g": 8}],
                "total_kcal": 330, "protein_g": 40, "carbs_g": 0, "fat_g": 8,
                "range_low_kcal": 300, "range_high_kcal": 360, "assumptions": [],
            }],
        )
        response = self.client.get("/data/nutrition-records?day=2026-08-23")
        self.assertEqual(response.status_code, 200)
        groups = response.json()["items"]
        self.assertEqual(len(groups), 2)
        self.assertEqual({item["name"] for item in groups}, {"餐盘照片 1", "餐盘照片 2"})
        self.assertEqual({item["source"] for item in groups}, {"food_photo_estimate"})
        self.assertEqual({item["kind"] for item in groups}, {"meal"})
        self.assertEqual({item["revision"] for item in groups}, {1})

    def test_manual_nutrition_group_is_editable_without_wiping_other_checkin_fields(self) -> None:
        self.save(
            date="2026-08-23", weight_kg=72, calories_kcal=2258,
            protein_g=172, carbs_g=280, fat_g=50, note="原备注",
        )
        group = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"][0]

        response = self.client.patch(
            f"/data/nutrition-records/{group['id']}",
            json={
                "revision": group["revision"], "calories_kcal": 2300,
                "protein_g": 180, "carbs_g": 275, "fat_g": 55, "note": "已校正",
            },
        )

        self.assertEqual(response.status_code, 200)
        saved = self.checkins("2026-08-23")[0]
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["record"]["weight_kg"], 72)
        self.assertEqual(saved["record"]["calories_kcal"], 2300)
        self.assertEqual(saved["record"]["protein_g"], 180)
        self.assertEqual(saved["record"]["note"], "已校正")

    def test_editing_photo_meal_items_recalculates_meal_and_daily_totals(self) -> None:
        self.save(
            date="2026-08-23", calories_kcal=700, protein_g=45, carbs_g=80, fat_g=20,
            meal_estimates=[{
                "source": "food_photo_estimate", "confidence": "medium", "user_confirmed": True,
                "items": [{"name": "rice", "portion": "1 bowl", "calories_kcal": 230,
                           "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5}],
                "total_kcal": 230, "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5,
                "range_low_kcal": 180, "range_high_kcal": 280, "assumptions": [],
            }],
        )
        group = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"][0]
        response = self.client.patch(
            f"/data/nutrition-records/{group['id']}",
            json={"revision": group["revision"], "items": [
                {"name": "rice", "portion": "2 bowls", "calories_kcal": 460,
                 "protein_g": 9.2, "carbs_g": 100.4, "fat_g": 1.0},
                {"name": "egg", "portion": "1", "calories_kcal": 80,
                 "protein_g": 7, "carbs_g": 1, "fat_g": 5},
            ]},
        )

        self.assertEqual(response.status_code, 200)
        updated = response.json()["record"]
        self.assertEqual(updated["total_kcal"], 540)
        self.assertEqual(updated["protein_g"], 16.2)
        record = self.checkins("2026-08-23")[0]["record"]
        self.assertEqual(record["calories_kcal"], 1010)
        self.assertEqual(record["protein_g"], 56.6)
        self.assertEqual(len(record["meal_estimates"][0]["items"]), 2)

    def test_editing_text_nutrition_group_preserves_its_source(self) -> None:
        self.save(
            date="2026-08-23", calories_kcal=189, protein_g=3.2, carbs_g=43.5, fat_g=0.5,
            nutrition_source="agent_tool",
            meal_estimates=[{
                "source": "text_nutrition", "confidence": "high", "user_confirmed": True,
                "items": [{"name": "文字记录", "portion": "未记录", "calories_kcal": 189,
                           "protein_g": 3.2, "carbs_g": 43.5, "fat_g": 0.5}],
                "total_kcal": 189, "protein_g": 3.2, "carbs_g": 43.5, "fat_g": 0.5,
                "range_low_kcal": 189, "range_high_kcal": 189, "assumptions": [],
            }],
        )
        group = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"][0]

        response = self.client.patch(
            f"/data/nutrition-records/{group['id']}",
            json={"revision": group["revision"], "items": [{
                "name": "文字记录", "portion": "未记录", "calories_kcal": 200,
                "protein_g": 4, "carbs_g": 45, "fat_g": 1,
            }]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["record"]["source"], "text_nutrition")
        record = self.checkins("2026-08-23")[0]["record"]
        self.assertEqual(record["meal_estimates"][0]["source"], "text_nutrition")

    def test_deleting_one_nutrition_group_updates_totals_and_keeps_other_fields(self) -> None:
        first = self.nutrition_group(230)
        second = self.nutrition_group(330)
        self.save(
            date="2026-08-23", weight_kg=72, calories_kcal=700,
            protein_g=60, carbs_g=90, fat_g=25, meal_estimates=[first, second],
        )
        groups = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"]
        group = next(item for item in groups if item["total_kcal"] == 230)

        response = self.client.request(
            "DELETE", f"/data/nutrition-records/{group['id']}",
            json={"revision": group["revision"]},
        )

        self.assertEqual(response.status_code, 200)
        record = self.checkins("2026-08-23")[0]["record"]
        self.assertEqual(record["weight_kg"], 72)
        self.assertEqual(record["calories_kcal"], 470)
        self.assertEqual(len(record["meal_estimates"]), 1)
        self.assertEqual(record["meal_estimates"][0]["total_kcal"], 330)

    def test_deleting_final_nutrition_group_does_not_leave_a_zero_manual_group(self) -> None:
        meal = self.nutrition_group(230)
        self.save(
            date="2026-08-23", weight_kg=72, calories_kcal=230,
            protein_g=meal["protein_g"], carbs_g=meal["carbs_g"], fat_g=meal["fat_g"],
            meal_estimates=[meal],
        )
        group = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"][0]

        response = self.client.request(
            "DELETE", f"/data/nutrition-records/{group['id']}",
            json={"revision": group["revision"]},
        )

        self.assertEqual(response.status_code, 200)
        record = self.checkins("2026-08-23")[0]["record"]
        self.assertEqual(record["weight_kg"], 72)
        self.assertNotIn("meal_estimates", record)
        self.assertNotIn("calories_kcal", record)
        self.assertEqual(
            self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"],
            [],
        )

    def test_deleting_legacy_manual_nutrition_group_preserves_the_daily_checkin(self) -> None:
        self.save(
            date="2026-08-23", weight_kg=72, calories_kcal=2000,
            protein_g=150, carbs_g=220, fat_g=60,
        )
        group = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"][0]
        self.assertEqual(group["kind"], "manual")

        response = self.client.request(
            "DELETE", f"/data/nutrition-records/{group['id']}",
            json={"revision": group["revision"]},
        )

        self.assertEqual(response.status_code, 200)
        record = self.checkins("2026-08-23")[0]["record"]
        self.assertEqual(record, {"weight_kg": 72.0})
        self.assertEqual(
            self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"],
            [],
        )

    def test_nutrition_group_save_rejects_stale_revision(self) -> None:
        self.save(date="2026-08-23", calories_kcal=2000)
        group = self.client.get("/data/nutrition-records?day=2026-08-23").json()["items"][0]
        url = f"/data/nutrition-records/{group['id']}"
        self.assertEqual(self.client.patch(url, json={"revision": 1, "calories_kcal": 2100}).status_code, 200)
        stale = self.client.patch(url, json={"revision": 1, "calories_kcal": 2200})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "STALE_RECORD_REVISION")

    def test_nutrition_endpoint_rejects_invalid_dates(self) -> None:
        self.assertEqual(self.client.get("/data/nutrition-records?day=not-a-date").status_code, 400)


class UploadFitErrorReportingTest(EndpointTestBase):
    """BUG-10：端点层的报错与"空结果"必须说实话。"""

    FIT_BYTES = b"\x0e\x10FIT" + b"x" * 200

    def upload(self, name: str = "activity.fit"):
        return self.client.post(
            "/upload_fit", files={"file": (name, self.FIT_BYTES, "application/octet-stream")}
        )

    def as_activity(self):
        return mock.patch.object(
            deps, "inspect_fit_source", return_value={"kind": "activity", "warnings": []}
        )

    def test_a_file_with_no_segments_is_not_reported_as_success(self) -> None:
        """否则"整场训练被丢光"与"一切正常"在用户眼里长得一模一样。"""
        install_fake_fitparse([])
        with self.as_activity():
            response = self.upload("empty.fit")
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "empty")
        self.assertIsNone(body["workout"])
        self.assertIn("没有解析出任何训练分段", body["message"])
        self.assertIn("请保留这个 FIT 文件", body["message"])
        # 空壳不能留在待确认区，否则用户会对着它点"确认并保存"
        self.assertIsNone(self.main.workout_store.get_current())

    def test_health_import_error_keeps_its_reason_and_status(self) -> None:
        """它带着精确的中文原因和自己的 status_code，吞掉等于让用户去查一个好文件。"""
        error = self.main.HealthImportError(
            "该 FIT 是设备监测文件，请从 Garmin Connect 导出全天健康数据 ZIP", status_code=400
        )
        with mock.patch.object(deps, "inspect_fit_source", return_value={"kind": "monitoring"}), \
             mock.patch.object(deps.health_import_service, "import_file", side_effect=error):
            response = self.upload("monitor.fit")

        self.assertEqual(response.status_code, 400)
        self.assertIn("设备监测文件", response.json()["message"])

    def test_an_unexpected_error_no_longer_blames_the_users_file(self) -> None:
        with self.as_activity(), \
             mock.patch.object(deps, "parse_fit_file", side_effect=RuntimeError("解析器内部错误")):
            response = self.upload()

        self.assertEqual(response.status_code, 500)
        message = response.json()["message"]
        self.assertNotIn("请确认文件完整", message)
        self.assertIn("解析器", message)

    def test_a_normal_strength_file_still_parses(self) -> None:
        install_fake_fitparse(hr_stream() + strength_sets(4) + [session_message()])
        with self.as_activity():
            response = self.upload("bench.fit")
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["workout"]["sets"]), 4)


class MergeCarriesEditorDraftTest(EndpointTestBase):
    """BUG-11：合并必须带上编辑区草稿，否则未保存的修改被静默丢弃。"""

    def setUp(self) -> None:
        module = load_fit_parser(hr_stream() + strength_sets(4) + [session_message()])
        self.main.workout_store.set_current(module.parse_fit_file("bench.fit"))

    def tearDown(self) -> None:
        self.main.workout_store.clear_current()

    def segments(self) -> list[tuple]:
        return [
            (s.index, s.category, s.weight_kg, s.repetitions)
            for s in self.main.workout_store.get_current().segments
        ]

    def merge(self, indices: list[int], **extra: object):
        return self.client.post(
            "/workout_state/update", json={"action": "merge_sets", "indices": indices, **extra}
        )

    def full_draft(self, **first: object) -> list[dict]:
        draft = [
            {"index": index, "category": "卧推", "weight_kg": 60, "repetitions": 10}
            for index in (1, 2, 3, 4)
        ]
        draft[0].update(first)
        return draft

    def test_unsaved_edits_are_applied_before_merging(self) -> None:
        draft = self.full_draft()
        draft[0].update({"category": "上斜卧推", "weight_kg": 80, "repetitions": 12})
        draft[1].update({"category": "上斜卧推", "weight_kg": 80, "repetitions": 12})

        body = self.merge([1, 2], updates=draft, note="今天状态不错").json()

        # 修复前：category="卧推" / weight=60 / reps=20，用的是服务端那份旧数据
        self.assertEqual(body["category"], "上斜卧推")
        self.assertEqual(body["weight_kg"], 80)
        self.assertEqual(body["repetitions"], 24)
        self.assertEqual(self.main.workout_store.get_current().note, "今天状态不错")

    def test_merging_without_a_draft_keeps_the_old_behaviour(self) -> None:
        """Agent 路径与没刷新的老页面不会带 updates。"""
        body = self.merge([1, 2]).json()
        self.assertEqual(body["category"], "卧推")
        self.assertEqual(body["repetitions"], 20)

    def test_an_invalid_draft_aborts_the_whole_merge(self) -> None:
        before = self.segments()
        draft = self.full_draft(category="")
        response = self.merge([1, 2], updates=draft)

        self.assertEqual(response.status_code, 400)
        self.assertIn("动作名称", response.json()["error"])
        # 不能留下"编辑应用了一半、合并没做"的状态
        self.assertEqual(self.segments(), before)

    def test_an_over_long_note_aborts_before_merging(self) -> None:
        before = self.segments()
        response = self.merge([1, 2], note="长" * 501)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.segments(), before)


# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    unittest.main()
