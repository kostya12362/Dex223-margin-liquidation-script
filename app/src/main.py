import asyncio
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from loguru import logger

from config import settings
from core.logger import setup_logger
from core.retrying_provider import RetryingHTTPProvider
from core.utils.w3 import load_abi
from core.contracts import MarginModuleClient
from core.topics import TopicsRegistry
from core.db import SessionFactory

from services.block_poller import BlockPoller
from services.positions import PositionService
from services.pools import PoolService
from services.event_orchestrator import EventOrchestrator
from services.liquidator import LiquidatorService
from services.startup_initializer import StartupInitializer

ASSET_EVENTS = ["NewAsset", "AssetRemoved"]
POOL_EVENTS = ["Initialize", "Swap", "Mint", "Burn", "Collect"]

setup_logger()


async def main():
    provider = RetryingHTTPProvider(settings.HTTP_RPC_URL)
    w3 = AsyncWeb3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    mm_abi = load_abi(settings.ABI_PATH / "margin_module.json")
    pool_abi = load_abi(settings.ABI_PATH / "pool.json")
    mm = MarginModuleClient(w3, settings.MARGIN_MODULE_ADDRESS, mm_abi)

    topics = TopicsRegistry(mm_abi=mm_abi, pool_abi=pool_abi, asset_events=ASSET_EVENTS, pool_events=POOL_EVENTS)

    queue: asyncio.Queue = asyncio.Queue()
    poller = BlockPoller(w3, queue)

    # services receive a session factory and open a session for each call
    pos_service = PositionService(SessionFactory, mm)
    pool_service = PoolService(SessionFactory)

    orchestrator = EventOrchestrator(
        w3=w3, topics=topics, mm=mm, positions=pos_service, pools=pool_service
    )
    liquidator = LiquidatorService(w3=w3, mm=mm, session_factory=SessionFactory)

    # Initialization of all positions
    initializer = StartupInitializer(
        mm=mm,
        session_factory=SessionFactory,
        pos_service=pos_service,
        liquidator=liquidator,
        concurrency=8
    )
    await initializer.run()

    async def worker():
        while True:
            block = await queue.get()
            try:
                # on-chain part with retracements
                while True:
                    try:
                        ids = [pid async for pid in orchestrator.handle_block(block)]
                        break
                    except Exception as e:
                        logger.exception(f"handle_block failed on {block['number']}: {e}")
                        await asyncio.sleep(1.0)

                # parallel: each call has its own session/connection
                fr = await asyncio.gather(*(liquidator.frozen(pid, block) for pid in ids), return_exceptions=True)
                for r in fr:
                    if isinstance(r, Exception):
                        logger.warning(f"freeze task error: {r!r}")

                liq = await asyncio.gather(
                    *(liquidator.liquidate(pid, block) for pid in liquidator.to_liquidate(block)),
                    return_exceptions=True
                )
                for r in liq:
                    if isinstance(r, Exception):
                        logger.warning(f"liquidate task error: {r!r}")
            finally:
                queue.task_done()

    await asyncio.gather(poller.run(), worker())


if __name__ == "__main__":
    asyncio.run(main())
