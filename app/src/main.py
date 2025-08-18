import asyncio
from loguru import logger
from web3 import AsyncWeb3

from config import settings
from core.db import get_session
from services.block_poller import BlockPoller
from services.event_listeners import (
    AssetEventProcessor,
    PoolEventProcessor,
    CombinedLogProvider,
    CombinedListener
)
from services.liquidator import LiquidatorService
from web3.middleware import ExtraDataToPOAMiddleware
from core.logger import setup_logger
from core.retrying_provider import RetryingHTTPProvider

setup_logger()


async def main():
    provider = RetryingHTTPProvider(settings.HTTP_RPC_URL)
    w3 = AsyncWeb3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    queue: asyncio.Queue = asyncio.Queue()
    poller = BlockPoller(w3, queue)

    async for session in get_session():
        # Make processors for events
        processors = [
            AssetEventProcessor(w3, session),
            PoolEventProcessor(session),
        ]
        # General log provider that combines multiple processors
        log_provider = CombinedLogProvider(
            w3,
            session,
            processors
        )
        listener = CombinedListener(log_provider, processors, session)
        liquidator = LiquidatorService(w3, session)
        await liquidator.init_skip_positions()

        # Start the liquidator service
        async def worker():
            while True:
                block = await queue.get()
                try:
                    while True:
                        try:
                            position_ids = [pid async for pid in listener.handle_block(block)]
                            break
                        except Exception as e:
                            logger.exception(f"handle_block failed on {block['number']}, will retry {e}")
                            await asyncio.sleep(1.0)

                    freeze_results = await asyncio.gather(
                        *(liquidator.frozen(pid, block) for pid in position_ids),
                        return_exceptions=True,
                    )
                    for r in freeze_results:
                        if isinstance(r, Exception):
                            logger.warning(f"freeze task error: {r!r}")

                    liq_results = await asyncio.gather(
                        *(liquidator.liquidate(pid, block) for pid in liquidator.to_liquidate(block)),
                        return_exceptions=True,
                    )
                    for r in liq_results:
                        if isinstance(r, Exception):
                            logger.warning(f"liquidate task error: {r!r}")

                finally:
                    queue.task_done()

        await asyncio.gather(
            poller.run(),
            worker(),
        )


if __name__ == "__main__":
    asyncio.run(main())
