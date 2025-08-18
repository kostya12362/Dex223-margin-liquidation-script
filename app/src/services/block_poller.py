import asyncio
from web3 import AsyncWeb3
from loguru import logger
from web3.types import BlockData


class BlockPoller:
    def __init__(self, w3: AsyncWeb3, queue: asyncio.Queue[BlockData]):
        self.w3 = w3
        self.queue = queue
        self.last = None

    async def run(self):
        while True:
            head = await self.w3.eth.get_block("latest")
            current = head['number']
            if self.last is None:
                self.last = current
            while self.last < current:
                if head['number'] - self.last > 1:
                    logger.warning(f"⏳ Skipping blocks from {self.last} to {head['number']}")
                self.last += 1
                await self.queue.put(head)
                logger.info(f"🗓 New block {self.last}")
            await asyncio.sleep(1)
