from typing import Iterable
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from models import Position, Pool, position_pool
from sqlalchemy.dialects.postgresql import insert
from core.utils.w3 import ADDRESS_ZERO


class PositionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, pos: Position):
        self.session.add(pos)
        await self.session.commit()

    async def link_pool(self, position_id: int, pool_id: str):
        stmt = insert(position_pool).values(
            position_id=position_id,
            pool_id=pool_id,
        ).on_conflict_do_nothing(
            index_elements=['position_id', 'pool_id']
        )
        await self.session.execute(stmt)

    async def get_by_id(self, position_id: int) -> Position | None:
        stmt = select(Position).where(Position.id == position_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def update_pools(self, position_id: int, new_pools: list[str]) -> None:
        # 1) fetch existing
        stmt = select(position_pool.c.pool_id).where(
            position_pool.c.position_id == position_id
        )
        result = await self.session.execute(stmt)
        existing = set(result.scalars().all())
        new_set = set(filter(lambda x: x != ADDRESS_ZERO, new_pools))
        to_delete = existing - new_set
        to_add = new_set - existing

        if to_delete:
            await self.session.execute(
                delete(position_pool)
                .where(
                    position_pool.c.position_id == position_id,
                    position_pool.c.pool_id.in_(to_delete)
                )
            )

        if to_add:
            to_position_pool = []
            to_pool = []

            for pid in to_add:
                to_position_pool.append({"position_id": position_id, "pool_id": pid})
                to_pool.append({"id": pid})

            await self.session.execute(
                insert(Pool)
                .values(to_pool)
                .on_conflict_do_nothing(
                    index_elements=["id"]
                )
            )

            await self.session.execute(
                insert(position_pool)
                .values(to_position_pool)
                .on_conflict_do_nothing(
                    index_elements=["position_id", "pool_id"]
                )
            )
        await self.session.commit()

    async def set_liquidated(self, position_id: int) -> None:
        stmt = (
            update(Position)
            .where(Position.id == position_id)
            .values(is_liquidated=True)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def get_skip_position_ids(self):
        stmt = select(Position.id)
        result = await self.session.execute(stmt)
        data = result.scalars().all()
        return data


class PoolRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, address: str) -> Pool:
        res = await self.session.execute(
            select(Pool).where(Pool.id == address)
        )
        pool = res.scalar_one_or_none()
        if not pool:
            pool = Pool(id=address)
            self.session.add(pool)
            await self.session.flush()
        return pool

    async def find_positions(self, pool_id: str) -> Iterable[int]:
        stmt = select(
            position_pool.c.position_id
        ).join(Position, Position.id == position_pool.c.position_id).where(
            position_pool.c.pool_id == pool_id,
            Position.frozen_by.is_(None),  # Only active positions
            Position.is_liquidated.is_(False)  # Only non-liquidated positions
        )

        result = await self.session.execute(stmt)
        data = result.scalars().all()
        logger.debug(f"Found positions for pool {pool_id}: {data}")
        return data
