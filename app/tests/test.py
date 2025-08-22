import re
import asyncio
import json
import pytest
import pytest_asyncio
from web3 import AsyncWeb3, Web3
from web3.providers import AsyncHTTPProvider
from eth_account import Account
from faker import Faker
from loguru import logger

from config import settings, ROOT_PATH
from core.utils.w3 import ADDRESS_ZERO

# === Configuration ===
RPC_URL = settings.HTTP_RPC_URL
PRIVATE_KEY = settings.PRIVATE_KEY
MARGIN_MODULE_ADDRESS = settings.MARGIN_MODULE_ADDRESS
UTILITY_MODULE_CFG2_ADDRESS = Web3.to_checksum_address("0x07f6AADD5934e74382758F47D792360C217215E6")
UTILITY_BULK_POSITION_CREATOR_ADDRESS = Web3.to_checksum_address("0x7543dc80AA7A5A87C5d08B1541b9ACd7F0D7fEd1")

# === Setup AsyncWeb3 and Contracts ===
w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
acct = Account.from_key(PRIVATE_KEY)

with open(ROOT_PATH / "src" / "abi" / 'margin_module.json') as f:
    margin_module_abi = json.load(f)

# Load ABIs (synchronously at startup)
with open(ROOT_PATH / "tests" / "abi" / 'UtilityModuleCFG2.json') as f:
    utility_module_cfg2_abi = json.load(f)

with open(ROOT_PATH / "tests" / "abi" / 'UtilityBulkPositionCreator.json') as f:
    utility_bulk_position_creator_abi = json.load(f)

utility_bulk_position_creator = w3.eth.contract(
    address=UTILITY_BULK_POSITION_CREATOR_ADDRESS,
    abi=utility_bulk_position_creator_abi
)
utility_module_cfg2 = w3.eth.contract(
    address=UTILITY_MODULE_CFG2_ADDRESS,
    abi=utility_module_cfg2_abi
)
margin_module = w3.eth.contract(
    address=MARGIN_MODULE_ADDRESS,
    abi=margin_module_abi,
)

fake = Faker()
# GROUPS_IDS = [1, 2, 3, 15]  # Groups to be created or used in tests
GROUPS_IDS = [5, 6]
BULK_GROUPS_IDS = [17]


class TokenParams:
    def __init__(self):
        name = fake.word().capitalize()
        consonants = re.findall(r'(?i)[b-df-hj-np-tv-z]', name)
        symbol = "".join(consonants).upper()[:3] or name[:3].upper()
        decimals = 6  # random.choice([6, 8, 9, 12, 18])
        self.name, self.symbol, self.decimals = name, symbol, decimals


_nonce_lock = asyncio.Lock()
_next_nonce = None


# === Helper to send transactions ===
async def send(txn_fn):
    # txn_fn is a contract function call, e.g. util2.functions.step1_MakeWhitelist(0)
    global _next_nonce
    # только эту часть — под локом
    async with _nonce_lock:
        if _next_nonce is None:
            _next_nonce = await w3.eth.get_transaction_count(acct.address, 'pending')
        nonce = _next_nonce
        _next_nonce += 1
        tx = await txn_fn.build_transaction({
            'from': acct.address,
            'nonce': nonce,
            'gas': 30_000_000,
            'gasPrice': await w3.eth.gas_price
        })
        signed = acct.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash)
    logger.info(
        f"↳ {txn_fn}: https://sepolia.etherscan.io/tx/{receipt['transactionHash'].to_0x_hex()} | "
        f"status={receipt['status']} | "
        f"gasUsed={receipt['gasUsed']} | "
        f"blockNumber={receipt['blockNumber']}"
    )
    return receipt


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_tokens():
    async def create_if_needed_single(group_id: int):
        tg = await utility_module_cfg2.functions.test_group(group_id).call()
        if tg[0] != ADDRESS_ZERO and tg[1] != ADDRESS_ZERO:
            logger.info(f"Group {group_id} already exists with tokens: {tg[0]}, {tg[1]}")
            return
        t1, t2 = TokenParams(), TokenParams()
        await send(utility_module_cfg2.functions.x0_MakeTokens(
            group_id, t1.name, t1.symbol, t1.decimals,
            t2.name, t2.symbol, t2.decimals,
            acct.address
        ))
        await send(utility_module_cfg2.functions.x1_MakePool10000(group_id))
        await send(utility_module_cfg2.functions.x2_Liquidity(group_id))
        await send(utility_module_cfg2.functions.step1_MakeWhitelist(group_id))

    async def create_if_needed_bulk(group_id: int):
        tg = await utility_bulk_position_creator.functions.test_group(group_id).call()
        if all([token != ADDRESS_ZERO for token in tg[0:5]]):
            logger.info(f"Bulk group {group_id} already exists with tokens: {tg[0:5]}")
            return
        await send(utility_bulk_position_creator.functions.preparation1_Tokens(group_id))

    await asyncio.gather(*(create_if_needed_single(i) for i in GROUPS_IDS))
    await asyncio.gather(*(create_if_needed_bulk(i) for i in BULK_GROUPS_IDS))


# === Tests ===
@pytest.mark.asyncio
async def test_position_for_liquidation():
    """
    #0 Test scenario for creating a position that will be liquidated.
    :return:
    """

    async def _make_positions(group_id: int):
        await send(utility_module_cfg2.functions.step2_MakeSlowOrder(group_id))
        await send(utility_module_cfg2.functions.step3_SupplyOrder(group_id))
        await send(utility_module_cfg2.functions.step4_MakePosition(group_id))
        await send(utility_module_cfg2.functions.step5_MarginSwapAll(group_id))
        await send(utility_module_cfg2.functions.step7_LiquidatingSwapViaPool(group_id))

        # Get positionId из storage
        tg = await utility_module_cfg2.functions.test_group(group_id).call()
        logger.info(f"Test group {group_id} data: {tg}")
        position_id = tg[3]  # token0, token1, orderId, positionId, last_step
        await asyncio.sleep(10)  # wait for the next block to ensure liquidation check
        subject, liquidator, frozen_timestamp, liquidated = await margin_module.functions.subjectToLiquidationExtended(position_id).call()
        logger.info(
            f"Position ID: {position_id} | Group ID: {group_id} | "
            f"subjectToLiquidationExtended: {subject}"
        )
        assert subject is True
        assert liquidator != ADDRESS_ZERO
        assert frozen_timestamp != 0
        assert liquidated is True

    await asyncio.gather(*(_make_positions(i) for i in GROUPS_IDS[0:1]))


@pytest.mark.asyncio
async def test_position_with_multiple_pools():
    """
    #1 Test scenario for creating a position with multiple positions.
    :return:
    """
    group_id = BULK_GROUPS_IDS[0]
    await send(utility_bulk_position_creator.functions.preparation2_Order(group_id))
    await send(utility_bulk_position_creator.functions.step4_bulk_MakePosition(group_id))
    await send(utility_bulk_position_creator.functions.step5_bulk_MarginSwap(group_id, 99))
    tg = await utility_bulk_position_creator.functions.test_group(group_id).call()
    print(tg)
    assert len([i for i in tg[0:5] if i != ADDRESS_ZERO]) == 5
    assert tg[6] != 0  # positionId
    assert tg[7] != 0  # last_step


@pytest.mark.asyncio
async def test_position_not_liquidation_one_block():
    """
    #3.1 Case when (the position was liquid and immediately became illiquid on the next block)
    :return:
    """
    group_id = GROUPS_IDS[0]

    await send(utility_module_cfg2.functions.step2_MakeSlowOrder(group_id))
    await send(utility_module_cfg2.functions.step3_SupplyOrder(group_id))
    await send(utility_module_cfg2.functions.step4_MakePosition(group_id))
    await asyncio.gather(*[
        send(utility_module_cfg2.functions.step5_MarginSwapAll(group_id)),
        send(utility_module_cfg2.functions.step7_LiquidatingSwapWithPullback(group_id))
    ])
    tg = await utility_module_cfg2.functions.test_group(group_id).call()
    position_id = tg[3]  # token0, token1, orderId, positionId, last_step
    logger.info(f"Position ID: {position_id} | Group ID: {group_id}... waiting for liquidation check")
    await asyncio.sleep(10)
    call = asyncio.create_task(margin_module.functions.subjectToLiquidationExtended(position_id).call())
    subject, liquidator, frozen_timestamp, liquidated = await call

    assert subject is False
    assert liquidator == ADDRESS_ZERO
    assert frozen_timestamp == 0
    assert liquidated is False

    # wait for the next block
    logger.info(
        f"subject: {subject}, "
        f"liquidator: {liquidator}, "
        f"frozen_timestamp: {frozen_timestamp}, "
        f"liquidated: {liquidated}"
    )


@pytest.mark.asyncio
async def test_make_position_liquid_on_next_block_illiquid():
    """
    #3.1 Case when (the position was liquid and immediately became illiquid on the next block)
    :return:
    """
    group_id = GROUPS_IDS[0]
    await send(utility_module_cfg2.functions.step2_MakeSlowOrder(group_id))
    await send(utility_module_cfg2.functions.step3_SupplyOrder(group_id))
    await send(utility_module_cfg2.functions.step4_MakePosition(group_id))
    await send(utility_module_cfg2.functions.step5_MarginSwapAll(group_id))
    await utility_module_cfg2.functions.step7_LiquidatingSwapViaPool(group_id).call()
    await asyncio.gather(
        *[
            utility_module_cfg2.functions.step7_OneForZeroSwapViaPool(group_id).call()
            for _ in range(2)
        ]
    )
    tg = await utility_module_cfg2.functions.test_group(group_id).call()
    position_id = tg[3]  # token0, token1, orderId, positionId, last_step
    logger.info(f"Position ID: {position_id} | Group ID: {group_id}... waiting for liquidation check")
    await asyncio.sleep(10)
    call = asyncio.create_task(margin_module.functions.subjectToLiquidationExtended(position_id).call())
    subject, liquidator, frozen_timestamp, liquidated = await call

    assert subject is False
    assert liquidator == ADDRESS_ZERO
    assert frozen_timestamp == 0
    assert liquidated is False

    # wait for the next block
    logger.info(
        f"subject: {subject}, "
        f"liquidator: {liquidator}, "
        f"frozen_timestamp: {frozen_timestamp}, "
        f"liquidated: {liquidated}"
    )


# @pytest.mark.asyncio
# async def test_parallel_creation():
#     """

# @pytest.mark.asyncio
# async def test_liquidity_flip():
#     # 3. Переход в два блока: ликвидна -> не ликвидна
#     await send(util2.functions.step7_LiquidatingSwapViaPool(0))
#     await send(util2.functions.step8_FreezeForLiquidation(0))
#
#     # Имитируем новый блок
#     await w3.provider.make_request('evm_mine', [])
#
#     await send(util2.functions.step7_OneForZeroSwapViaPool(0))
#     tg = await util2.functions.test_group(0).call()
#     pos = tg[2]
#     is_liq = await margin.functions.subjectToLiquidation(pos).call()
#     assert not is_liq
#
#
# @pytest.mark.asyncio
# async def test_multiple_new_asset_events():
#     # 4. Bulk swap - несколько NewAsset в одном блоке
#     receipt = await send(util2.functions.step5_MarginSwapAll(0))
#     events = margin.events.NewAsset().process_receipt(receipt)
#     assert len(events) > 1
#
#
# @pytest.mark.asyncio
# async def test_double_swap_sequence():
#     # 5.1 Сценарий: swap делает ликвидной, затем 1-for-0 возвращает в прибыль
#     await send(util2.functions.step5_MarginSwapAll(0))
#     await send(util2.functions.step7_OneForZeroSwapViaPool(0))
#
#     # 5.2 Сценарий: сначала ликвидизирующий swap, потом pullback+liquidation
#     await send(util2.functions.step7_LiquidatingSwapViaPool(0))
#     await send(util2.functions.step8_PullbackAndLiquidationAgain(0))


if __name__ == "__main__":
    pytest.main()
