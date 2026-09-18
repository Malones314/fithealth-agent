from __future__ import annotations

import os
import unittest
from functools import partial
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fithealth_agent.info_store import resolve_confirmed_memory_facts
from fithealth_agent.domain.recovery_view import render_session_intro
from tests.source_tools import load_symbols


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_intro_builder():
    wanted = {
        "profile_summary", "configured_model_name", "active_temporary_health_facts",
        "build_session_intro",
    }

    class UserProfileStoreStub:
        DEFAULT_EQUIPMENT = ["哑铃", "哑铃凳"]

    class ProfileStoreStub:
        def get_profile(self):
            return {
                "weekly_weight_kg": [70.0, 69.8],
                "height_cm": 175,
                "birth_date": "1995-01-01",
                "sex": "male",
                "goal": "减脂",
                "equipment": ["哑铃"],
            }

        def missing_fields(self, profile):
            return []

    class ItemsStoreStub:
        def __init__(self, items):
            self.items = items

        def list_records(self):
            return self.items

        def list_plans(self):
            return self.items

        def get_all(self):
            return self.items

        def list_imports(self):
            return self.items

        def cleanup_expired(self):
            return 0

    class SettingsStoreStub:
        def get(self):
            return {"external_models_enabled": False}

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    # 阶段 1 之后 build_session_intro 读的是 `deps.profile_store` 等属性，所以这里注入
    # 一个替身 deps 而不是一个个散装 store——这正好把"这个函数依赖哪些 store"变成了
    # 显式的一处声明（阶段 3c 会据此把它拆成 workflow 收集 + domain 渲染两段）。
    deps_stub = SimpleNamespace(
        profile_store=ProfileStoreStub(),
        daily_record_store=ItemsStoreStub([1, 2]),
        plan_store=ItemsStoreStub([1]),
        info_store=ItemsStoreStub([
            {
                "user_confirmed": True,
                "facts": [{
                    "namespace": "health", "key": "recovery_status",
                    "value": "膝盖疼痛", "status": "active",
                    "duration_type": "temporary",
                    "valid_from": (today - timedelta(days=1)).isoformat(),
                    "valid_until": (today + timedelta(days=7)).isoformat(),
                }],
            },
            2,
            3,
        ]),
        health_store=ItemsStoreStub([1, 2, 3, 4]),
        external_model_settings_store=SettingsStoreStub(),
    )
    namespace = {
        "os": os,
        "date": date,
        "datetime": datetime,
        "ZoneInfo": ZoneInfo,
        "UserProfileStore": UserProfileStoreStub,
        "deps": deps_stub,
        "resolve_confirmed_memory_facts": resolve_confirmed_memory_facts,
        "render_session_intro": render_session_intro,
    }
    loaded = load_symbols(wanted, namespace=namespace)
    loaded["active_temporary_health_facts"] = partial(
        loaded["active_temporary_health_facts"],
        resolve_facts_fn=resolve_confirmed_memory_facts,
    )
    return loaded["build_session_intro"]


class SessionIntroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_intro = staticmethod(load_intro_builder())

    def test_includes_model_privacy_profile_and_local_counts(self) -> None:
        intro = self.build_intro()
        self.assertIn("## 欢迎回来", intro)
        self.assertIn("外部模型：已关闭", intro)
        self.assertIn("### 个人档案", intro)
        self.assertIn("身高(cm)：175", intro)
        self.assertIn("年龄：", intro)
        self.assertNotIn("出生日期：1995-01-01", intro)
        self.assertIn("训练记录：2 条", intro)
        self.assertIn("健康/睡眠导入：4 次", intro)
        self.assertIn("### 今日健康复查", intro)
        self.assertIn("膝盖疼痛", intro)
        self.assertIn("已恢复、减轻、无变化还是加重", intro)
        self.assertIn("### 可使用功能", intro)
        self.assertIn("查看训练记录", intro)
        self.assertIn("查看训练计划", intro)
        self.assertIn("我想练背，帮我生成训练计划", intro)
        self.assertIn("更改周计划", intro)


if __name__ == "__main__":
    unittest.main()
