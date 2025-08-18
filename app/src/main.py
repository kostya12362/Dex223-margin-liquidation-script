import asyncio
from web3 import AsyncWeb3, AsyncHTTPProvider
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
        asyncio.create_task(liquidator.init_skip_positions())

        # Start the liquidator service
        async def worker():
            while True:
                block = await queue.get()
                await asyncio.gather(
                    *[
                        liquidator.frozen(position_id, block)
                        async for position_id in listener.handle_block(block)
                    ]
                )
                await asyncio.gather(
                    *[
                        liquidator.liquidate(position_id, block)
                        for position_id in liquidator.to_liquidate(block)
                    ]
                )

                queue.task_done()

        await asyncio.gather(
            poller.run(),
            worker(),
        )


if __name__ == "__main__":
    asyncio.run(main())
