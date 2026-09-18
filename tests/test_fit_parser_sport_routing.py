"""DATA-04 回归测试：sport=training 无 set 消息时不得整段丢弃 lap 与心率。

原缺陷链：
1. `_pick_parser` 只看 `sport`，把 `sport=training` 一律交给
   `StrengthTrainingParser`。但 Garmin 的 `sport=training(10)` 是**大类**
   ——HIIT、瑜伽、普拉提、有氧训练全都用它，只靠 `sub_sport` 区分。
2. `StrengthTrainingParser.parse()` 只读 `raw_messages["set"]`，**无任何
   fallback**，于是这些活动的 lap 与心率被整段丢弃，用户只看到
   "未找到任何训练组数据"，而原始 FIT 通常已不在磁盘上，数据拿不回来。
3. 附带：`IntervalSportParser.can_handle()` 是死代码，`_pick_parser` 从不
   调用它，而是在函数末尾自己又写了一遍整数判断。

`fit_parser.py` 此前实质零覆盖（ARCH-06），而这些都是纯函数——直接构造
`raw_messages` dict 就能测，不需要真实 .fit 夹具。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import module_source


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_fit_parser():
    path = REPO_ROOT / "fithealth_agent" / "fit_parser.py"
    spec = importlib.util.spec_from_file_location("fit_parser_data04", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load fit_parser")
    module = importlib.util.module_from_spec(spec)
    # dataclass 的注解解析要靠 sys.modules 找回定义模块，先注册再执行
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ParserRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fit = load_fit_parser()

    def name_of(self, sport, sub=None) -> str:
        return type(self.fit._pick_parser(sport, sub)).__name__

    def test_broad_training_sport_routes_by_sub_sport(self) -> None:
        cases = {
            "cardio_training": "LapBasedParser",
            "yoga": "LapBasedParser",
            "pilates": "LapBasedParser",
            "breathing": "LapBasedParser",
            "flexibility_training": "LapBasedParser",
            "hiit": "IntervalSportParser",
            "interval_training": "IntervalSportParser",
            "circuit_training": "IntervalSportParser",
            "strength_training": "StrengthTrainingParser",
        }
        for sub, expected in cases.items():
            with self.subTest(sub_sport=sub):
                self.assertEqual(self.name_of("training", sub), expected)

    def test_training_without_sub_sport_still_parses_sets(self) -> None:
        # 老文件没有 sub_sport，无从判断，保持原有行为（按 set 解析）
        for sub in (None, "", "none", "unknown"):
            with self.subTest(sub_sport=sub):
                self.assertEqual(self.name_of("training", sub), "StrengthTrainingParser")

    def test_explicit_sport_is_not_overridden_by_sub_sport(self) -> None:
        # sport 已经写明具体值时，sub_sport 不该把它推翻
        self.assertEqual(self.name_of("strength_training", "cardio_training"), "StrengthTrainingParser")
        self.assertEqual(self.name_of("cycling", "hiit"), "LapBasedParser")
        self.assertEqual(self.name_of("running", "treadmill"), "LapBasedParser")

    def test_known_lap_sports_route_as_before(self) -> None:
        for sport in ("cycling", "running", "swimming", "walking", "hiking", "fitness_equipment"):
            with self.subTest(sport=sport):
                self.assertEqual(self.name_of(sport), "LapBasedParser")

    def test_integer_sport_routes_to_interval_parser(self) -> None:
        # Garmin 自定义运动：这条以前靠 _pick_parser 末尾手写的整数判断，
        # 现在必须由 IntervalSportParser.can_handle 接管
        self.assertEqual(self.name_of(23), "IntervalSportParser")
        self.assertEqual(self.name_of("23"), "IntervalSportParser")
        self.assertTrue(self.fit.IntervalSportParser.can_handle(23))
        self.assertFalse(self.fit.IntervalSportParser.can_handle(True))

    def test_unknown_sport_falls_back(self) -> None:
        self.assertEqual(self.name_of("basketball"), "FallbackParser")
        self.assertEqual(self.name_of(None), "FallbackParser")

    def test_fallback_parser_is_last_in_registry(self) -> None:
        # FallbackParser.can_handle 恒真，排在前面会吞掉一切
        names = [type(p).__name__ for p in self.fit._REGISTERED_PARSERS]
        self.assertEqual(names[-1], "FallbackParser")
        self.assertTrue(self.fit.FallbackParser.can_handle("anything"))


class StrengthParserFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fit = load_fit_parser()

    def setUp(self) -> None:
        self.start = datetime(2026, 8, 15, 10, 52, 53, tzinfo=timezone.utc)
        self.session = self.fit.SessionSummary(
            sport="有氧训练",
            sport_raw="training",
            sub_sport="cardio_training",
            start_time=self.start,
            total_elapsed_s=1200.0,
            total_distance_m=3000.0,
            total_calories=232,
            avg_hr=140,
            max_hr=147,
        )
        self.laps = [
            {
                "start_time": self.start,
                "timestamp": self.start + timedelta(seconds=1200),
                "total_elapsed_time": 1200.0,
                "total_distance": 3000.0,
                "avg_heart_rate": 140,
                "max_heart_rate": 147,
                "total_calories": 232,
            }
        ]
        self.hr = [
            self.fit.HRRecord(self.start + timedelta(seconds=i * 10), 138 + (i % 5))
            for i in range(120)
        ]

    def test_no_set_messages_degrades_to_lap_parsing(self) -> None:
        # 这就是原缺陷的实测复现：20 分钟、3km、心率 140 的 training 活动
        # 以前得到 segments=[]，lap 与心率全丢。
        segments = self.fit.StrengthTrainingParser().parse(
            {"lap": self.laps}, self.hr, self.session
        )
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].segment_type, "lap")
        self.assertEqual(segments[0].avg_hr, 140)
        self.assertEqual(segments[0].distance_m, 3000.0)

    def test_unusable_set_messages_also_degrade(self) -> None:
        # set 消息存在但全都缺 start_time —— 解析结果同样是空，必须降级
        segments = self.fit.StrengthTrainingParser().parse(
            {"set": [{"duration": 30, "repetitions": 10}], "lap": self.laps},
            self.hr,
            self.session,
        )
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].segment_type, "lap")

    def test_no_set_and_no_lap_still_keeps_the_session_segment(self) -> None:
        # 连 lap 都没有时退到 session 整段，心率仍然被采到
        segments = self.fit.StrengthTrainingParser().parse({}, self.hr, self.session)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].lap_trigger, "session")
        self.assertEqual(segments[0].duration_s, 1200.0)
        self.assertIsNotNone(segments[0].avg_hr)

    def test_real_sets_are_not_affected_by_the_fallback(self) -> None:
        # 修降级不能把正常的力量训练解析改坏
        sets = [
            {
                "start_time": self.start,
                "duration": 40.0,
                "repetitions": 10,
                "weight": 60.0,
                "set_type": "active",
            },
            {
                "start_time": self.start + timedelta(seconds=40),
                "duration": 60.0,
                "repetitions": 0,
                "weight": 0.0,
                "set_type": "rest",
            },
        ]
        segments = self.fit.StrengthTrainingParser().parse(
            {"set": sets, "lap": self.laps}, self.hr, self.session
        )
        self.assertEqual([s.segment_type for s in segments], ["set_active", "set_rest"])
        self.assertEqual(segments[0].repetitions, 10)
        self.assertEqual(segments[0].weight_kg, 60.0)

    def test_empty_session_without_start_time_returns_empty(self) -> None:
        # 没有起始时间就真的无从构造分段；这里只确认不抛异常
        session = self.fit.SessionSummary(
            sport="训练", sport_raw="training", sub_sport="", start_time=None
        )
        self.assertEqual(self.fit.StrengthTrainingParser().parse({}, [], session), [])


class SportNamingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fit = load_fit_parser()

    def test_sub_sport_refines_the_broad_training_label(self) -> None:
        # 单看 sport=training 只能得到"训练"，没有信息量
        self.assertEqual(self.fit._sport_to_zh("training", "", "cardio_training"), "有氧训练")
        self.assertEqual(self.fit._sport_to_zh("training", "", "yoga"), "瑜伽")
        self.assertEqual(self.fit._sport_to_zh("generic", "", "hiit"), "高强度间歇")

    def test_file_provided_name_still_wins(self) -> None:
        self.assertEqual(
            self.fit._sport_to_zh("training", "有氧运动", "cardio_training"), "有氧运动"
        )

    def test_falls_back_to_the_sport_label_when_sub_sport_is_unknown(self) -> None:
        self.assertEqual(self.fit._sport_to_zh("training", "", None), "训练")
        self.assertEqual(self.fit._sport_to_zh("training", "", "some_new_sub"), "训练")
        self.assertEqual(self.fit._sport_to_zh("cycling", "", "spin"), "骑行")
        self.assertEqual(self.fit._sport_to_zh(None), "未知运动")


class WiringTest(unittest.TestCase):
    """源码断言：防止修复退化成又一处死代码。"""

    def setUp(self) -> None:
        self.parser_source = (REPO_ROOT / "fithealth_agent" / "fit_parser.py").read_text(
            encoding="utf-8"
        )
        self.main_source = module_source(consumer_home("upload_fit"))

    def test_pick_parser_actually_calls_can_handle(self) -> None:
        body = self.parser_source.split("def _pick_parser(", 1)[1]
        self.assertIn("parser.can_handle(", body)

    def test_parse_fit_file_passes_sub_sport_to_the_router(self) -> None:
        self.assertIn("_pick_parser(sport_raw, sub_sport_raw)", self.parser_source)

    def test_strength_parser_has_a_fallback(self) -> None:
        body = self.parser_source.split("class StrengthTrainingParser(", 1)[1].split(
            "\nclass ", 1
        )[0]
        self.assertIn("LapBasedParser().parse(", body)

    def test_upload_fit_branches_on_segment_types_not_sport_raw(self) -> None:
        # 若仍按 sport_raw 判定，降级出来的 lap 分段会被当成"动作组"渲染成
        # 一串重量 0 / 次数 0 的假数据
        self.assertIn(
            "is_strength = has_sets or (", self.main_source
        )


class InWindowContractTest(unittest.TestCase):
    """DATA-34 回归：`_in_window` 的"start 为 None 时不过滤"契约必须钉住。

    原缺陷是 multisport 的过滤条件写成 `start_bound and ...`——子 session
    缺 `start_time`（中断活动最常见的形状）时条件恒 False，那一段的 lap 与
    set 一条不剩地被丢。这条契约此前无任何测试，所以缺陷才能出厂。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.fit = load_fit_parser()

    def setUp(self) -> None:
        self.start = datetime(2026, 8, 26, 1, tzinfo=timezone.utc)
        self.end = self.start + timedelta(seconds=60)

    def test_start_none_means_no_filtering(self) -> None:
        # 无论消息带什么时间戳（哪怕远在窗外、甚至根本没有时间戳），
        # start 为 None 时都必须收下
        for item in (
            {},
            {"start_time": self.start - timedelta(days=1)},
            {"start_time": self.start + timedelta(days=1)},
            {"timestamp": self.start},
            {"start_time": None, "timestamp": None},
        ):
            with self.subTest(item=item):
                self.assertTrue(self.fit._in_window(item, None, None))
                self.assertTrue(self.fit._in_window(item, None, self.end))

    def test_normal_window_keeps_inside_and_drops_outside(self) -> None:
        inside = {"start_time": self.start + timedelta(seconds=30)}
        before = {"start_time": self.start - timedelta(seconds=1)}
        after = {"start_time": self.end}  # 右边界开区间，等于 end 即窗外
        self.assertTrue(self.fit._in_window(inside, self.start, self.end))
        self.assertTrue(self.fit._in_window({"start_time": self.start}, self.start, self.end))
        self.assertFalse(self.fit._in_window(before, self.start, self.end))
        self.assertFalse(self.fit._in_window(after, self.start, self.end))

    def test_open_ended_window_and_missing_timestamp(self) -> None:
        # end 为 None：右侧开放，只要不早于 start 就收
        self.assertTrue(
            self.fit._in_window({"start_time": self.start + timedelta(days=3)}, self.start, None)
        )
        self.assertFalse(
            self.fit._in_window({"start_time": self.start - timedelta(seconds=1)}, self.start, None)
        )
        # 在过滤的前提下，取不到时间戳的消息算窗外
        self.assertFalse(self.fit._in_window({}, self.start, self.end))
        self.assertFalse(self.fit._in_window({"start_time": "not-a-datetime"}, self.start, self.end))
        # timestamp 兜底（无 start_time 时用它），naive 时间按 UTC 解释
        self.assertTrue(
            self.fit._in_window({"timestamp": self.start.replace(tzinfo=None)}, self.start, self.end)
        )

    def test_multisport_keeps_every_lap_of_a_session_without_start_time(self) -> None:
        # 端到端确认：缺 start_time 的子会话仍拿到属于它的 lap
        fit = self.fit
        summaries = [
            fit.SessionSummary("跑步", "running", start_time=self.start, total_elapsed_s=60),
            fit.SessionSummary("骑行", "cycling", start_time=None, total_elapsed_s=60),
        ]
        raw = {
            "record": [],
            "lap": [
                {"marker": "timed", "start_time": self.start},
                {"marker": "untimed"},
            ],
            "set": [],
        }
        seen: list[list[str]] = []

        class RecordingParser:
            def parse(self, local, _hr_records, _summary):
                seen.append([item["marker"] for item in local.get("lap", [])])
                return []

        original = fit._pick_parser
        fit._pick_parser = lambda *_args, **_kwargs: RecordingParser()
        try:
            _session, _segments, note = fit._parse_multisport(raw, [], summaries)
        finally:
            fit._pick_parser = original

        # 先按时间归属，剩下的无时间戳消息归入缺 start_time 的会话，一条不丢
        self.assertEqual(seen, [["timed"], ["untimed"]])
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
