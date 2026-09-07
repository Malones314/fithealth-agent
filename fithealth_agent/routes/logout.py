"""HTTP adapter for the logout workflow."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from fithealth_agent.observability import set_turn_result, start_turn
from fithealth_agent.workflows.logout_workflow import logout as run_logout


router = APIRouter()


@router.post("/logout")
def logout(payload: dict | None = None) -> JSONResponse:
    # 同步端点：FastAPI 在工作线程里跑它，整个回合都在那一个线程内，所以
    # `start_turn` 设的 ContextVar 全程有效（跨线程的限制只影响再往下切线程）。
    messages = (payload or {}).get("messages")
    with start_turn(
        "/logout", history_len=len(messages) if isinstance(messages, list) else 0
    ):
        result = run_logout(payload)
        # logout 的业务 status（saved / not_saved / no_messages / error）记进 source：
        # turn_end 的 `status` 是 ok/error/aborted 这个生命周期三态，被业务值覆盖之后
        # "这次到底是不是崩了"就读不出来了，所以那个名字是保留字段。
        set_turn_result(
            source=result.body.get("status"), status_code=result.status_code
        )
        return JSONResponse(result.body, status_code=result.status_code)
