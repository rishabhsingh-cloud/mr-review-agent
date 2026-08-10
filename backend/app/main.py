"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.hitl import router as hitl_router
from app.api.reviews import router as reviews_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.db.session import get_session
from app.telemetry.cost import spend_today

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(title="MR Review Agent", version="0.1.0")
app.include_router(webhooks_router)
app.include_router(reviews_router)
app.include_router(hitl_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Send the bare root to the dashboard so http://host/ just works."""
    return RedirectResponse(url="/dashboard/")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/cost/today")
async def cost_today(session: AsyncSession = Depends(get_session)) -> dict:
    spent = await spend_today(session)
    return {"spend_usd": round(spent, 6), "budget_usd": settings.daily_budget_usd}


# Bit 12 — Dashboard (static, no build step). Served same-origin so the
# frontend calls the read/HITL endpoints above directly (no CORS needed).
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/dashboard", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")
