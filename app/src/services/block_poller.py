import asyncio
from web3 import AsyncWeb3
from web3.types import BlockData
from loguru import logger


class BlockPoller:
    def __init__(self, w3: AsyncWeb3, queue: asyncio.Queue[BlockData]):
        self.w3 = w3
        self.queue = queue
        self.last: int | None = None

    async def run(self):
        while True:
            head = await self.w3.eth.get_block("latest", full_transactions=False)
            current = head["number"]

            if self.last is None:
                # we start with the last known
                self.last = current

            if self.last < current:
                gap = current - self.last
                if gap > 1:
                    logger.warning(f"⏳ Skipped blocks: {self.last+1}..{current-1} (catching up)")

                # we are consistently catching up: [last+1 .. current]
                for n in range(self.last + 1, current + 1):
                    # to avoid making an extra rpc for the very last one, use the already received head
                    blk = head if n == current else await self.w3.eth.get_block(n, full_transactions=False)
                    await self.queue.put(blk)
                    logger.info(f"🗓 New block {blk['number']}")
                    self.last = n

            await asyncio.sleep(1)
