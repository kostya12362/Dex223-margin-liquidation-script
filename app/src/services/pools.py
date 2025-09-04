from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from repositories import PoolRepository


class PoolService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.sf = session_factory

    async def affected_positions(self, pool_address: str, ts: int) -> list[int]:
        repo = PoolRepository(session_factory=self.sf)
        return list(await repo.find_positions(pool_id=pool_address, cutoff_ts=ts))
