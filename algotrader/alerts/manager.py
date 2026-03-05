"""Alert manager for the algo trading system.

Dispatches notifications to configured backends (Telegram, email) when
significant trading events occur:

Events
------
* ``fill``        — an order was filled (live trading only)
* ``signal``      — a new trading signal was generated
* ``daily_loss``  — daily loss limit reached
* ``stale_data``  — live data feed went stale
* ``startup``     — bot started
* ``shutdown``    — bot stopped (graceful or forced)

Edge constraints
----------------
* Missing credentials → backend silently skipped, never raises.
* Network / SMTP failures → logged at WARNING, not propagated.
* Rate-limit guard: at most one alert per (event_type, cooldown_secs) period
  (default 60 s) to prevent alert storms during volatile sessions.
* Thread-safe: can be called from background polling threads.
"""

from __future__ import annotations

import logging
import smtplib
import threading
import time
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    """All alert-backend credentials and per-event toggles."""

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_token: str = ""       # Bot token from @BotFather
    telegram_chat_id: str = ""     # Target chat / group ID

    # ── Email (SMTP) ──────────────────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""             # Comma-separated recipients

    # ── Per-event toggles (True = send alert) ─────────────────────────────────
    on_fill: bool = True
    on_signal: bool = False        # Off by default — can be noisy
    on_daily_loss: bool = True
    on_stale_data: bool = True
    on_startup: bool = True
    on_shutdown: bool = True

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Minimum seconds between alerts of the same type (0 = unlimited).
    cooldown_secs: float = 60.0


class AlertManager:
    """Dispatches trading-event notifications to configured backends.

    Usage::

        cfg = AlertConfig(telegram_token="...", telegram_chat_id="...")
        alerts = AlertManager(cfg)
        alerts.send("fill", "Bought 50 RELIANCE @ 2450.00")
    """

    def __init__(self, config: AlertConfig) -> None:
        self._cfg = config
        self._lock = threading.Lock()
        # Maps event_type → last-sent epoch timestamp.
        self._last_sent: Dict[str, float] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def send(self, event_type: str, message: str) -> None:
        """Send *message* to all configured backends for *event_type*.

        Silently no-ops if the event toggle is off or the cooldown window has
        not elapsed since the last alert of this type.

        Args:
            event_type: One of ``fill | signal | daily_loss | stale_data |
                        startup | shutdown`` (or any custom string).
            message: Human-readable alert body.
        """
        if not self._should_send(event_type):
            return

        prefix = f"[AlgoTrader] [{event_type.upper()}]"
        full_msg = f"{prefix} {message}"

        self._send_telegram(full_msg)
        self._send_email(event_type, full_msg)

        with self._lock:
            self._last_sent[event_type] = time.monotonic()

    # ── Convenience helpers ────────────────────────────────────────────────────

    def on_fill(self, symbol: str, side: str, qty: int, price: float) -> None:
        """Send a fill notification."""
        self.send(
            "fill",
            f"{side} {qty} {symbol} @ {price:.2f}",
        )

    def on_signal(self, symbol: str, action: str, strategy: str) -> None:
        """Send a signal notification."""
        self.send("signal", f"{action} {symbol} [{strategy}]")

    def on_daily_loss(self, loss_pct: float, capital: float) -> None:
        """Send a daily-loss-limit-reached notification."""
        self.send(
            "daily_loss",
            f"Daily loss limit hit: {loss_pct:.1%} of capital. "
            f"Capital remaining: ₹{capital:,.0f}. Trading halted.",
        )

    def on_stale_data(self, symbol: str, seconds_stale: float) -> None:
        """Send a stale-data warning."""
        self.send(
            "stale_data",
            f"No new data for {symbol} in {seconds_stale:.0f}s. Check connection.",
        )

    def on_startup(self, symbol: str, strategy: str, broker: str) -> None:
        """Send bot-startup notification."""
        self.send("startup", f"Bot started | {symbol} | {strategy} | broker={broker}")

    def on_shutdown(self, reason: str = "user request") -> None:
        """Send bot-shutdown notification."""
        self.send("shutdown", f"Bot stopped | reason={reason}")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _should_send(self, event_type: str) -> bool:
        """Return True if this event should be dispatched right now."""
        toggle_map = {
            "fill":       self._cfg.on_fill,
            "signal":     self._cfg.on_signal,
            "daily_loss": self._cfg.on_daily_loss,
            "stale_data": self._cfg.on_stale_data,
            "startup":    self._cfg.on_startup,
            "shutdown":   self._cfg.on_shutdown,
        }
        if not toggle_map.get(event_type, True):
            return False

        if self._cfg.cooldown_secs > 0:
            with self._lock:
                # float("-inf") ensures the first alert always passes through
                last = self._last_sent.get(event_type, float("-inf"))
            if time.monotonic() - last < self._cfg.cooldown_secs:
                log.debug(
                    "Alert '%s' suppressed (cooldown %.0fs not elapsed)",
                    event_type, self._cfg.cooldown_secs,
                )
                return False

        return True

    def _send_telegram(self, message: str) -> None:
        """POST message to the Telegram Bot API."""
        token = self._cfg.telegram_token
        chat_id = self._cfg.telegram_chat_id
        if not token or not chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": message},
                timeout=10,
            )
            if not resp.ok:
                log.warning(
                    "Telegram alert failed: %d %s", resp.status_code, resp.text
                )
        except Exception as exc:
            log.warning("Telegram alert error: %s", exc)

    def _send_email(self, subject_suffix: str, body: str) -> None:
        """Send an email via SMTP (TLS/STARTTLS)."""
        user = self._cfg.smtp_user
        password = self._cfg.smtp_password
        to = self._cfg.email_to
        if not user or not password or not to:
            return
        from_addr = self._cfg.email_from or user
        recipients = [r.strip() for r in to.split(",") if r.strip()]
        if not recipients:
            return

        msg = MIMEText(body)
        msg["Subject"] = f"[AlgoTrader] {subject_suffix}"
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)

        try:
            with smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(user, password)
                smtp.sendmail(from_addr, recipients, msg.as_string())
        except Exception as exc:
            log.warning("Email alert error: %s", exc)
