from dataclasses import dataclass
from typing import Optional

PROD_BASE = "https://api.upstox.com/v2/"
SANDBOX_BASE = "https://api-sandbox.upstox.com/v2/"
TOKEN_ENDPOINT = "https://api.upstox.com/v2/login/authorization/token"
AUTHORIZE_URL = "https://api.upstox.com/authorize"
WS_AUTHORIZE = "/market-data-feed-authorize-v3"

@dataclass
class UpstoxConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    sandbox: bool = True
    base_url: Optional[str] = None
    notifier_webhook: Optional[str] = None

    @property
    def api_base(self) -> str:
        if self.base_url:
            return self.base_url
        return SANDBOX_BASE if self.sandbox else PROD_BASE
