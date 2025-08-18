import asyncio
from aiohttp.client_exceptions import ClientConnectorError, ClientConnectorDNSError
from web3.providers.rpc import AsyncHTTPProvider
from loguru import logger


class RetryingHTTPProvider(AsyncHTTPProvider):
    def __init__(self, endpoint_uri: str, *, request_kwargs=None):
        super().__init__(endpoint_uri, request_kwargs=request_kwargs)

    async def make_request(self, method: str, params: list):
        retry_delay = 5
        while True:
            try:
                return await super().make_request(method, params)
            except (ClientConnectorDNSError, ClientConnectorError) as e:
                logger.warning(
                    f"[RPC retry] {method} → {self.endpoint_uri}: {e}. "
                    f"Retry after {retry_delay}s"
                )
            except Exception as e:
                logger.warning(
                    f"[RPC error] {method} → {self.endpoint_uri}: {e}. "
                    f"Retry after {retry_delay}s"
                )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
