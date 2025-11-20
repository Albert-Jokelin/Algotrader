import requests
from typing import Optional, Dict, Any
from urllib.parse import urlencode
from .config import UpstoxConfig, TOKEN_ENDPOINT
from .types import TokenResponse, Profile, OrderResponse, Quote
from .utils import retry_decorator

class UpstoxSyncClient:
    def __init__(self, cfg: UpstoxConfig, access_token: Optional[str] = None, timeout: int = 15):
        self.cfg = cfg
        self.session = requests.Session()
        self.timeout = timeout
        self.access_token = access_token

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return self.cfg.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _headers(self, extra: Dict[str, str] = None):
        h = {"Accept": "application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if extra:
            h.update(extra)
        return h

    def build_authorize_url(self, state: Optional[str] = None) -> str:
        params = {"client_id": self.cfg.client_id, "redirect_uri": self.cfg.redirect_uri}
        if state:
            params["state"] = state
        return f"{self.cfg.base_url}authorize?{urlencode(params)}"

    @retry_decorator()
    def exchange_code_for_token(self, code: str) -> TokenResponse:
        data = {
            "code": code,
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "redirect_uri": self.cfg.redirect_uri,
            "grant_type": "authorization_code",
        }
        r = self.session.post(TOKEN_ENDPOINT, data=data, timeout=self.timeout)
        r.raise_for_status()
        obj = r.json()
        tr = TokenResponse(**obj)
        self.access_token = tr.access_token
        return tr

    @retry_decorator()
    def get_profile(self) -> Profile:
        r = self.session.get(self._url("/user/profile"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return Profile(**r.json())

    @retry_decorator()
    def place_order(self, payload: Dict[str, Any]) -> OrderResponse:
        r = self.session.post(self._url("/order/place"), json=payload, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return OrderResponse(**r.json())

    @retry_decorator()
    def get_quote(self, symbol: str) -> Quote:
        r = self.session.get(self._url(f"/market/quote/{symbol}"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return Quote(**r.json())
