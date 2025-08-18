import re
import json
import asyncio

from faker import Faker

from web3 import AsyncWeb3, AsyncHTTPProvider, Web3
from web3.middleware import ExtraDataToPOAMiddleware

from config import settings, ROOT_PATH

# 1) Настройки — подставьте свои
# print(settings.HTTP_RPC_URL)
RPC_URL = settings.HTTP_RPC_URL
PRIVATE_KEY = settings.PRIVATE_KEY
CONTRACT_ADDRESS = Web3.to_checksum_address("0x252D414f545903256B1E2C0BD00C18D2135F9a30")
ABI_PATH = ROOT_PATH / "test" / "abi" / "UtilityModuleCfg.json"

fake = Faker()


class TokenParams:
    def __init__(self):
        name = fake.word().capitalize()
        consonants = re.findall(r'(?i)[b-df-hj-np-tv-z]', name)
        symbol = "".join(consonants).upper()[:3] or name[:3].upper()
        decimals = 6  # random.choice([6, 8, 9, 12, 18])
        self.name, self.symbol, self.decimals = name, symbol, decimals


async def main():
    # 1) Подключаемся к RPC
    w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    # если сеть на PoA (например, тесты), раскомментируйте:
    # w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    # 2) Готовим аккаунт
    account = w3.eth.account.from_key(PRIVATE_KEY)
    w3.eth.default_account = account.address

    # 3) Загружаем ABI и создаём контракт
    with open(ABI_PATH) as f:
        abi = json.load(f)
    module = w3.eth.contract(address=CONTRACT_ADDRESS, abi=abi)

    # 4) Локальный nonce‑счётчик
    local_nonce = None

    async def get_nonce():
        nonlocal local_nonce
        if local_nonce is None:
            # учитываем и «pending»
            local_nonce = await w3.eth.get_transaction_count(account.address, "pending")
        else:
            local_nonce += 1
        return local_nonce

    # 5) Утилита для отправки транзакций
    async def send(function, value: int = 0):
        chain_id = await w3.eth.chain_id
        nonce = await get_nonce()
        gas_price = await w3.eth.gas_price
        print(gas_price)
        # оценка газа
        gas_est = await function.estimate_gas({"from": account.address, "value": value})

        txn = await function.build_transaction({
            "chainId": chain_id,
            "from": account.address,
            "nonce": nonce,
            "gas": int(gas_est * 1.4),
            "gasPrice": int(gas_price * 1.3),
            "value": value,
        })

        signed = account.sign_transaction(txn)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash)
        print(
            f"↳ https://sepolia.etherscan.io/tx/0x{receipt['transactionHash'].hex()} | status={receipt['status']} | gasUsed={receipt['gasUsed']} |"
        )
        return receipt

    # 6) Генерируем параметры двух новых токенов
    t1 = TokenParams()
    t2 = TokenParams()
    print(f"→ New tokens: {t1.name}/{t1.symbol}/{t1.decimals}, {t2.name}/{t2.symbol}/{t2.decimals}")

    data = [
        [
            "x0_MakeTokens…",
            module.functions.x0_MakeTokens(
                t1.name, t1.symbol, t1.decimals,
                t2.name, t2.symbol, t2.decimals,
                account.address
            )
        ],
        [
            "x1_MakePool10000…",
            module.functions.x1_MakePool10000()
        ],
        [
            "x2_Liquidity…",
            module.functions.x2_Liquidity()
        ],
        [
            "step1_MakeWhitelist…",
            module.functions.step1_MakeWhitelist()
        ],
        [
            "step2_MakeOrder…",
            module.functions.step2_MakeOrder()
        ],
        [
            "step3_SupplyOrder…",
            module.functions.step3_SupplyOrder()  # SlowOrder if use 'step3_SupplyOrderSlow' + step7
        ],
        [
            "step4_MakePosition…",
            module.functions.step4_MakePosition()
        ],
        [
            "step5_MarginSwapAll…",
            module.functions.step5_MarginSwapAll()
        ],
        [
            "step6_SwapViaPool…",
            module.functions.step6_SwapViaPool()
        ]
    ]
    for index, (step_name, fn) in enumerate(data, start=1):
        print(f"→ {index} {step_name}…")
        receipt = await send(fn)
        if receipt['status'] != 1:
            print(f"❌ Ошибка в шаге: {step_name}")
            return

    print("✅ Цикл завершён — можно проверять условие ликвидации.")


if __name__ == "__main__":
    asyncio.run(main())
