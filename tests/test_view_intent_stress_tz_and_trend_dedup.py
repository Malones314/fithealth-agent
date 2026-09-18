"""BUG-14 / BUG-15 / BUG-16 的回归测试。

三条的共同点是"数字或回答看起来正常，其实是错的或根本没给"：

* **BUG-14**：`view_*` 意图命中即 `return` 固定话术，同一条消息里的真实问题被整个
  丢弃（实测 Agent 完全未被调用）。修法不是"一律继续走 Agent"——短路正是 BUG-02
  建立的离线本地读路径，那样改会让关闭外部模型时「查看训练记录」变成 503。
* **BUG-15**：**清单的前提经实测不成立，而它给出的修法会亲手造出这条 bug。**
  抽三份真实 wellness ZIP 逐分钟核对：stress 的 naive 时间戳当 UTC 解释后与同一
  文件里心率的 `timestamp`(utc=True) 完全重合（偏差 0.0h）；按清单的
  `local_ts.replace(tzinfo=BEIJING)` 则整体差 8.0h。所以现有时区解释是对的，只是
  **碰巧对的**——本文件把这个契约钉死，防止后人"修"成 8 小时偏移。
* **BUG-16**：日汇总与 `query_heart_rate_window` 都按 `timestamp_utc` 去重，而
  `get_metric_trend(period="day")` 直扫原始表。实测真实库重复率 7.5%/7.3%，
  2026-08-15 整日 samples 恰好是日汇总的 2 倍，68 组里 11 组口径不一致。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4
from fithealth_agent.runtime import deps
from fithealth_agent.workflows import chat_workflow

from tests.module_map import consumer_home
from tests.source_tools import module_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"

#: 专供"首次 import main"使用的进程级目录。用例自己的临时目录会在 tearDown 时删掉，
#: 而 main 在模块级就把 store 绑定死了——用后者会让**别的**测试文件读到不存在的路径。
_IMPORT_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


# ══════════════════════════════════════════════════════════════════════════
# BUG-14
# ══════════════════════════════════════════════════════════════════════════
class NavigationOnlyDetectionTest(unittest.TestCase):
    """纯导航 vs 复合请求的判定。"""

    @classmethod
    def setUpClass(cls) -> None:
        previous = os.environ.get("FITHEALTH_DATA_DIR")
        os.environ["FITHEALTH_DATA_DIR"] = _IMPORT_DIR.name
        try:
            cls.main = importlib.import_module("main")
        finally:
            if previous is None:
                os.environ.pop("FITHEALTH_DATA_DIR", None)
            else:
                os.environ["FITHEALTH_DATA_DIR"] = previous

    def test_pure_navigation_is_recognized(self) -> None:
        for text in (
            "查看训练记录",
            "查询 2026-08-18 的训练记录",
            "我的训练记录",
            "查看个人信息",
            "个人档案",
            "帮我打开训练记录",
            "看看我的档案",
            "回顾一下运动数据",
            "查看训练记录吗？",
            "麻烦帮我查一下今天的训练记录",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.main.navigation_only_message(text))

    def test_an_extra_request_in_the_same_message_is_recognized(self) -> None:
        for text in (
            "帮我看看我的训练记录，上次卧推推了多少公斤？",
            "我的可用器械有哪些，用这些器械帮我安排今天的胸推",
            "查看训练记录，顺便告诉我这周练得够不够",
            "看看个人信息，我该怎么调整热量摄入",
            "查看训练记录并帮我制定明天的腿部计划",
        ):
            with self.subTest(text=text):
                self.assertFalse(self.main.navigation_only_message(text))

    def test_misjudging_leans_toward_answering(self) -> None:
        """把纯导航误判成"有额外诉求"只多花一次模型往返；反过来才是这条 bug。

        所以遇到没见过的说法时，判定结果应当是"有额外诉求"（False）。
        """
        self.assertFalse(self.main.navigation_only_message("瞅一眼训练存档"))


class ViewIntentDoesNotSwallowQuestionsTest(unittest.TestCase):
    """走真实 /chat：复合请求必须既给面板又回答问题。"""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from fithealth_agent.chat_intent_router import ChatIntent
        from fithealth_agent.storage import DailyRecordStore, UserProfileStore

        cls.ChatIntent = ChatIntent
        cls._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls._directory.name)
        previous = os.environ.get("FITHEALTH_DATA_DIR")
        os.environ["FITHEALTH_DATA_DIR"] = _IMPORT_DIR.name
        try:
            cls.main = importlib.import_module("main")
        finally:
            if previous is None:
                os.environ.pop("FITHEALTH_DATA_DIR", None)
            else:
                os.environ["FITHEALTH_DATA_DIR"] = previous

        cls._originals = {
            "daily_record_store": deps.daily_record_store,
            "profile_store": deps.profile_store,
        }
        deps.daily_record_store = DailyRecordStore(cls.root / "daily_records.json")
        deps.profile_store = UserProfileStore(cls.root / "user_profile.json")
        deps.profile_store.update_profile({
            "height_cm": 175, "birth_date": "1995-01-01", "sex": "male",
            "goal": "增肌", "equipment": ["哑铃", "杠铃"], "weekly_weight_kg": [70.0],
        })
        cls.client = TestClient(cls.main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        for name, value in cls._originals.items():
            setattr(deps, name, value)
        cls._directory.cleanup()

    def chat(self, message: str, intent, answer: str = "上次卧推是 60 kg × 10 次。"):
        """发一条消息，返回 (响应体, 模型收到的 prompt 列表)。"""
        prompts: list[str] = []

        def fake_agent(*args, **kwargs):
            class _Agent:
                def run(self, prompt):
                    prompts.append(prompt)
                    return answer

            return _Agent()

        with mock.patch.object(deps, "route_chat_intent", return_value=intent), \
             mock.patch.object(deps, "create_fithealth_agent", side_effect=fake_agent):
            response = self.client.post(
                "/chat", json={"message": message, "history": [], "source": "chat"}
            )
        return response, prompts

    def test_training_records_view_plus_question_reaches_the_agent(self) -> None:
        message = "帮我看看我的训练记录，上次卧推推了多少公斤？"
        response, prompts = self.chat(message, self.ChatIntent(view_training_records=True))
        body = response.json()

        self.assertEqual(len(prompts), 1, "修复前 Agent 完全没有被调用")
        self.assertIn("卧推推了多少公斤", prompts[0], "用户的原话必须送到模型")
        self.assertIn("60 kg", body["reply"], "模型的回答必须出现在回复里")
        # 面板照旧要给——用户确实说了"看看训练记录"
        self.assertEqual(body["artifact"]["type"], "training_records")

    def test_training_view_soreness_and_advice_survive_in_one_response(self) -> None:
        from fithealth_agent.muscle_recovery import MuscleRecoverySnapshot
        from fithealth_agent.soreness_store import SorenessStore

        message = "我昨天的训练计划是什么？另外我今天大腿非常酸，该怎么办？"
        store = SorenessStore(self.root / "compound-soreness.json")
        prompts: list[str] = []

        class _Agent:
            def run(self, prompt):
                prompts.append(prompt)
                return "今天先降低腿部负荷，避免继续做高强度腿部训练。"

        with (
            mock.patch.object(deps, "soreness_store", store),
            mock.patch.object(deps, "classify_user_health_statement", return_value=True),
            mock.patch.object(
                chat_workflow,
                "_current_recovery_snapshot",
                return_value=MuscleRecoverySnapshot(),
            ),
            mock.patch.object(
                deps,
                "route_chat_intent",
                return_value=self.ChatIntent(view_training_records=True),
            ),
            mock.patch.object(deps, "create_fithealth_agent", return_value=_Agent()),
        ):
            response = self.client.post(
                "/chat", json={"message": message, "history": [], "source": "chat"}
            )

        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(body["artifact"]["type"], "training_records")
        self.assertTrue(body["soreness_saved"])
        self.assertEqual(body["soreness_reports"][0]["region"], "腿部")
        self.assertEqual(body["soreness_reports"][0]["level"], "sore")
        self.assertIn("72 小时", body["reply"])
        self.assertIn("降低腿部负荷", body["reply"])

    def test_profile_view_plus_request_keeps_the_local_profile_text(self) -> None:
        message = "我的可用器械有哪些，用这些器械帮我安排今天的胸推"
        response, prompts = self.chat(
            message, self.ChatIntent(view_profile=True), answer="建议做卧推与飞鸟。"
        )
        body = response.json()

        self.assertEqual(len(prompts), 1)
        # 档案视图没有 artifact，"看到的东西"就是正文，所以它作为前缀保留
        self.assertTrue(body["reply"].startswith("## 个人档案"))
        self.assertIn("建议做卧推与飞鸟", body["reply"])

    def test_pure_navigation_still_short_circuits_without_a_model_call(self) -> None:
        """这条路径是 BUG-02 的离线本地读，不能因为修 BUG-14 而丢掉。"""
        for message, intent in (
            ("查看训练记录", self.ChatIntent(view_training_records=True)),
            ("查看个人信息", self.ChatIntent(view_profile=True)),
        ):
            with self.subTest(message=message):
                response, prompts = self.chat(message, intent)
                self.assertEqual(prompts, [])
                self.assertIn(
                    response.json()["source"], {"local_training_records", "local_profile"}
                )

    def test_offline_compound_request_says_so_instead_of_dropping_it(self) -> None:
        deps.external_model_settings_store.set_external_models_enabled(False)
        self.addCleanup(
            deps.external_model_settings_store.set_external_models_enabled, True
        )
        response, prompts = self.chat(
            "帮我看看我的训练记录，上次卧推推了多少公斤？",
            self.ChatIntent(view_training_records=True),
        )
        body = response.json()

        self.assertEqual(prompts, [])
        self.assertEqual(response.status_code, 200, "离线也不该变成 503")
        self.assertEqual(body["artifact"]["type"], "training_records")
        self.assertIn("外部模型当前已关闭", body["reply"])

    def test_offline_pure_navigation_has_no_extra_note(self) -> None:
        deps.external_model_settings_store.set_external_models_enabled(False)
        self.addCleanup(
            deps.external_model_settings_store.set_external_models_enabled, True
        )
        response, _ = self.chat("查看训练记录", self.ChatIntent(view_training_records=True))
        self.assertNotIn("外部模型当前已关闭", response.json()["reply"])

    def test_a_plan_save_card_outranks_the_pending_view_panel(self) -> None:
        """前端一次只渲染一个 artifact，需要用户操作的那张卡优先。"""
        plan = (
            "## 训练计划\n训练科目：胸部训练\n\n"
            "| 动作 | 组数 | 次数 | 重量 |\n|---|---|---|---|\n"
            "| 卧推 | 4 | 10 | 60kg |\n| 上斜哑铃卧推 | 3 | 12 | 20kg |\n"
            "| 双杠臂屈伸 | 3 | 12 | 自重 |\n| 器械夹胸 | 3 | 15 | 30kg |\n"
            "\n总组数：13 组\n"
        )
        response, _ = self.chat(
            "我的可用器械有哪些，用这些器械帮我安排今天的胸推",
            self.ChatIntent(
                view_profile=True,
                create_training_plan=True,
                training_plan_subject="胸部训练",
                training_plan_title="胸部训练",
            ),
            answer=plan,
        )
        body = response.json()
        self.assertEqual(body["artifact"]["type"], "training_plan")
        self.assertIn("## 个人档案", body["reply"])

    def test_recovery_message_gets_plan_artifact_when_router_misses_intent(self) -> None:
        plan = (
            "## 今日恢复训练计划\n训练科目：综合训练\n\n"
            "热身：跳绳 5 分钟，肩胛俯卧撑 2 组。\n"
            "哑铃卧推：3 组 × 10 次；俯卧撑：3 组 × 12 次；"
            "哑铃划船：3 组 × 10 次；深蹲：3 组 × 12 次。\n"
            "拉伸：胸部、背部和髋部各 30 秒。\n\n"
            + "动作要点：使用轻重量，保留余力，组间休息 90 秒。\n" * 16
        )
        message = "上周我感冒了，没有进行训练，我现在已经恢复了，为我设计我今天的训练计划"
        with mock.patch.object(
            deps, "validate_plan_goal_alignment", return_value={"passed": True, "stage": "test"}
        ), mock.patch.object(chat_workflow, "_immediate_memory_candidate", return_value=None):
            response, _ = self.chat(message, self.ChatIntent(), answer=plan)

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["artifact"]["type"], "training_plan")
        self.assertTrue(body["artifact"]["draft_id"])
        self.assertEqual(body["workflow_state"], "awaiting_save")


# ══════════════════════════════════════════════════════════════════════════
# BUG-15
# ══════════════════════════════════════════════════════════════════════════
def load_importer():
    """单独加载一份 health_importer，避免污染其他用例的 fitfile 打桩。"""
    package = sys.modules.get("fithealth_agent")
    if package is None or not hasattr(package, "__path__"):
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
    for name, filename in (
        ("fithealth_agent.health_store", "health_store.py"),
        ("fithealth_agent.health_importer", "health_importer.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / filename)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules["fithealth_agent.health_importer"]


class _Message:
    def __init__(self, name: str, fields: dict) -> None:
        self.type = types.SimpleNamespace(name=name)
        self.fields = fields


class StressTimestampSemanticsTest(unittest.TestCase):
    """把"naive 即 UTC"的契约钉死——清单给的修法会让它偏 8 小时。"""

    def setUp(self) -> None:
        self.importer = load_importer()
        self.addCleanup(
            lambda: [
                sys.modules.pop(name, None)
                for name in (
                    "fithealth_agent.health_importer",
                    "fithealth_agent.health_store",
                )
            ]
        )

    def parse(self, messages: list[_Message], kind: str = "monitoring_b") -> dict:
        fake = types.SimpleNamespace(
            type=types.SimpleNamespace(name=kind), serial_number=1, messages=messages
        )
        with mock.patch.object(self.importer.fitfile.file, "File", lambda path: fake):
            return self.importer._parse_fit_source("sample_WELLNESS.fit", b"x" * 32)

    def stress_samples(self, parsed: dict) -> list[dict]:
        return [s for s in parsed["metric_samples"] if s["metric"] == "stress"]

    def test_a_naive_stress_timestamp_is_read_as_utc(self) -> None:
        """04:00 naive → 12:00 北京。若当成本地墙钟会得到前一天 20:00。"""
        parsed = self.parse([
            _Message("stress_level", {
                "local_timestamp": datetime(2026, 8, 13, 4, 0), "stress_level": 42.0
            })
        ])
        sample = self.stress_samples(parsed)[0]
        self.assertEqual(sample["timestamp_utc"], "2026-08-13T04:00:00+00:00")
        self.assertEqual(sample["timestamp_local"], "2026-08-13T12:00:00+08:00")
        self.assertEqual(sample["local_date"], "2026-08-13")
        # 清单建议的写法会给出这个——明确断言我们**不是**这样
        self.assertNotEqual(sample["timestamp_local"], "2026-08-12T20:00:00+08:00")

    def test_stress_and_heart_rate_from_one_file_stay_aligned(self) -> None:
        """真实数据里两者逐分钟重合；同一时刻必须落进同一个本地时间。

        这是"naive 即 UTC"唯一的判据：心率走的是 `timestamp`(utc=True)，语义确定。
        """
        moment_naive = datetime(2026, 8, 13, 4, 0)
        moment_aware = moment_naive.replace(tzinfo=timezone.utc)
        parsed = self.parse([
            _Message("stress_level", {"local_timestamp": moment_naive, "stress_level": 42.0}),
            _Message("monitoring", {"timestamp": moment_aware, "heart_rate": 70}),
        ])
        self.assertEqual(
            self.stress_samples(parsed)[0]["timestamp_local"],
            parsed["heart_rates"][0]["timestamp_local"],
        )

    def test_an_aware_timestamp_is_honoured_if_upstream_ever_supplies_one(self) -> None:
        parsed = self.parse([
            _Message("stress_level", {
                "timestamp": datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc),
                "stress_level": 42.0,
            })
        ])
        self.assertEqual(
            self.stress_samples(parsed)[0]["timestamp_local"], "2026-08-13T12:00:00+08:00"
        )

    def test_the_spec_field_name_is_tried_first(self) -> None:
        """fitfile 自己认为 `local_timestamp` 这个名字是错的；改名不该丢数据。"""
        parsed = self.parse([
            _Message("stress_level", {
                "stress_level_time": datetime(2026, 8, 13, 4, 0), "stress_level": 42.0
            })
        ])
        self.assertEqual(len(self.stress_samples(parsed)), 1)

    def test_an_unknown_time_field_warns_instead_of_silently_dropping(self) -> None:
        """原实现直接跳过：1440 条/天的压力样本一条不剩，且不留痕迹。"""
        parsed = self.parse([
            _Message("stress_level", {
                "some_future_name": datetime(2026, 8, 13, 4, 0), "stress_level": 42.0
            })
        ])
        self.assertEqual(self.stress_samples(parsed), [])
        self.assertTrue(any("压力读数" in text for text in parsed["warnings"]))

    def test_other_metrics_keep_using_the_shared_timestamp(self) -> None:
        moment = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
        parsed = self.parse([
            _Message("respiration", {"timestamp": moment, "respiration_rate": 16.0}),
            _Message("pulse_ox", {"timestamp": moment, "pulse_ox": 96.0}),
            _Message("hrv_value", {"timestamp": moment, "hrv_value": 6400}),
        ])
        by_metric = {s["metric"]: s for s in parsed["metric_samples"]}
        self.assertEqual(set(by_metric), {"respiration", "spo2", "hrv"})
        for sample in by_metric.values():
            self.assertEqual(sample["timestamp_local"], "2026-08-13T12:00:00+08:00")

    def test_a_stress_message_does_not_disturb_its_neighbours(self) -> None:
        """原实现在 stress 分支里就地覆盖 `timestamp`——后面的分支读的是同一个变量。"""
        moment = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
        parsed = self.parse([
            _Message("stress_level", {
                "local_timestamp": datetime(2026, 8, 13, 6, 0), "stress_level": 42.0
            }),
            _Message("hrv_status_summary", {
                "timestamp": moment, "weekly_average": 6400, "status": 4, "reading_count": 3
            }),
        ])
        self.assertEqual(len(parsed["hrv_statuses"]), 1)
        self.assertEqual(
            parsed["hrv_statuses"][0]["timestamp_local"], "2026-08-13T12:00:00+08:00"
        )


# ══════════════════════════════════════════════════════════════════════════
# BUG-16
# ══════════════════════════════════════════════════════════════════════════
class TrendDeduplicationTest(unittest.TestCase):
    """按天趋势必须与日汇总同口径。"""

    def setUp(self) -> None:
        package = sys.modules.get("fithealth_agent")
        if package is None or not hasattr(package, "__path__"):
            package = types.ModuleType("fithealth_agent")
            package.__path__ = [str(PACKAGE_DIR)]
            sys.modules["fithealth_agent"] = package
        spec = importlib.util.spec_from_file_location(
            "fithealth_agent.health_store", PACKAGE_DIR / "health_store.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["fithealth_agent.health_store"] = module
        spec.loader.exec_module(module)
        self.store_module = module
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        self.addCleanup(sys.modules.pop, "fithealth_agent.health_store", None)
        root = Path(self._directory.name)
        self.store = module.HealthStore(root / "health.db", root / "raw")

    DAY = "2026-08-15"

    def sample(self, minute: int, *, bpm: int | None = None, value: float | None = None,
               metric: str = "stress") -> dict:
        moment = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
        base = {
            "timestamp_utc": moment.isoformat(),
            "timestamp_local": moment.astimezone(self.store_module.BEIJING).isoformat(),
            "local_date": self.DAY,
        }
        if bpm is not None:
            return {**base, "bpm": bpm}
        return {**base, "metric": metric, "value": value}

    def save(self, heart_rates: list[dict], metric_samples: list[dict], tag: str) -> None:
        self.store.save_import({
            "id": str(uuid4()),
            "sha256": (tag * 64)[:64],
            "filename": f"{tag}-{self.DAY}.zip",
            "kind": "wellness_zip",
            "status": "imported",
            "date_hint": self.DAY,
            "warnings": [],
            "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sleep": None,
            "sources": [{
                "id": str(uuid4()),
                "filename": f"{tag}_WELLNESS.fit",
                "kind": "wellness",
                "sha256": (tag * 64)[:64],
                "earliest_utc": None,
                "latest_utc": None,
                "device_serial": "device",
                "warnings": [],
                "record_count": len(heart_rates) + len(metric_samples),
                "message_counts": {},
                "data_types": ["heart_rate", "stress"],
                "heart_rates": heart_rates,
                "metric_samples": metric_samples,
                "activity_observations": [],
                "hrv_statuses": [],
                "sleep_stages": [],
            }],
        })

    def trend_samples(self, metric: str) -> int:
        trend = self.store.get_metric_trend(metric, "day", self.DAY)
        return sum(int(item["samples"]) for item in trend["items"])

    def summary(self, column: str):
        with self.store._connection() as connection:
            row = connection.execute(
                f"SELECT {column} AS v FROM daily_health_summary WHERE date = ?", (self.DAY,)
            ).fetchone()
        return row["v"] if row else None

    def test_an_overlapping_reimport_does_not_double_the_sample_count(self) -> None:
        """两份 ZIP 覆盖同一时段：整包 sha256 不同，导入侧去重拦不住。"""
        heart_rates = [self.sample(index, bpm=70 + index) for index in range(30)]
        metrics = [self.sample(index, value=40 + index) for index in range(30)]
        self.save(heart_rates, metrics, "a")
        self.save(heart_rates, metrics, "b")

        with self.store._connection() as connection:
            raw = connection.execute(
                "SELECT COUNT(*) AS n FROM heart_rate_samples WHERE local_date = ?", (self.DAY,)
            ).fetchone()["n"]
        self.assertEqual(raw, 60, "原始表确实存了两份（delete_import 需要逐行归属）")

        self.assertEqual(self.trend_samples("heart_rate"), 30)
        self.assertEqual(self.trend_samples("stress"), 30)

    def test_trend_and_daily_summary_report_the_same_sample_count(self) -> None:
        heart_rates = [self.sample(index, bpm=70 + index) for index in range(30)]
        metrics = [self.sample(index, value=40 + index) for index in range(30)]
        self.save(heart_rates, metrics, "a")
        self.save(heart_rates[:10], metrics[:10], "b")  # 只重叠一部分

        for metric, column in (("heart_rate", "heart_rate_samples"), ("stress", "stress_samples")):
            with self.subTest(metric=metric):
                self.assertEqual(self.trend_samples(metric), self.summary(column))

    def test_a_partially_duplicated_hour_no_longer_skews_its_average(self) -> None:
        """整小时被完整复制时均值不变；**只重复一部分**才会把那几分钟的权重翻倍。"""
        heart_rates = [self.sample(index, bpm=60) for index in range(10)]
        heart_rates += [self.sample(10 + index, bpm=180) for index in range(10)]
        self.save(heart_rates, [], "a")
        self.save(heart_rates[:10], [], "b")  # 只把 60 bpm 那 10 分钟再来一遍

        trend = self.store.get_metric_trend("heart_rate", "day", self.DAY)
        values = {item["label"]: item["value"] for item in trend["items"]}
        # 不去重会得到 (60*20 + 180*10) / 30 = 100.0
        self.assertEqual(values["09:00"], 120.0)
        self.assertEqual(self.summary("heart_rate_avg"), 120.0)

    def test_week_and_month_periods_are_unchanged(self) -> None:
        heart_rates = [self.sample(index, bpm=70 + index) for index in range(30)]
        self.save(heart_rates, [], "a")
        self.save(heart_rates, [], "b")
        for period in ("week", "month"):
            with self.subTest(period=period):
                trend = self.store.get_metric_trend("heart_rate", period, self.DAY)
                today = [item for item in trend["items"] if item["label"] == self.DAY]
                self.assertEqual(len(today), 1)
                self.assertEqual(today[0]["samples"], self.summary("heart_rate_samples"))

    def test_deleting_one_of_two_overlapping_imports_keeps_the_other(self) -> None:
        """这正是刻意不在写入侧加 UNIQUE 约束的原因。"""
        heart_rates = [self.sample(index, bpm=70) for index in range(10)]
        self.save(heart_rates, [], "a")
        self.save(heart_rates, [], "b")
        first = self.store.list_imports()[0]["id"]
        self.assertTrue(self.store.delete_import(first))
        self.assertEqual(self.trend_samples("heart_rate"), 10)


# ══════════════════════════════════════════════════════════════════════════
class SourceInvariantTest(unittest.TestCase):
    """把三条修法的形状钉在源码上。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.main_tree = module_tree(consumer_home("chat"))
        cls.importer_source = (PACKAGE_DIR / "health_importer.py").read_text(encoding="utf-8")
        cls.store_source = (PACKAGE_DIR / "health_store.py").read_text(encoding="utf-8")

    def _function(self, tree: ast.AST, name: str):
        return next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        )

    def _body_source(self, tree: ast.AST, name: str) -> str:
        """函数体源码，**去掉 docstring**——注释里会照抄缺陷代码，会误伤断言。"""
        node = self._function(tree, name)
        statements = node.body[1:] if ast.get_docstring(node) is not None else node.body
        return "\n".join(ast.unparse(statement) for statement in statements)

    # ---- BUG-14 ----
    def test_chat_has_no_direct_json_response_outside_its_builder(self) -> None:
        """新增返回分支必须经过请求级构造器，不能再手抄响应字段。"""
        chat = self._function(self.main_tree, "chat")
        direct_calls: list[int] = []

        class DirectResponseVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                if node.name == "_chat_response":
                    return
                self.generic_visit(node)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "JSONResponse":
                    direct_calls.append(node.lineno)
                self.generic_visit(node)

        for statement in chat.body:
            DirectResponseVisitor().visit(statement)
        self.assertEqual(direct_calls, [])

    # ---- BUG-15 ----
    def test_the_stress_time_field_order_puts_the_spec_name_first(self) -> None:
        tree = ast.parse(self.importer_source)
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_STRESS_TIME_FIELDS" for t in node.targets)
        )
        names = [element.value for element in assignment.value.elts]
        self.assertEqual(names[0], "stress_level_time")
        self.assertIn("local_timestamp", names)
        self.assertLess(names.index("timestamp"), names.index("local_timestamp"))

    def test_the_stress_branch_no_longer_reassigns_timestamp(self) -> None:
        function = self._function(ast.parse(self.importer_source), "_parse_fit_source")
        assignments = [
            ast.unparse(node) for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "timestamp" for t in node.targets)
        ]
        self.assertEqual(assignments, ["timestamp = values.get('timestamp')"])

    def test_beijing_is_never_attached_to_a_naive_fit_timestamp(self) -> None:
        """清单建议的写法会把全部压力数据提前 8 小时。

        断言在 **AST 上**而不是整份源码文本上：解释"本地墙钟字段才需要这么写"的
        注释本身就会提到 `replace(tzinfo=BEIJING)`。
        """
        tree = ast.parse(self.importer_source)
        offenders = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and any(
                keyword.arg == "tzinfo"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "BEIJING"
                for keyword in node.keywords
            )
        ]
        self.assertEqual(offenders, [])

    def test_the_naive_means_utc_contract_is_written_down(self) -> None:
        """原先这个假设完全没有记录，于是"碰巧对"看着像"随便写的"。"""
        docstring = ast.get_docstring(self._function(ast.parse(self.importer_source), "_utc_datetime"))
        self.assertIsNotNone(docstring)
        self.assertIn("date_time", docstring)
        self.assertIn("local_date_time", docstring)

    # ---- BUG-16 ----
    def test_every_raw_sample_reader_shares_one_dedup_definition(self) -> None:
        tree = ast.parse(self.store_source)
        for name in ("_rebuild_daily_summary", "get_metric_trend"):
            with self.subTest(function=name):
                body = self._body_source(tree, name)
                self.assertIn("DEDUPED_", body)
                # 直扫原始表就是这条 bug 的本体
                self.assertNotIn("FROM heart_rate_samples", body)
                self.assertNotIn("FROM health_metric_samples", body)

    def test_the_dedup_sql_groups_by_timestamp_utc(self) -> None:
        module_globals: dict[str, object] = {}
        for name in ("DEDUPED_HEART_RATE_SQL", "DEDUPED_METRIC_SQL"):
            match = re.search(rf'{name} = """(.*?)"""', self.store_source, re.S)
            self.assertIsNotNone(match, name)
            module_globals[name] = match.group(1)
        self.assertIn("GROUP BY timestamp_utc", str(module_globals["DEDUPED_HEART_RATE_SQL"]))
        self.assertIn("GROUP BY metric, timestamp_utc", str(module_globals["DEDUPED_METRIC_SQL"]))
        for sql in module_globals.values():
            # 分桶要用确定的那一个本地时间，且与日汇总的 coverage_start 一致
            self.assertIn("MIN(timestamp_local)", str(sql))

    def test_sample_uniqueness_stays_scoped_to_one_source_file(self) -> None:
        """两次导入必须各存一行。

        表上本来就有 `UNIQUE(source_file_id, timestamp_utc, ...)`——它挡的是**同一个**
        FIT 文件里的重复，挡不住跨导入重叠（source_file_id 不同），这正是 BUG-16 的
        由来。但也不能把它收紧成不含 source_file_id 的全局唯一：那样两次导入会共享
        同一行，`delete_import` 删掉任一个都会连带抹掉另一个的数据。
        """
        for table in ("heart_rate_samples", "health_metric_samples"):
            with self.subTest(table=table):
                match = re.search(
                    rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\s*\)", self.store_source, re.S
                )
                self.assertIsNotNone(match, table)
                unique = re.search(r"UNIQUE\s*\(([^)]*)\)", match.group(1))
                self.assertIsNotNone(unique, f"{table} 应保留同文件内的唯一约束")
                self.assertIn("source_file_id", unique.group(1))


if __name__ == "__main__":
    unittest.main()
