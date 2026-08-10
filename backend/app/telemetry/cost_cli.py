"""CLI: print today's recorded spend and the configured daily budget.

Usage (from backend/, venv active, db up):
    python -m app.telemetry.cost_cli
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.telemetry.cost import spend_today


async def _run() -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        spent = await spend_today(session)
    limit = settings.daily_budget_usd
    pct = (spent / limit * 100) if limit else 0.0
    status = "OVER BUDGET" if spent >= limit else "ok"
    print(f"Today's spend: ${spent:.4f} / ${limit:.2f} budget ({pct:.1f}%) — {status}")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
