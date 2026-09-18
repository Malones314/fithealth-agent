"""共享单例与可替换的外部依赖引用（main.py 拆分：阶段 1）。

拆分之后 routes / workflows 各层都要用到同一批 store 实例。如果每个模块各自
`import main`，立刻形成循环导入；如果各自再 new 一个 store，那就是两份状态互相覆盖。
所以这里做**单一持有点**。

## 为什么调用方必须写 `deps.X` 而不是 `from deps import X`

`from x import f` 会在调用方模块里绑一个**新名字**。测试替换 `deps.soreness_store`
时，那个副本还指向旧实例——测试变绿但桩根本没生效。这是本次拆分里最危险的一类回归
（拆分计划里的"约束 A"），因为它不报错、不留痕，只是让一整组用例失去意义。
属性访问在调用时才解析，所以凡是**可能被整体替换**的东西都必须写 `deps.X`：

- 12 个 store / service 实例；
- 10 个被测试打桩的外部依赖函数（下面第二组）。

反过来，**不会被替换**的东西按名字导入就好，没有"副本指向旧对象"的问题：上传体积
上限（见 `upload_io.py`）、`logger`、两个签名辅助函数。

## 为什么不能在 `runtime/__init__.py` 里 eager import 本模块

见 `runtime/__init__.py` 的说明：store 实例化必须晚于 `FITHEALTH_DATA_DIR` 设值。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fithealth_agent.backup_service import LocalBackupService
from fithealth_agent.external_model_settings import ExternalModelSettingsStore
from fithealth_agent.health_importer import HealthImportService
from fithealth_agent.health_store import HealthStore
from fithealth_agent.hr_stream_store import HRStreamStore
from fithealth_agent.info_store import InfoStore
from fithealth_agent.maintenance import MAINTENANCE
from fithealth_agent.observability import TraceStore
from fithealth_agent.plan_draft_cache import PlanDraftCache
from fithealth_agent.plan_store import TrainingPlanStore
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.storage import DailyRecordStore, UserProfileStore
from fithealth_agent.settings import set_runtime_settings_provider

# ── 被测试打桩的外部依赖 ────────────────────────────────────────────────
# 这些名字在本模块里"未被使用"是故意的：它们就是为了让调用方走
# `deps.route_chat_intent(...)`，从而使 `monkeypatch.setattr(deps, ...)` 生效。
# 不要因为静态检查报"unused import"就删掉，也不要改成 `import ... as _...`。
from fithealth_agent import create_fithealth_agent  # noqa: F401
from fithealth_agent.chat_intent_router import route_chat_intent  # noqa: F401
from fithealth_agent.fit_parser import parse_fit_file  # noqa: F401
from fithealth_agent.food_analysis import analyze_food_image  # noqa: F401
from fithealth_agent.health_importer import (  # noqa: F401
    extract_activity_fits,
    inspect_fit_source,
)
from fithealth_agent.health_safety import classify_user_health_statement  # noqa: F401
from fithealth_agent.information_router import route_information  # noqa: F401
from fithealth_agent.muscle_recovery import parse_soreness_reply  # noqa: F401
from fithealth_agent.plan_goal_validator import validate_plan_goal_alignment  # noqa: F401
from fithealth_agent.weekly_summary import build_current_week_reply  # noqa: F401


#: 全局共享的日志器。用固定名字而不是 `__name__`：拆分后 routes / workflows 各层
#: 都从这里取 logger，用 `__name__` 的话日志里会出现一串
#: `fithealth_agent.runtime.deps`，反而看不出是哪一层打的。
logger = logging.getLogger("fithealth")


# ── 共享单例 ────────────────────────────────────────────────────────────
profile_store = UserProfileStore()
daily_record_store = DailyRecordStore()
info_store = InfoStore()
external_model_settings_store = ExternalModelSettingsStore()
set_runtime_settings_provider(external_model_settings_store.agent_runtime_settings)
soreness_store = SorenessStore()
plan_store = TrainingPlanStore()
# 生成计划时在服务端留一份完整正文，避免"保存刚才的计划"回捞被截断的
# 聊天历史（BUG-05）。进程内短期缓存，不持久化。
plan_draft_cache = PlanDraftCache()
health_store = HealthStore()
# 已保存训练的 1Hz 心率流旁挂存储（DATA-05）：训练记录里只留摘要，
# 原始流不进 daily_records.json（会撑爆 ReAct 观察）也不进 health.db
# 的 heart_rate_samples（会把日均心率按采样点等权算歪）。
hr_stream_store = HRStreamStore()
health_import_service = HealthImportService(health_store)
# TRACE-06：agent 执行轨迹的治理入口。它不是用户数据（不进备份、不需要恢复点），
# 但里面有健康细节，所以必须受「删除全部数据」管辖。构造不碰文件系统——deps 在应用
# 启动时就 import，在这里建目录会破坏"trace 关闭即零副作用"。
trace_store = TraceStore()


def _clear_traces_after_restore() -> int:
    """恢复备份后清空 trace。

    为什么恢复后要清：trace 是**恢复之前**那份数据的诊断记录。留着它，新数据与旧轨迹
    混在一个目录里，排障时会读到与当前状态矛盾的结论。

    为什么写成函数而不是直接把 `trace_store.clear` 传进回调列表：绑定方法会在 import
    时就被捕获，测试替换 `deps.trace_store` 之后回调还指向旧实例——就是本模块开头那条
    "打桩会静默失效"。这里在**调用时**才解析模块全局量。
    """
    return trace_store.clear()


# DATA-12：恢复备份要同时换掉 4 个 JSON 与 health.db，所以把维护开关和
# HealthStore 都交给备份服务——它需要竖开关、排空在飞请求、独占数据库。
backup_service = LocalBackupService(
    daily_record_store.db_path.parent,
    gate=MAINTENANCE,
    database=health_store,
    # 顺序有意义：`maintenance_ops.import_backup` 按位置取 `callback_results[0]`
    # 当作记忆库重校验结果，所以 `info_store.revalidate` 必须留在第一位。
    # 两个回调都**绝不抛**——恢复已经生效之后再抛，会让一次成功的恢复被报成失败。
    on_restored=[info_store.revalidate, _clear_traces_after_restore],
)


# ── 餐盘分析置信度的签名状态 ────────────────────────────────────────────
# 餐盘分析签发 token、营养保存校验 token，两条路径必须用**同一把**密钥。所以密钥和
# 两个辅助函数一起放在这唯一的持有点：拆分后如果 routes/uploads.py 和
# routes/records.py 各自 `os.urandom(32)`，签出来的 token 永远验不过，
# 而症状只是"保存营养记录时置信度被静默降级"。
_configured_analysis_secret = (
    os.getenv("FITHEALTH_SIGNING_KEY")
    or os.getenv("VISION_API_KEY")
    or os.getenv("LLM_API_KEY")
)
_analysis_signing_key = (
    _configured_analysis_secret.encode("utf-8")
    if _configured_analysis_secret
    else os.urandom(32)
)


def _sign_analysis_confidence(confidence: str) -> str:
    signature = hmac.new(
        _analysis_signing_key, confidence.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{confidence}.{signature}"


def _verified_analysis_confidence(token: str) -> str | None:
    try:
        confidence, signature = token.split(".", 1)
    except ValueError:
        return None
    if confidence not in {"low", "medium", "high"}:
        return None
    expected = _sign_analysis_confidence(confidence).split(".", 1)[1]
    return confidence if hmac.compare_digest(signature, expected) else None
