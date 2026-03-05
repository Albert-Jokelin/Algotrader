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

    # ── Execution realism (all default to 0 / disabled for backward-compat) ───
    # Market-order slippage in basis points (1 bps = 0.01%).
    # Buys fill higher, sells fill lower by this amount.
    slippage_bps: float = 0.0
    # Flat brokerage per trade in INR (e.g. 20.0 for Zerodha/Upstox flat fee).
    commission_flat: float = 0.0
    # Percentage brokerage as a fraction of turnover (e.g. 0.0003 = 0.03%).
    # Actual brokerage = min(commission_flat, commission_pct * turnover).
    commission_pct: float = 0.0
    # Maximum fill quantity as a fraction of the bar's traded volume.
    # 0 = disabled; 0.1 = can fill at most 10 % of bar volume.
    volume_cap_pct: float = 0.0

    # ── Automated risk stops (None = disabled) ─────────────────────────────────
    # Trailing stop-loss as a fraction of price (e.g. 0.02 = 2% trail).
    trailing_sl_pct: Optional[float] = None
    # Per-trade maximum loss as a fraction of position cost basis.
    # If unrealised loss exceeds this, the position is force-closed.
    max_loss_per_trade_pct: Optional[float] = None

    # ── Intraday (MIS) product settings ────────────────────────────────────────
    # Leverage multiplier for MIS (Margin Intraday Square-off) orders.
    # e.g. 5.0 means only 20% capital is consumed per MIS position.
    # 1.0 = no leverage (treat MIS like NRML for margin purposes).
    mis_leverage: float = 1.0
    # HH:MM (IST) at which all MIS positions are forcibly squared off.
    # Set to "" or None to disable auto square-off in backtests.
    squareoff_time: str = "15:15"


@dataclass
class AlertConfig:
    """Notification backend credentials and event toggles."""
    # Telegram Bot API
    telegram_token: str = ""
    telegram_chat_id: str = ""
    # SMTP email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""             # Comma-separated recipients
    # Per-event toggles
    on_fill: bool = True
    on_signal: bool = False
    on_daily_loss: bool = True
    on_stale_data: bool = True
    on_startup: bool = True
    on_shutdown: bool = True
    # Minimum seconds between alerts of the same type (0 = unlimited)
    cooldown_secs: float = 60.0


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
    alerts: AlertConfig = field(default_factory=AlertConfig)

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
