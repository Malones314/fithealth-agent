"""Local privacy settings for features that send data to external services."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any
from fithealth_agent.atomic_json import atomic_write_json
from fithealth_agent.json_file_lock import JsonFileLock
from fithealth_agent.settings import AgentRuntimeSettings, data_path, load_agent_runtime_settings


DEFAULT_SETTINGS = {"external_models_enabled": True}
DEFAULT_CHAT_TIMEOUT_SECONDS = 600


class ExternalModelSettingsStore:
    """Persist the user's opt-in for all external AI model requests."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_path("external_model_settings.json")
        self._lock = RLock()
        self._degraded_reason: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, JsonFileLock(self.path):
            if not self.path.exists():
                self._write(DEFAULT_SETTINGS)

    def _read(self) -> dict[str, Any]:
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return dict(DEFAULT_SETTINGS)
        except (OSError, json.JSONDecodeError) as exc:
            self._degraded_reason = str(exc)
            return {"external_models_enabled": False}
        if not isinstance(data, dict) or not isinstance(data.get("external_models_enabled"), bool):
            self._degraded_reason = "隐私设置格式无效"
            return {"external_models_enabled": False}
        self._degraded_reason = None
        result: dict[str, Any] = {"external_models_enabled": data["external_models_enabled"]}
        if isinstance(data.get("runtime_settings"), dict):
            result["runtime_settings"] = data["runtime_settings"]
        return result

    def _write(self, settings: dict[str, Any]) -> None:
        # DATA-06：这里存的是"要不要把数据发给外部模型"的隐私开关。断电后
        # 读到 0 字节文件会静默回落到 DEFAULT_SETTINGS（也就是**开启**），
        # 把用户显式关掉的开关又打开——所以同样必须 fsync 后再 replace。
        atomic_write_json(self.path, settings)

    def get(self) -> dict[str, Any]:
        with self._lock, JsonFileLock(self.path):
            return self._read()

    def storage_status(self) -> dict[str, object]:
        with self._lock, JsonFileLock(self.path):
            settings = self._read()
            return {
                "available": self._degraded_reason is None,
                "degraded_reason": self._degraded_reason,
                **settings,
            }

    def set_external_models_enabled(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("external_models_enabled 必须是布尔值")
        with self._lock, JsonFileLock(self.path):
            settings = self._read()
            settings["external_models_enabled"] = enabled
            self._write(settings)
            self._degraded_reason = None
            return settings

    def runtime_settings(self) -> dict[str, int | float | None]:
        with self._lock, JsonFileLock(self.path):
            saved = self._read().get("runtime_settings")
        if not isinstance(saved, dict):
            agent = load_agent_runtime_settings(os.environ)
            return self._runtime_payload(agent, DEFAULT_CHAT_TIMEOUT_SECONDS)
        agent = AgentRuntimeSettings(
            max_steps=saved.get("agent_max_steps", 15),
            temperature=saved.get("llm_temperature", 0.7),
            max_tokens=saved.get("llm_max_tokens"),
            timeout=saved.get("llm_timeout_seconds", 90),
            max_retries=saved.get("llm_max_retries", 0),
        )
        chat_timeout = saved.get("chat_timeout_seconds", DEFAULT_CHAT_TIMEOUT_SECONDS)
        if isinstance(chat_timeout, bool) or not isinstance(chat_timeout, int) or not 30 <= chat_timeout <= 3600:
            raise ValueError("chat_timeout_seconds 必须为 30..3600 的整数")
        return self._runtime_payload(agent, chat_timeout)

    @staticmethod
    def _runtime_payload(
        agent: AgentRuntimeSettings, chat_timeout: int
    ) -> dict[str, int | float | None]:
        return {
            "agent_max_steps": agent.max_steps,
            "llm_temperature": agent.temperature,
            "llm_max_tokens": agent.max_tokens,
            "llm_timeout_seconds": agent.timeout,
            "llm_max_retries": agent.max_retries,
            "chat_timeout_seconds": chat_timeout,
        }

    def agent_runtime_settings(self) -> AgentRuntimeSettings:
        values = self.runtime_settings()
        return AgentRuntimeSettings(
            max_steps=values["agent_max_steps"],
            temperature=values["llm_temperature"],
            max_tokens=values["llm_max_tokens"],
            timeout=values["llm_timeout_seconds"],
            max_retries=values["llm_max_retries"],
        )

    def set_runtime_settings(self, payload: dict[str, Any]) -> dict[str, int | float | None]:
        max_tokens = payload.get("llm_max_tokens")
        if max_tokens in ("", None):
            max_tokens = None
        agent = AgentRuntimeSettings(
            max_steps=payload.get("agent_max_steps"),
            temperature=payload.get("llm_temperature"),
            max_tokens=max_tokens,
            timeout=payload.get("llm_timeout_seconds"),
            max_retries=payload.get("llm_max_retries"),
        )
        chat_timeout = payload.get("chat_timeout_seconds")
        if isinstance(chat_timeout, bool) or not isinstance(chat_timeout, int) or not 30 <= chat_timeout <= 3600:
            raise ValueError("chat_timeout_seconds 必须为 30..3600 的整数")
        runtime = self._runtime_payload(agent, chat_timeout)
        with self._lock, JsonFileLock(self.path):
            settings = self._read()
            settings["runtime_settings"] = runtime
            self._write(settings)
            self._degraded_reason = None
        return runtime

