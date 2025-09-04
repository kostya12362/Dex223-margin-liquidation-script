from __future__ import annotations
import asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.contracts import MarginModuleClient
from repositories import PositionRepository
from services.positions import PositionService
from services.liquidator import LiquidatorService


class StartupInitializer:
    def __init__(
            self,
            mm: MarginModuleClient,
            session_factory: async_sessionmaker[AsyncSession],
            pos_service: PositionService,
            liquidator: LiquidatorService,
            *,
            concurrency: int = 8,
    ):
        self.mm = mm
        self.sf = session_factory
        self.pos_service = pos_service
        self.liquidator = liquidator
        self.sem = asyncio.Semaphore(concurrency)

    async def run(self):
        total = await self.mm.position_index()
        existing = set(await PositionRepository(session_factory=self.sf).get_skip_position_ids())
        missing = [pid for pid in range(total) if pid not in existing]

        if not missing:
            logger.info("StartupInitializer: no missing items")
            return

        logger.warning(f"StartupInitializer: initialization {len(missing)} positions (from {total})")

        async def _init_one(pid: int):
            async with self.sem:
                dto = await self.mm.subject_to_liquidation(pid)

                # upsert
                await PositionRepository(session_factory=self.sf).upsert_position(
                    id=pid,
                    is_liquidated=dto.liquidated,
                    predict_check_timestamp=dto.predict_timestamp,
                )

                # sync pools if not liquidated
                if not dto.liquidated:
                    await self.pos_service.sync_pools(pid)

                # ⛓️ freeze immediately if eligible and nobody froze yet
                if dto.status is True and dto.liquidator is None:
                    await self.liquidator.freeze_now(pid)

        results = await asyncio.gather(*(_init_one(pid) for pid in missing), return_exceptions=True)
        for e in results:
            if isinstance(e, Exception):
                logger.exception("StartupInitializer task failed: {}", e)

        logger.info("StartupInitializer: completed")
