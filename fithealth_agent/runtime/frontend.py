"""Built frontend delivery."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPOSITORY_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
router = APIRouter()


@lru_cache(maxsize=1)
def _read_index(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = DIST_DIR / "index.html"
    if not index_path.is_file():
        message = (
            f"FitHealthAgent frontend is unavailable: {index_path}. "
            "Run `npm ci && npm run build` in frontend/ before starting the service."
        )
        return HTMLResponse(message, status_code=503)
    return HTMLResponse(_read_index(index_path))


def register(app: FastAPI) -> None:
    """Register frontend routes without adding assembly logic to ``main.py``."""
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets", check_dir=False), name="assets")
    app.add_api_route("/", index, methods=["GET"], response_class=HTMLResponse)
