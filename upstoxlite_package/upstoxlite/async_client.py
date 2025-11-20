import aiohttp
import asyncio
from typing import Optional, Dict, Any
from .config import UpstoxConfig, TOKEN_ENDPOINT
from .types import TokenResponse, Profile, OrderResponse, Quote

def aio_retry(max_attempts=4):
    def deco(fn):
        async def wrapper(*args, **kwargs):
            attempts = 0
            wait = 0.5
            while True:
                try:
                    return await fn(*args, **kwargs)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    attempts += 1
                    if attempts >= max_attempts:
                        raise
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 10)
        return wrapper
    return deco

class UpstoxAsyncClient:
    def __init__(self, cfg: UpstoxConfig, access_token: Optional[str] = None, timeout: int = 15):
        self.cfg = cfg
        self.timeout = timeout
        self.access_token = access_token
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_get(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _headers(self, extra: Dict[str, str] = None):
        h = {"Accept": "application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if extra:
            h.update(extra)
        return h

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return self.cfg.base_url.rstrip("/") + "/" + path.lstrip("/")

    @aio_retry()
    async def exchange_code_for_token(self, code: str) -> TokenResponse:
        s = await self._session_get()
        data = {
            "code": code,
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "redirect_uri": self.cfg.redirect_uri,
            "grant_type": "authorization_code"
        }
        async with s.post(TOKEN_ENDPOINT, data=data, timeout=self.timeout) as resp:
            resp.raise_for_status()
            obj = await resp.json()
            tr = TokenResponse(**obj)
            self.access_token = tr.access_token
            return tr

    @aio_retry()
    async def get_profile(self) -> Profile:
        s = await self._session_get()
        async with s.get(self._url("/user/profile"), headers=self._headers(), timeout=self.timeout) as resp:
            resp.raise_for_status()
            return Profile(**(await resp.json()))

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
