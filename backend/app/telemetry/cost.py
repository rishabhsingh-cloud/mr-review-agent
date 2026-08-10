"""Cost ledger + budget guard, reading from the agent_events hypertable."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvent


class BudgetExceeded(Exception):
    def __init__(self, spent: float, limit: float):
        self.spent = spent
        self.limit = limit
        super().__init__(f"daily budget exceeded: ${spent:.4f} >= ${limit:.2f}")


async def spend_today(session: AsyncSession) -> float:
    """Total recorded cost since the start of the current day (server time)."""
    stmt = select(func.coalesce(func.sum(AgentEvent.cost_usd), 0)).where(
        AgentEvent.ts >= func.date_trunc("day", func.now())
    )
    return float((await session.execute(stmt)).scalar_one())


async def ensure_budget(session: AsyncSession, limit: float) -> None:
    """Raise BudgetExceeded if today's spend already meets/exceeds the limit."""
    spent = await spend_today(session)
    if spent >= limit:
        raise BudgetExceeded(spent, limit)
