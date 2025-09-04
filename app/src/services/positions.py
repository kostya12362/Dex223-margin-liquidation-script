from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from repositories import PositionRepository
from core.contracts import MarginModuleClient
from config import settings


class PositionService:
    """Operations on positions; DBs are managed by repositories."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], mm: MarginModuleClient):
        self.sf = session_factory
        self.mm = mm

    async def ensure_exists(self, position_id: int) -> None:
        repo = PositionRepository(session_factory=self.sf)
        exists = await repo.get_by_id(position_id)
        if exists is not None:
            return

        dto = await self.mm.subject_to_liquidation(position_id)
        await repo.upsert_position(
            id=position_id,
            is_liquidated=dto.liquidated,
            predict_check_timestamp=dto.predict_timestamp,
        )

    async def sync_pools(self, position_id: int) -> None:
        pools = await self.mm.get_position_pools(position_id, settings.FEE_TIERS)
        repo = PositionRepository(session_factory=self.sf)
        await repo.update_pools(position_id, pools)
