"""Canonical locations for frontend sources and generated artifacts."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_SOURCE_DIR = FRONTEND_DIR / "src"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
FRONTEND_BUILD_INDEX = FRONTEND_DIST_DIR / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
def active_frontend_index() -> Path:
    """Return the build artifact, falling back to the Vite source shell for tests."""
    return FRONTEND_BUILD_INDEX if FRONTEND_BUILD_INDEX.is_file() else FRONTEND_DIR / "index.html"
