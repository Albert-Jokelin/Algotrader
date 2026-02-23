"""System-wide configuration for the algo trading system.

Reads from environment variables / .env file.
All monetary values are in INR.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskConfig:
    """Risk management parameters."""
    # Maximum capital allocated to a single instrument (fraction of total capital).
    max_position_pct: float = 0.05          # 5 %
    # Maximum total long + short exposure across all positions.
    max_portfolio_exposure_pct: float = 0.80  # 80 %
    # Stop trading for the day if realised PnL drops below this fraction.
    max_daily_loss_pct: float = 0.02         # 2 %
    # Default stop-loss distance as fraction of entry price (used when signal provides none).
    default_stop_loss_pct: float = 0.02      # 2 %
    # Default take-profit distance as fraction of entry price.
    default_take_profit_pct: float = 0.04    # 4 %


@dataclass
class WebhookConfig:
    """TradingView webhook server settings."""
    host: str = "0.0.0.0"
    port: int = 8000
    # Optional shared secret sent in X-Webhook-Secret header from TradingView.
    secret: Optional[str] = None
    # Maximum request body size in bytes.
    max_body_size: int = 64 * 1024  # 64 KB


@dataclass
class UpstoxConfig:
    """Upstox broker API credentials."""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "https://localhost/"
    access_token: str = ""
    sandbox: bool = True

    @property
    def base_url(self) -> str:
        if self.sandbox:
            return "https://api-hft.upstox.com/v2"
        return "https://api.upstox.com/v2"


@dataclass
class Settings:
    """Top-level application settings."""
    # Starting capital for paper trading / backtest (INR).
    initial_capital: float = 1_000_000.0   # ₹10 lakh

    risk: RiskConfig = field(default_factory=RiskConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    upstox: UpstoxConfig = field(default_factory=UpstoxConfig)

    # Signal deduplication window in seconds.
    dedup_window_seconds: int = 60

    # Broker mode: "paper" | "upstox"
    broker_mode: str = "paper"

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables."""
        risk = RiskConfig(
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.05")),
            max_portfolio_exposure_pct=float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.80")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02")),
            default_stop_loss_pct=float(os.getenv("DEFAULT_STOP_LOSS_PCT", "0.02")),
            default_take_profit_pct=float(os.getenv("DEFAULT_TAKE_PROFIT_PCT", "0.04")),
        )
        webhook = WebhookConfig(
            host=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
            port=int(os.getenv("WEBHOOK_PORT", "8000")),
            secret=os.getenv("WEBHOOK_SECRET"),
        )
        upstox = UpstoxConfig(
            client_id=os.getenv("UPSTOX_CLIENT_ID", ""),
            client_secret=os.getenv("UPSTOX_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("UPSTOX_REDIRECT_URI", "https://localhost/"),
            access_token=os.getenv("UPSTOX_ACCESS_TOKEN", ""),
            sandbox=os.getenv("UPSTOX_SANDBOX", "true").lower() == "true",
        )
        return cls(
            initial_capital=float(os.getenv("INITIAL_CAPITAL", "1000000")),
            risk=risk,
            webhook=webhook,
            upstox=upstox,
            dedup_window_seconds=int(os.getenv("DEDUP_WINDOW_SECONDS", "60")),
            broker_mode=os.getenv("BROKER_MODE", "paper"),
        )


# Module-level default (can be replaced in tests or main entry point).
settings = Settings()
