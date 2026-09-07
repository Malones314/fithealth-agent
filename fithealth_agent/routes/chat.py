"""Thin HTTP adapter for the chat workflow."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from fithealth_agent.context_budget import ContextInputError
from fithealth_agent.observability import set_turn_result, start_turn
from fithealth_agent.runtime.responses import context_error_response
from fithealth_agent.workflows.chat_workflow import chat as run_chat_workflow


router = APIRouter()


async def read_chat_request(request: Request) -> dict:
    from fithealth_agent.context_budget import (
        CHAT_REQUEST_MAX_BYTES,
        decode_chat_payload,
        validate_chat_request_headers,
    )

    validate_chat_request_headers(
        request.headers.get("content-type"),
        request.headers.get("content-length"),
    )
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > CHAT_REQUEST_MAX_BYTES:
            raise ContextInputError(
                "REQUEST_TOO_LARGE",
                "聊天请求不能超过 256 KiB。",
                status_code=413,
            )
        chunks.append(chunk)
    return decode_chat_payload(b"".join(chunks))


@router.post("/chat")
async def chat(request: Request) -> JSONResponse:
    try:
        payload = await read_chat_request(request)
    except ContextInputError as exc:
        # 刻意在 turn 之外：请求根本没到 agent，而且这一步还在读 body——在它之前
        # 开 turn 等于让客户端用超大请求随意造 trace 文件。见 observability/http.py。
        return context_error_response(exc)
    history = payload.get("history")
    with start_turn(
        "/chat",
        source=payload.get("source"),
        message=payload.get("message"),
        history_len=len(history) if isinstance(history, list) else 0,
    ):
        result = await run_chat_workflow(payload)
        artifact = result.body.get("artifact")
        set_turn_result(
            source=result.body.get("source"),
            status_code=result.status_code,
            artifact_type=artifact.get("type") if isinstance(artifact, dict) else None,
        )
        # JSONResponse 在 __init__ 里就把 body 渲染成字节，所以序列化失败也落在
        # 回合内（会记成 turn_end.status="error"）。挪到 with 外面就看不见了。
        return JSONResponse(result.body, status_code=result.status_code)
