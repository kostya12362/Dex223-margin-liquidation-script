from __future__ import annotations
from typing import AsyncGenerator, Set
from web3 import AsyncWeb3
from web3.types import BlockData

from core.topics import TopicsRegistry
from core.contracts import MarginModuleClient
from services.positions import PositionService
from services.pools import PoolService


class EventOrchestrator:
    """
   Single point of decision making:
    - collect unique Position_id positions and address pools in one block,
    - perform the required actions exactly once,
    - return UNIQUE positions for freeze/liquidation.
    """

    def __init__(
            self,
            w3: AsyncWeb3,
            topics: TopicsRegistry,
            mm: MarginModuleClient,
            positions: PositionService,
            pools: PoolService,
    ):
        self.w3 = w3
        self.topics = topics
        self.mm = mm
        self.positions = positions
        self.pools = pools

    async def handle_block(self, block: BlockData) -> AsyncGenerator[int, None]:
        logs = await self._get_logs(block)

        positions_from_assets: Set[int] = set()
        pools_touched: Set[str] = set()

        # 1) Classifying logs in one pass
        for log in logs:
            topic0 = self.topics.topic0_of(log)

            # asset-events only at MarginModule address
            if self.topics.is_asset_topic(topic0) and log["address"] == self.mm.address:
                name = self.topics.asset_event_name(topic0)  # "NewAsset" | "AssetRemoved"
                ev = getattr(self.mm.contract.events, name)().process_log(log)
                pos_id = int(ev["args"]["positionId"])
                positions_from_assets.add(pos_id)
                continue

            # pool-events: collecting pool addresses
            if self.topics.is_pool_topic(topic0):
                pools_touched.add(log["address"])

        # 2) Processing asset positions (ensure + sync_pools + optional prime)
        for pid in positions_from_assets:
            await self.positions.ensure_exists(pid)
            await self.positions.sync_pools(pid)

        # 3) For all pools - find affected positions (unique!)
        emitted: Set[int] = set()
        for pool_addr in pools_touched:
            for pid in await self.pools.affected_positions(pool_addr, block['timestamp']):
                if pid not in emitted:
                    emitted.add(pid)
                    yield pid

    async def _get_logs(self, block: BlockData) -> list[dict]:
        if not self.topics.all_topics:
            return []
        return await self.w3.eth.get_logs({
            "fromBlock": block["number"],
            "toBlock": block["number"],
            "topics": [self.topics.all_topics],
        })
