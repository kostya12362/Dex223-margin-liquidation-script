import copy
import asyncio
from loguru import logger
from typing import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from web3 import AsyncWeb3
from eth_account import Account
from web3.types import BlockData, HexBytes

from core.contracts import MarginModuleClient
from repositories import PositionRepository
from config import settings


@dataclass
class SubjectToLiquidation:
    status: bool
    liquidator: str | None
    frozen_ts: int | None
    predict_timestamp: int | None
    liquidated: bool = False


class LiquidatorService:
    def __init__(
            self,
            w3: AsyncWeb3,
            mm: MarginModuleClient,
            session_factory: async_sessionmaker[AsyncSession],
    ):
        self.w3 = w3
        self.mm = mm
        self.sf = session_factory

        self.account = Account.from_key(settings.PRIVATE_KEY)
        self.from_address = self.account.address
        self.liquidate_address = settings.LIQUIDATE_ADDRESS or self.from_address
        self._nonce: int = 0
        self._nonce_lock = asyncio.Lock()
        self._chain_id = None

        # positionId -> tx_hash of freeze
        self._freeze_txs: dict[int, HexBytes] = {}
        self.frozen_positions: dict[int, int] = {}  # positionId -> block_number

    async def frozen(self, position_id: int, block: BlockData) -> None:
        # already processed / awaiting receipt
        if position_id in self._freeze_txs or position_id in self.frozen_positions:
            return

        subject = await self.mm.subject_to_liquidation(position_id)
        if subject.liquidated is True:
            logger.info(f"Position {position_id} already liquidated at block {block['number']}")
            await PositionRepository(session_factory=self.sf).set_liquidated(position_id)
            return

        if subject.status is False or subject.liquidator is not None:
            logger.info(f"Position {position_id} not eligible at block {block['number']}")
            await PositionRepository(session_factory=self.sf).set_predict_timestamp(
                position_id=position_id, ts=subject.predict_timestamp
            )
            return

        # send freeze (first call freezes on your address)
        tx_hash = await self._send_liquidate_tx(position_id)
        self._freeze_txs[position_id] = tx_hash
        logger.info(f"Sent freeze tx {tx_hash.to_0x_hex()} for position {position_id}")

        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
        block_number = receipt['blockNumber']
        if receipt['status'] != 1:
            logger.error(f"Freeze tx {tx_hash.to_0x_hex()} failed for position {position_id}")
            del self._freeze_txs[position_id]
            return

        logger.info(f"Freeze tx mined in block {block_number} for position {position_id}")

        # move from queue -> frozen
        del self._freeze_txs[position_id]
        self.frozen_positions[position_id] = block_number

    async def freeze_now(self, position_id: int) -> None:
        """Start freeze without block context (used at startup)."""
        if position_id in self._freeze_txs or position_id in self.frozen_positions:
            return

        subject = await self.mm.subject_to_liquidation(position_id)
        if subject.liquidated is True:
            await PositionRepository(session_factory=self.sf).set_liquidated(position_id)
            return
        if subject.status is False or subject.liquidator is not None:
            await PositionRepository(session_factory=self.sf).set_predict_timestamp(
                position_id, subject.predict_timestamp
            )
            return

        tx_hash = await self._send_liquidate_tx(position_id)
        self._freeze_txs[position_id] = tx_hash
        logger.info(f"[startup] Sent freeze tx {tx_hash.to_0x_hex()} for position {position_id}")

        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
        if receipt['status'] != 1:
            logger.error(f"[startup] Freeze tx {tx_hash.to_0x_hex()} failed for position {position_id}")
            del self._freeze_txs[position_id]
            return

        del self._freeze_txs[position_id]
        self.frozen_positions[position_id] = receipt['blockNumber']

    def to_liquidate(self, block: BlockData) -> Iterable[int]:
        to_process = copy.copy(self.frozen_positions)
        if not to_process:
            logger.debug(f"No frozen positions to liquidate block {block['number']}")
            return

        for position_id, frozen_block in to_process.items():
            if block["number"] <= frozen_block:
                continue
            yield position_id

    async def liquidate(self, position_id, block: BlockData) -> None:
        try:
            del self.frozen_positions[position_id]
            subject = await self.mm.subject_to_liquidation(position_id)

            # we check that we are the liquidator
            if subject.status is True and subject.liquidator == self.liquidate_address and subject.liquidated is False:
                tx_hash = await self._send_liquidate_tx(position_id)
                logger.info(f"Sent liquidation tx {tx_hash.to_0x_hex()} for {position_id}")
                await PositionRepository(session_factory=self.sf).set_liquidated(position_id)
            else:
                logger.info(f"Position {position_id} no longer liquidatable at block {block['number']}")
        except Exception as e:
            logger.error(f"Error liquidating position {position_id}: {e}")

    async def _send_liquidate_tx(self, position_id: int) -> HexBytes:
        if self._chain_id is None:
            self._chain_id = await self.w3.eth.chain_id

        async with self._nonce_lock:
            pending = await self.w3.eth.get_transaction_count(self.from_address, "pending")
            if pending > self._nonce or self._nonce == 0:
                self._nonce = pending
            nonce_to_use = self._nonce
            self._nonce += 1

        max_priority_fee, hist = await asyncio.gather(
            self.w3.eth.max_priority_fee,
            self.w3.eth.fee_history(1, "latest", [10]),
        )

        priority_fee = max_priority_fee + 2_000_000_000  # +2 gwei
        base_fee = hist["baseFeePerGas"][-1]
        max_fee = base_fee + priority_fee

        tx = await self.mm.contract.functions.liquidate(
            position_id, self.liquidate_address
        ).build_transaction({
            "from": self.from_address,
            "nonce": nonce_to_use,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee,
            "gas": 50_000_000,
            "chainId": self._chain_id,
        })

        signed = self.w3.eth.account.sign_transaction(tx, private_key=settings.PRIVATE_KEY)
        return await self.w3.eth.send_raw_transaction(signed.raw_transaction)
