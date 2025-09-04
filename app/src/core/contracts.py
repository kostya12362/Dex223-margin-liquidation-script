from __future__ import annotations
from dataclasses import dataclass

from loguru import logger
from web3 import AsyncWeb3, Web3
from web3.contract import AsyncContract
from web3.exceptions import ContractLogicError

from core.utils.w3 import ADDRESS_ZERO


@dataclass
class SubjectToLiquidationDTO:
    status: bool
    liquidator: str | None
    frozen_ts: int | None
    predict_timestamp: int | None
    liquidated: bool


class MarginModuleClient:
    """Encapsulates interaction with MarginModule (ABI/decoding/calls)."""

    def __init__(
            self,
            w3: AsyncWeb3,
            address: str,
            abi: list[dict]
    ):
        self.w3 = w3
        self.address = address
        self.abi = abi
        self.contract: AsyncContract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)

    async def get_position_pools(self, position_id: int, fee_tiers: list[int]) -> list[str]:
        return await self.contract.functions.getPositionActualPools(position_id, fee_tiers).call()

    async def subject_to_liquidation(self, position_id: int) -> SubjectToLiquidationDTO:
        try:
            status, liquidator, frozen_ts, liquidated, predict_timestamp = \
                await self.contract.functions.subjectToLiquidationExtended(
                    position_id
                ).call()
            logger.debug(
                f'subjectToLiquidationExtended status: {status},'
                f'liquidator: {liquidator}, '
                f'frozen_ts: {frozen_ts}, '
                f'liquidated: {liquidated}, '
                f'position_id: {position_id}, '
                f"predict_timestamp: {predict_timestamp}"
            )
        except ContractLogicError as e:
            logger.error(f"subjectToLiquidationExtended reverted for {position_id}: {e}")
            return SubjectToLiquidationDTO(False, None, None, None, False)
        return SubjectToLiquidationDTO(
            status=status,
            frozen_ts=frozen_ts,
            liquidator=None if liquidator == ADDRESS_ZERO else liquidator,
            liquidated=liquidated,
            predict_timestamp=predict_timestamp if predict_timestamp != 0 else None,
        )

    async def position_index(self) -> int:
        return await self.contract.functions.positionIndex().call()
