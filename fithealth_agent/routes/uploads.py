"""HTTP adapters for upload workflows."""

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from fithealth_agent.observability import set_turn_result, start_turn
from fithealth_agent.workflows.upload_workflow import (
    analyze_food as run_analyze_food,
    upload_activity_from_health_zip as run_upload_activity,
    upload_fit as run_upload_fit,
    upload_health as run_upload_health,
    upload_plan as run_upload_plan,
)


food_router = APIRouter()
router = APIRouter()
activity_router = APIRouter()


@food_router.post("/analyze_food")
async def analyze_food(
    file: UploadFile = File(...), context: str = Form("")
) -> JSONResponse:
    # `context` 是用户手写的补充说明（可能带健康描述），所以按自由文本字段传：
    # meta 级别只落长度与 HMAC 摘要。
    with start_turn("/analyze_food", message=context):
        result = await run_analyze_food(file, context)
        set_turn_result(status_code=result.status_code)
        return JSONResponse(result.body, status_code=result.status_code)


@router.post("/upload_fit")
async def upload_fit(
    file: UploadFile = File(...), overwrite_pending: bool = Form(False)
) -> JSONResponse:
    result = await run_upload_fit(file, overwrite_pending)
    return JSONResponse(result.body, status_code=result.status_code)


@router.post("/upload_plan")
async def upload_plan(
    file: UploadFile = File(...), confirm_large: bool = Form(False)
) -> JSONResponse:
    # 上传的计划正文会送去轻量模型做"这是不是一份训练计划"的语义分类
    # （`plan_classifier.validate_training_plan`），所以这条路由也要有回合。
    with start_turn("/upload_plan"):
        result = await run_upload_plan(file, confirm_large)
        set_turn_result(status_code=result.status_code)
        return JSONResponse(result.body, status_code=result.status_code)


@router.post("/upload_health")
async def upload_health(files: list[UploadFile] = File(...)) -> JSONResponse:
    result = await run_upload_health(files)
    return JSONResponse(result.body, status_code=result.status_code)


@activity_router.post("/upload_health/activity")
async def upload_activity_from_health_zip(
    file: UploadFile = File(...),
    activity_name: str = Form(...),
    overwrite_pending: bool = Form(False),
) -> JSONResponse:
    result = await run_upload_activity(file, activity_name, overwrite_pending)
    return JSONResponse(result.body, status_code=result.status_code)
