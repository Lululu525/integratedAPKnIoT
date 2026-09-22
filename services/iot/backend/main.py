"""FastAPI application entry point.

Run locally with:  uv run uvicorn main:app --reload --app-dir backend
"""

from __future__ import annotations

from api.auth import build_auth_router
from api.routes import router
from config import get_settings
from fastapi import FastAPI

# Fail at boot, not at first login, if JWT_SECRET is missing. Scripts and
# alembic import config without this check so they run before .env exists.
_ = get_settings().jwt_secret

app = FastAPI(title="ESP OTA Backend", version="0.1.0")
app.include_router(build_auth_router())
app.include_router(router)
