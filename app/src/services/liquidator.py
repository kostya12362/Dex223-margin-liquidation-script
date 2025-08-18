import copy
import asyncio
import json
from loguru import logger
from typing import Iterable
from dataclasses import dataclass

from web3 import AsyncWeb3
from eth_account import Account
from web3.types import BlockData, HexBytes
from web3.exceptions import ContractLogicError

from core.utils.w3 import ADDRESS_ZERO
from repositories import PositionRepository
from models import Position as PositionModel

from config import settings


@dataclass
class SubjectToLiquidation:
    status: bool
    liquidator: str | None
    frozen_ts: int | None
    liquidated: bool = False


class LiquidatorService:
    def __init__(self, w3: AsyncWeb3, session):
        self.w3 = w3
        self.repo_pos = PositionRepository(session)

        with open(f"{settings.ABI_PATH}/margin_module.json") as f:
            self.abi = json.load(f)

        self.contract = self.w3.eth.contract(
            address=settings.MARGIN_MODULE_ADDRESS,
            abi=self.abi,
        )
        self.account = Account.from_key(settings.PRIVATE_KEY)
        self.from_address = self.account.address
        self.liquidate_address = settings.LIQUIDATE_ADDRESS or self.from_address
        self._nonce = 0
        self._nonce_lock = asyncio.Lock()
        self._chain_id = None
        # positionId -> tx_hash of freeze
        self._freeze_txs: dict[int, HexBytes] = {}
        self.frozen_positions: dict[int, int] = {}  # positionId -> block_number

    async def check_positions(self, position_id: int) -> SubjectToLiquidation:
        try:

            status, liquidator, frozen_ts, liquidated = await self.contract.functions.subjectToLiquidationExtended(
                position_id
            ).call()
            logger.debug(
                f'subjectToLiquidationExtended status: {status},'
                f' liquidator: {liquidator},'
                f' frozen_ts: {frozen_ts},'
                f' position_id: {position_id}'
            )
        except ContractLogicError as e:
            logger.error(f"subjectToLiquidationExtended reverted for {position_id}: {e}")
            return SubjectToLiquidation(False, None, None, False)
        return SubjectToLiquidation(
            status=status,
            frozen_ts=frozen_ts,
            liquidator=None if liquidator == ADDRESS_ZERO else liquidator,
            liquidated=liquidated
        )

    async def frozen(self, position_id: int, block: BlockData) -> None:
        # if we are already waiting for receipt or it is frozen - exit
        if position_id in self._freeze_txs or position_id in self.frozen_positions:
            return

        subject = await self.check_positions(position_id)
        if subject.status is False or subject.liquidator is not None:
            logger.info(f"Position {position_id} not eligible at block {block['number']}")
            return

        # try:
        # send freeze (use the same liquidate function,
        # but specify your address - it freezes on the first call)
        tx_hash = await self._send_liquidate_tx(position_id)
        self._freeze_txs[position_id] = tx_hash
        logger.info(f"Sent freeze tx {tx_hash.to_0x_hex()} for position {position_id}")

        # we are waiting for the transaction to get into the block
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
        block_number = receipt['blockNumber']
        if receipt['status'] != 1:
            logger.error(f"Freeze tx {tx_hash.to_0x_hex()} failed for position {position_id}")
            del self._freeze_txs[position_id]
            return
        logger.info(f"Freeze tx mined in block {block_number} for position {position_id}")

        # move from _freeze_txs → frozen_positions
        del self._freeze_txs[position_id]
        self.frozen_positions[position_id] = block_number
        # except Exception as e:
        #     logger.error(f"Error freezing position {position_id}: {e}")

    def to_liquidate(self, block: BlockData) -> Iterable[int]:
        to_process = copy.copy(self.frozen_positions)
        if not to_process:
            logger.debug(f"No frozen positions to liquidate block {block['number']}")
            return

        # we copy so as not to mutate during iterations
        for position_id, frozen_block in to_process.items():
            # we are waiting for the minimum of the next block
            if block["number"] <= frozen_block:
                continue
            yield position_id

    async def liquidate(self, position_id, block: BlockData) -> None:
        try:
            del self.frozen_positions[position_id]
            subject = await self.check_positions(position_id)
            # we check that we are the liquidator
            if subject.status is True and subject.liquidator == self.liquidate_address:
                tx_hash = await self._send_liquidate_tx(position_id)
                logger.info(f"Sent liquidation tx {tx_hash.to_0x_hex()} for {position_id}")
                await self.repo_pos.set_liquidated(position_id)
            else:
                logger.info(f"Position {position_id} no longer liquidatable at block {block['number']}")
        except Exception as e:
            logger.error(f"Error liquidating position {position_id}: {e}")

    async def _send_liquidate_tx(self, position_id: int) -> HexBytes:
        if self._chain_id is None:
            self._chain_id = await self.w3.eth.chain_id
        async with self._nonce_lock:
            if self._nonce is None:
                # This is the first time we've definitely taken it from the internet
                self._nonce = await self.w3.eth.get_transaction_count(
                    self.from_address, "pending"
                )
            else:
                # just in case insurance
                pending = await self.w3.eth.get_transaction_count(
                    self.from_address, "pending"
                )
                if pending > self._nonce:
                    self._nonce = pending

            nonce_to_use = self._nonce
            self._nonce += 1

        max_priority_fee, hist = await asyncio.gather(
            self.w3.eth.max_priority_fee,
            self.w3.eth.fee_history(1, "latest", [10])
        )

        priority_fee = max_priority_fee + 2_000_000_000  # +2 gwei

        base_fee = hist["baseFeePerGas"][-1]
        max_fee = base_fee + priority_fee

        tx = await self.contract.functions.liquidate(position_id, self.liquidate_address).build_transaction({
            "from": self.from_address,
            "nonce": nonce_to_use,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee,
            "gas": 50_000_000,
            "chainId": self._chain_id,
        })

        signed = self.w3.eth.account.sign_transaction(
            tx, private_key=settings.PRIVATE_KEY
        )
        return await self.w3.eth.send_raw_transaction(signed.raw_transaction)

    async def init_skip_positions(self):
        """
        Initialize old positions that were frozen before the liquidator service started.
        This is necessary to ensure that we can process them correctly.
        """
        positions = await self.repo_pos.get_skip_position_ids()
        total_position = await self.contract.functions.positionIndex().call()
        missing = set(range(total_position)) - set(positions)  # TODO check positionIndex
        if missing:
            logger.warning(f"Missing positions: {missing}")
            for position_id in missing:
                subject = await self.check_positions(position_id)

                await self.repo_pos.upsert(PositionModel(id=position_id, is_liquidated=subject.liquidated))
                if subject.liquidated is True:
                    logger.info(f"Position {position_id} is already liquidated, skipping")
                    continue
                pools = await self.contract.functions.getPositionActualPools(
                    position_id, settings.FEE_TIERS
                ).call()
                await self.repo_pos.update_pools(position_id, pools)
