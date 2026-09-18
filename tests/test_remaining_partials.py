"""收尾这一批"部分完成"条目的行为回归（2026-08-27）。

覆盖 BUG-23 / BUG-25 / BUG-26 / DATA-24 / DATA-25。这几条原先都"改了一半"，
而且各自都有一个专门的坑，所以每条至少钉一个**会因为回退而变红**的行为断言：

* `BUG-23` 的 `blocked` 条件曾以 `not load.needs_reduction` 起头，使追加的
  `explicitly_requested` 判断成为死代码——测试必须构造"有酸痛反馈的未恢复肌群"，
  否则怎么写都是绿的（原清单已警告过这点）。
* `BUG-25` 的否定否决只在否定词位于部位名**之前**时生效，所以必须钉住后置形态。
* `BUG-26` 三个子问题各钉一条：窗口折扣、`BASE_RECOVERY_HOURS` 覆盖率不变量、
  `muscles_for_sport` 的角色。
* `DATA-24`/`DATA-25` 都是"0 与 NULL 混为一谈"或"取到前一天的值"，构造数据时要让
  两种情形可区分。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import main
from fithealth_agent.health_store import HealthStore
from fithealth_agent.runtime import deps
from fithealth_agent.muscle_map import (
    MUSCLE_META,
    MUSCLE_RULES,
    muscles_for_exercise,
    muscles_for_sport,
)
from fithealth_agent.muscle_recovery import (
    BASE_RECOVERY_HOURS,
    SECONDARY_WINDOW_FACTOR,
    MuscleLoad,
    MuscleRecoverySnapshot,
    build_recovery_snapshot,
)

BJ = ZoneInfo("Asia/Shanghai")


def _load(
    muscle_id: str,
    region: str,
    *,
    hours_remaining: float,
    needs_reduction: bool = False,
    soreness_level: str = "unknown",
    role: str = "primary",
) -> MuscleLoad:
    now = datetime(2026, 8, 21, 12, 0, tzinfo=BJ)
    return MuscleLoad(
        muscle_id=muscle_id,
        zh=MUSCLE_META[muscle_id][0],
        region=region,
        last_trained_at=now - timedelta(hours=6),
        weekday_zh="周五",
        exercises=("深蹲",),
        effective_sets=6.0,
        recovery_hours=48.0,
        recovered_at=now + timedelta(hours=hours_remaining),
        hours_remaining=hours_remaining,
        role=role,
        needs_reduction=needs_reduction,
        soreness_level=soreness_level,
    )


class Bug23ExplicitlyRequestedTest(unittest.TestCase):
    """未恢复就该拦，除非用户明确要求练这个区域。"""

    PLAN = "深蹲：4组\n"

    def validate(self, load: MuscleLoad, requested: set[str] | None) -> list[str]:
        snapshot = MuscleRecoverySnapshot(
            loads=(load,), recovering=(load,) if load.hours_remaining > 0 else (),
        )
        return main.validate_generated_training_plan(
            self.PLAN, [], recovery=snapshot, explicitly_requested=requested,
        )

    def test_an_unrecovered_muscle_without_soreness_is_blocked(self) -> None:
        load = _load("quadriceps", "腿部", hours_remaining=20.0)
        self.assertTrue(any("恢复" in item for item in self.validate(load, None)))

    def test_soreness_alone_no_longer_switches_the_rule_off(self) -> None:
        """核心回归：以前只要有任何一条生效酸痛反馈，这条硬校验就对该肌群失效。"""
        load = _load(
            "quadriceps", "腿部", hours_remaining=20.0,
            needs_reduction=True, soreness_level="sore",
        )
        self.assertTrue(
            any("恢复" in item for item in self.validate(load, None)),
            "有酸痛反馈的未恢复肌群仍必须被拦下",
        )

    def test_an_explicit_request_lifts_the_block_for_that_region(self) -> None:
        load = _load(
            "quadriceps", "腿部", hours_remaining=20.0,
            needs_reduction=True, soreness_level="sore",
        )
        self.assertEqual(self.validate(load, {"腿部"}), [])

    def test_an_explicit_request_for_another_region_does_not_lift_it(self) -> None:
        load = _load(
            "quadriceps", "腿部", hours_remaining=20.0,
            needs_reduction=True, soreness_level="sore",
        )
        self.assertTrue(any("恢复" in item for item in self.validate(load, {"胸部"})))

    def test_a_recovered_muscle_is_never_blocked(self) -> None:
        load = _load("quadriceps", "腿部", hours_remaining=0.0)
        self.assertEqual(self.validate(load, None), [])


class Bug25ClauseNegationTest(unittest.TestCase):
    """"明确要求训练某部位"必须按小句判定，且否定词在部位名之后也要生效。"""

    def regions(self, message: str) -> list[str]:
        return main.explicitly_requested_recovery_regions(message)

    def test_negation_after_the_region_name_is_honoured(self) -> None:
        """原清单点名的形态：否定词在部位名之后，旧实现漏掉。"""
        self.assertEqual(self.regions("帮我安排今天的训练，腹部不要练"), [])

    def test_negation_before_the_region_name_still_works(self) -> None:
        self.assertEqual(self.regions("今天不想练腿"), [])
        self.assertEqual(self.regions("腿还在恢复就别练腿了"), [])

    def test_a_past_tense_mention_is_not_a_request(self) -> None:
        self.assertEqual(self.regions("昨天练了腿，今天练什么"), [])

    def test_a_statement_is_not_a_request(self) -> None:
        """裸的"训练"两个字不该让"腰部不适影响训练"变成"明确要求练腰"。"""
        self.assertEqual(self.regions("腰部不适影响训练"), [])

    def test_a_genuine_request_is_still_detected(self) -> None:
        self.assertEqual(self.regions("今天想练腿"), ["腿部"])
        self.assertEqual(self.regions("帮我安排今天的腿部训练"), ["腿部"])
        self.assertEqual(self.regions("我要练胸"), ["胸部"])

    def test_one_clause_requests_while_another_vetoes(self) -> None:
        self.assertEqual(self.regions("今天练腿，但手臂不要练"), ["腿部"])


class Bug26SecondaryLoadTest(unittest.TestCase):
    def records(self, exercise: str, sets: int, day: int) -> list[dict]:
        return [{
            "date": f"2026-08-{day:02d}",
            "category": "training",
            "record": {"segments": [
                {
                    "segment_type": "set_active",
                    "category": exercise,
                    "start_time": f"2026-08-{day:02d}T10:{index:02d}:00+08:00",
                }
                for index in range(sets)
            ]},
        }]

    def snapshot(self, exercise: str, sets: int = 3) -> MuscleRecoverySnapshot:
        return build_recovery_snapshot(
            self.records(exercise, sets, 21),
            now=datetime(2026, 8, 21, 20, 0, tzinfo=BJ),
        )

    # ---- 问题 1：次要肌群的窗口折扣 ----
    def test_a_secondary_only_muscle_gets_a_discounted_window(self) -> None:
        """"练一次背"里硬拉带来的小臂次要负荷，不该拿到和主项一样的窗口。"""
        loads = {load.muscle_id: load for load in self.snapshot("罗马尼亚硬拉").loads}
        forearms = loads["forearms"]
        self.assertEqual(forearms.role, "secondary")
        self.assertLessEqual(
            forearms.recovery_hours,
            BASE_RECOVERY_HOURS["forearms"] * SECONDARY_WINDOW_FACTOR * 1.5 + 0.01,
        )
        self.assertLess(forearms.recovery_hours, BASE_RECOVERY_HOURS["forearms"])

    def test_a_primary_muscle_keeps_the_full_window(self) -> None:
        loads = {load.muscle_id: load for load in self.snapshot("罗马尼亚硬拉").loads}
        hamstrings = loads["hamstrings"]
        self.assertEqual(hamstrings.role, "primary")
        self.assertGreaterEqual(hamstrings.recovery_hours, BASE_RECOVERY_HOURS["hamstrings"])

    # ---- 问题 2：覆盖率不变量 ----
    def test_every_rule_id_has_a_baseline_window(self) -> None:
        """源码级不变量：加了肌群却忘了配恢复窗口，会静默走 36 小时默认值。"""
        self.assertEqual(set(MUSCLE_META), set(BASE_RECOVERY_HOURS))
        self.assertEqual({rule.muscle_id for rule in MUSCLE_RULES}, set(MUSCLE_META))

    # ---- 问题 3：名字与角色稳定 ----
    def test_a_muscle_id_has_exactly_one_chinese_name(self) -> None:
        """同一个 id 在任何规则下名字都一样——zh 现在来自 MUSCLE_META。"""
        for rule in MUSCLE_RULES:
            self.assertEqual(rule.zh, MUSCLE_META[rule.muscle_id][0])
            self.assertEqual(rule.region, MUSCLE_META[rule.muscle_id][1])

    def test_sport_fallback_hits_are_always_primary(self) -> None:
        """`muscles_for_sport("跳绳")` 曾按"最后一条规则"取到 secondary/0.4。"""
        hits = {hit.muscle_id: hit for hit in muscles_for_sport("跳绳")}
        self.assertEqual(set(hits), {"calves", "quadriceps"})
        for hit in hits.values():
            self.assertEqual(hit.role, "primary")
            self.assertEqual(hit.weight, 1.0)

    def test_a_primary_keyword_still_wins_over_a_secondary_one(self) -> None:
        hits = {hit.muscle_id: hit.role for hit in muscles_for_exercise("提踵")}
        self.assertEqual(hits.get("calves"), "primary")


class RecoveryRegionSuppressionTest(unittest.TestCase):
    """区域压制只采纳主项负荷（BUG-26 问题 1 的下游）。"""

    def payload(self, load: MuscleLoad) -> dict:
        snapshot = MuscleRecoverySnapshot(loads=(load,), recovering=(load,))
        return main._recovery_context_payload(snapshot, [], [])

    def test_the_recovering_entries_carry_their_role(self) -> None:
        load = _load("forearms", "手臂", hours_remaining=10.0, role="secondary")
        entry = self.payload(load)["recovering"][0]
        self.assertEqual(entry["role"], "secondary")

    def test_a_primary_load_still_reports_its_role(self) -> None:
        load = _load("quadriceps", "腿部", hours_remaining=10.0)
        self.assertEqual(self.payload(load)["recovering"][0]["role"], "primary")


class Data24And25SummaryTest(unittest.TestCase):
    DAY = "2026-08-21"

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.store = HealthStore(root / "health.db", root / "raw")

    def stamp(self, hour: int, minute: int = 0) -> dict:
        local = datetime.fromisoformat(f"{self.DAY}T{hour:02d}:{minute:02d}:00+08:00")
        return {
            "timestamp_utc": local.astimezone(timezone.utc).isoformat(),
            "timestamp_local": local.isoformat(),
            "local_date": self.DAY,
        }

    def save(self, tag: str = "a", **payload) -> None:
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
                "earliest_utc": None, "latest_utc": None,
                "device_serial": "device", "warnings": [],
                "record_count": 1, "message_counts": {}, "data_types": [],
                "heart_rates": payload.get("heart_rates", []),
                "metric_samples": [], "hrv_statuses": [], "sleep_stages": [],
                "activity_observations": payload.get("activity_observations", []),
                "device_metrics": payload.get("device_metrics", []),
                "intensity_observations": payload.get("intensity_observations", []),
            }],
        })

    def summary(self, column: str):
        with self.store._connection() as connection:
            row = connection.execute(
                f"SELECT {column} AS v FROM daily_health_summary WHERE date = ?", (self.DAY,)
            ).fetchone()
        return row["v"] if row else None

    # ---- DATA-25 ----
    def test_a_genuine_zero_of_intensity_is_not_stored_as_null(self) -> None:
        """0 分钟和"没有强度数据"不是一回事；实测 16 天里有 7 天落进这个分支。"""
        self.save(
            heart_rates=[{**self.stamp(8, index), "bpm": 70} for index in range(12)],
            intensity_observations=[
                {**self.stamp(9), "moderate_activity_s": 0.0,
                 "vigorous_activity_s": 0.0, "intensity_level": None},
            ],
        )
        self.assertEqual(self.summary("moderate_activity_min"), 0.0)
        self.assertEqual(self.summary("vigorous_activity_min"), 0.0)
        self.assertEqual(self.summary("intensity_minutes"), 0.0)
        sections = self.store.get_daily_health(self.DAY)
        self.assertIsNotNone(sections["intensity"])

    def test_a_day_without_any_observation_still_reports_no_intensity(self) -> None:
        self.save(heart_rates=[{**self.stamp(8, index), "bpm": 70} for index in range(12)])
        self.assertIsNone(self.summary("moderate_activity_min"))
        self.assertIsNone(self.store.get_daily_health(self.DAY)["intensity"])

    def test_non_zero_intensity_still_aggregates(self) -> None:
        self.save(
            heart_rates=[{**self.stamp(8, index), "bpm": 70} for index in range(12)],
            intensity_observations=[
                {**self.stamp(9), "moderate_activity_s": 120.0,
                 "vigorous_activity_s": 60.0, "intensity_level": None},
            ],
        )
        self.assertEqual(self.summary("moderate_activity_min"), 2.0)
        self.assertEqual(self.summary("intensity_minutes"), 4.0)

    # ---- DATA-24 ----
    def test_a_midnight_only_resting_heart_rate_is_not_adopted(self) -> None:
        """跨零点的残留行带的是前一天的静息心率，不能当成当天的值。"""
        self.save(
            heart_rates=[{**self.stamp(8, index), "bpm": 70} for index in range(12)],
            device_metrics=[{**self.stamp(0, 7), "resting_heart_rate": 61}],
        )
        self.assertIsNone(self.summary("resting_heart_rate"))

    def test_a_daytime_resting_heart_rate_is_adopted(self) -> None:
        self.save(
            heart_rates=[{**self.stamp(8, index), "bpm": 70} for index in range(12)],
            device_metrics=[
                {**self.stamp(0, 7), "resting_heart_rate": 61},
                {**self.stamp(13, 56), "resting_heart_rate": 55},
            ],
        )
        self.assertEqual(self.summary("resting_heart_rate"), 55)

    def test_the_time_gate_does_not_apply_to_the_metabolic_rate(self) -> None:
        """RMR 是"设备对当天的陈述"，00:0x 给出完全正常，不该按时刻判掉。"""
        self.save(
            heart_rates=[{**self.stamp(8, index), "bpm": 70} for index in range(12)],
            device_metrics=[{**self.stamp(0, 7), "resting_metabolic_rate": 2230.0}],
        )
        self.assertEqual(self.summary("resting_metabolic_rate"), 2230.0)

    def test_a_single_sample_day_does_not_get_a_summary_row(self) -> None:
        """幽灵日：只有一个跨零点残留采样点的日期不该建行（修法 1，此处一并钉住）。"""
        self.save(heart_rates=[{**self.stamp(0), "bpm": 66}])
        self.assertIsNone(self.summary("heart_rate_avg"))


class Bug46AnalyzeFoodErrorShapeTest(unittest.TestCase):
    """`/analyze_food` 的非预期异常也必须回 `error` 键。

    BUG-46：以前只捕获 `FoodAnalysisError`，其他异常（网络超时、视觉服务返回怪结构、
    JSON 解析炸掉）会变成 FastAPI 默认的 500 `{"detail": ...}`，前端读 `data.error`
    得到 undefined，用户看到一片空白。
    """

    def post(self, side_effect):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        with (
            patch.object(
                deps.external_model_settings_store,
                "get",
                return_value={"external_models_enabled": True},
            ),
            patch.object(deps, "analyze_food_image", side_effect=side_effect),
        ):
            return TestClient(main.app, raise_server_exceptions=False).post(
                "/analyze_food",
                files={"file": ("meal.jpg", b"image", "image/jpeg")},
            )

    def test_an_unexpected_exception_still_produces_an_error_key(self) -> None:
        response = self.post(TimeoutError("视觉服务超时"))
        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertIn("error", payload)
        self.assertNotIn("detail", payload)
        self.assertIn("视觉服务超时", payload["error"])

    def test_a_food_analysis_error_still_maps_to_400(self) -> None:
        from fithealth_agent.food_analysis import FoodAnalysisError

        response = self.post(FoodAnalysisError("图片不是食物"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "图片不是食物")


if __name__ == "__main__":
    unittest.main()
