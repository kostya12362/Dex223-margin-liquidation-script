from typing import Sequence, Iterable, AsyncGenerator, Protocol

from loguru import logger
from web3 import AsyncWeb3
from web3.types import BlockData

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.utils.w3 import load_abi, init_topics
from repositories import PositionRepository, PoolRepository
from models import Position as PositionModel


class ILogProvider(Protocol):
    async def get_logs(self, block: BlockData) -> list[dict]:
        ...


class IEventProcessor(Protocol):
    topics: dict[str, str]  # eventName -> topic0

    async def process(self, log: dict, block: BlockData) -> Iterable[int]:
        ...


class AssetEventProcessor(IEventProcessor):
    EVENTS = ["NewAsset", "AssetRemoved"]

    def __init__(self, w3: AsyncWeb3, session: AsyncSession):
        self.abi = load_abi(settings.ABI_PATH / "margin_module.json")
        self.contract = w3.eth.contract(
            address=settings.MARGIN_MODULE_ADDRESS,
            abi=self.abi,
        )
        self.repo_pos = PositionRepository(session)
        self.topics = init_topics(self.abi, self.EVENTS)

    async def process(self, log: dict, block: BlockData) -> Iterable[int]:
        if log["address"] != settings.MARGIN_MODULE_ADDRESS:
            return []
        topic0 = log["topics"][0].to_0x_hex()
        event_name = next(name for name, t in self.topics.items() if t == topic0)
        logger.info(f"Processing log with topic0: {topic0} - event: {event_name}")
        ev = getattr(self.contract.events, event_name)().process_log(log)
        position_id = ev["args"]["positionId"]

        if await self.repo_pos.get_by_id(position_id) is None:
            await self.repo_pos.upsert(PositionModel(id=position_id))

        pools = await self.contract.functions.getPositionActualPools(
            position_id, settings.FEE_TIERS
        ).call()
        await self.repo_pos.update_pools(position_id, pools)

        return []  # don't return positions for liquidator


class PoolEventProcessor(IEventProcessor):
    EVENTS = ["Initialize", "Swap", "Mint", "Burn", "Flash", "Collect"]

    def __init__(self, session: AsyncSession):
        self.repo_pool = PoolRepository(session)
        self.abi = load_abi(settings.ABI_PATH / "pool.json")
        self.topics = init_topics(self.abi, self.EVENTS)

    async def process(self, log: dict, block: BlockData) -> Iterable[int]:
        topic0 = log["topics"][0].to_0x_hex()
        if topic0 not in self.topics.values():
            return []
        pool_addr = log["address"]
        return await self.repo_pool.find_positions(pool_addr)


class CombinedLogProvider(ILogProvider):
    """
    Collects all topic0 from processors and does one eth_getLogs per block.
    """

    def __init__(
            self,
            w3: AsyncWeb3,
            session: AsyncSession,
            processors: Sequence[IEventProcessor]
    ):
        self.w3 = w3
        self.pool_repo = PoolRepository(session)
        self.topics: list[str] = [t for p in processors for t in p.topics.values()]

    async def get_logs(self, block: BlockData) -> list[dict]:
        return await self.w3.eth.get_logs({
            "fromBlock": block["number"],
            "toBlock": block["number"],
            "topics": [self.topics],
        })


class CombinedListener:
    def __init__(
            self,
            log_provider: ILogProvider,
            processors: Sequence[IEventProcessor],
            session: AsyncSession
    ):
        self.log_provider = log_provider
        self.processors = processors
        self.session = session

    async def handle_block(self, block: BlockData) -> AsyncGenerator[int, None]:
        logs = await self.log_provider.get_logs(block)
        for log in logs:
            for processor in self.processors:
                for position_id in await processor.process(log, block):
                    logger.info(f"Position {position_id} processed by {processor.__class__.__name__}")
                    yield position_id
        await self.session.commit()
