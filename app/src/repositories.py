from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select, delete, update, exists
from sqlalchemy.dialects.postgresql import insert

from models import Position, Pool, position_pool
from core.utils.w3 import ADDRESS_ZERO


class BaseRepository:
    """
    The repository can work:
    - with a passed session (batch/composition) -> self.session
    - or with a factory (default) -> self.sf, the session is created on the method
    """

    def __init__(
            self,
            session: Optional[AsyncSession] = None,
            session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    ):
        self.session = session
        self.sf = session_factory
        if self.session is None and self.sf is None:
            raise ValueError("Either session or session_factory must be provided")

    @asynccontextmanager
    async def _session_scope(self, *, tx: bool):
        """
        Gives a session for use in a method.
        - If the session is passed from outside -> do not close it.
         If tx=True and there is no active transaction - open begin().
        - If the factory is passed -> create a short-lived session for the method. If tx=True use begin().
        """
        if self.session is not None:
            s = self.session
            if tx:
                # safe: begin() only if there is no transaction
                txobj = s.get_transaction()
                if txobj is None or not txobj.is_active:
                    async with s.begin():
                        yield s
                    return
            # without explicit transaction (autobegin/commit is decided by the calling code)
            yield s
            return
        # self.sf is not None — create a new session for the method
        async with self.sf() as s:  # type: ignore
            if tx:
                async with s.begin():
                    yield s
            else:
                yield s


class PositionRepository(BaseRepository):
    async def upsert_position(
            self,
            *,
            id: int,  # noqa
            is_liquidated: bool | None = None,
            predict_check_timestamp: int | None = None,
    ):
        """
        Upsert by PK (id). If the record exists, update the statuses.
        Transaction enabled (tx=True).
        """
        stmt = (
            insert(Position)
            .values(
                id=id,
                is_liquidated=is_liquidated,
                predict_check_timestamp=predict_check_timestamp,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "is_liquidated": insert(Position).excluded.is_liquidated,
                    "predict_check_timestamp": insert(Position).excluded.predict_check_timestamp,
                },
            )
        )
        async with self._session_scope(tx=True) as s:
            await s.execute(stmt)

    async def get_by_id(self, position_id: int) -> Position | None:
        stmt = select(Position).where(Position.id == position_id)
        async with self._session_scope(tx=False) as s:
            res = await s.execute(stmt)
            return res.scalar_one_or_none()

    async def update_pools(self, position_id: int, new_pools: list[str]) -> None:
        # fetch existing
        stmt = select(position_pool.c.pool_id).where(position_pool.c.position_id == position_id)
        async with self._session_scope(tx=True) as s:
            result = await s.execute(stmt)
            existing = set(result.scalars().all())

            new_set = set(filter(lambda x: x != ADDRESS_ZERO, new_pools))
            to_delete = existing - new_set
            to_add = new_set - existing

            if to_delete:
                await s.execute(
                    delete(position_pool).where(
                        position_pool.c.position_id == position_id,
                        position_pool.c.pool_id.in_(to_delete),
                    )
                )

            if to_add:
                to_position_pool = [{"position_id": position_id, "pool_id": pid} for pid in to_add]
                to_pool = [{"id": pid} for pid in to_add]

                await s.execute(
                    insert(Pool)
                    .values(to_pool)
                    .on_conflict_do_nothing(index_elements=["id"])
                )

                await s.execute(
                    insert(position_pool)
                    .values(to_position_pool)
                    .on_conflict_do_nothing(index_elements=["position_id", "pool_id"])
                )

    async def set_liquidated(self, position_id: int) -> None:
        stmt = update(Position).where(Position.id == position_id).values(is_liquidated=True)
        async with self._session_scope(tx=True) as s:
            await s.execute(stmt)

    async def set_predict_timestamp(self, position_id: int, ts: int | None) -> None:
        stmt = update(Position).where(Position.id == position_id).values(predict_check_timestamp=ts)
        async with self._session_scope(tx=True) as s:
            await s.execute(stmt)

    async def get_skip_position_ids(self) -> list[int]:
        stmt = select(Position.id)
        async with self._session_scope(tx=False) as s:
            result = await s.execute(stmt)
            return list(result.scalars().all())


class PoolRepository(BaseRepository):
    async def get_or_create(self, address: str) -> Pool:
        async with self._session_scope(tx=True) as s:
            res = await s.execute(select(Pool).where(Pool.id == address))
            pool = res.scalar_one_or_none()
            if not pool:
                pool = Pool(id=address)
                s.add(pool)
                await s.commit()
                # flush in transaction ok; commit will happen on exit begin()
                await s.flush()
            return pool

    async def find_positions(self, pool_id: str, cutoff_ts: int | None = None) -> Iterable[int]:
        ts = cutoff_ts or int(datetime.now(tz=timezone.utc).timestamp())
        ts += 20  # small buffer for possible clock skew
        q_pool = (
            select(Position.id.label("position_id"))
            .where(
                Position.is_liquidated.is_(False),
                exists(
                    select(1)
                    .select_from(position_pool)
                    .where(
                        position_pool.c.position_id == Position.id,
                        position_pool.c.pool_id == pool_id,
                    )
                ),
            )
        )

        q_overdue = (
            select(Position.id.label("position_id"))
            .where(
                Position.is_liquidated.is_(False),
                Position.predict_check_timestamp.is_not(None),
                Position.predict_check_timestamp <= ts,
            )
        )
        stmt = q_pool.union(q_overdue)
        async with self._session_scope(tx=False) as s:
            res = await s.execute(stmt)
            return res.scalars().all()
