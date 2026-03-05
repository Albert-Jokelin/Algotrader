"""TradingView webhook receiver (FastAPI).

Accepts HTTP POST alerts from TradingView and converts them to TradingSignal
objects that are forwarded to the configured signal handler.

Webhook payload schema (TradingView → this server)
---------------------------------------------------
{
  "symbol":     "RELIANCE",           // required
  "exchange":   "NSE",                // required (NSE | BSE | NFO | MCX)
  "action":     "BUY",                // required (BUY | SELL | EXIT)
  "strategy":   "ma_crossover",       // optional (defaults to "webhook")
  "price":      null,                 // optional — null means market order
  "stop_loss":  null,                 // optional
  "take_profit": null,                // optional
  "quantity":   null                  // optional — null means risk-sized
}

Endpoints
---------
POST /webhook               Receive a TradingView alert → emit TradingSignal
GET  /health                Liveness probe → {"status": "ok", "signals": N}

Security
--------
If WebhookConfig.secret is set, every request must carry the header:
    X-Webhook-Secret: <secret>
Requests with missing or wrong secrets receive HTTP 403.

Edge constraints
----------------
* Duplicate suppression: signals with the same (symbol, action, strategy)
  within dedup_window_seconds are silently dropped (returns 200 with
  {"status": "duplicate"}).
* Max body size: requests larger than WebhookConfig.max_body_size bytes are
  rejected with HTTP 413.
* Unknown exchange or action values → HTTP 422 with a clear error message.
* Signal handler errors do not propagate to the caller (returns 200 with
  {"status": "handler_error"} and logs the exception).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from algotrader.config import WebhookConfig
from algotrader.signals.models import (
    Exchange,
    SignalAction,
    SignalSource,
    TradingSignal,
)

log = logging.getLogger(__name__)


# ── Pydantic request model ─────────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    symbol: str
    exchange: str
    action: str
    strategy: str = "webhook"
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    quantity: Optional[int] = None

    @field_validator("exchange")
    @classmethod
    def validate_exchange(cls, v: str) -> str:
        valid = {e.value for e in Exchange}
        up = v.upper()
        if up not in valid:
            raise ValueError(
                f"Unknown exchange {v!r}. Valid values: {sorted(valid)}"
            )
        return up

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        valid = {a.value for a in SignalAction}
        up = v.upper()
        # Support "EXIT_LONG" / "EXIT_SHORT" → "EXIT" normalisation
        if up == "CLOSE":
            up = "EXIT"
        if up not in valid:
            raise ValueError(
                f"Unknown action {v!r}. Valid values: {sorted(valid)}"
            )
        return up


# ── Server factory ─────────────────────────────────────────────────────────────

def create_app(
    config: WebhookConfig,
    signal_handler: Optional[Callable[[TradingSignal], Any]] = None,
    dedup_window_secs: int = 60,
) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        config: WebhookConfig with host, port, secret, max_body_size.
        signal_handler: Optional callback called with each new TradingSignal.
            If None, signals are logged but not forwarded anywhere.
        dedup_window_secs: Suppress duplicate (symbol, action, strategy)
            signals within this window (seconds).
    """
    app = FastAPI(title="AlgoTrader Webhook", version="1.0.0", docs_url=None)

    # Mutable state (lives inside the app instance).
    state: Dict[str, Any] = {
        "signal_count": 0,
        # Maps dedup_key → last-accepted epoch.
        "dedup_cache": {},
    }

    # ── Body-size guard ────────────────────────────────────────────────────────

    @app.middleware("http")
    async def enforce_max_body_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > config.max_body_size:
            return JSONResponse(
                status_code=413,
                content={"error": "Request body too large"},
            )
        return await call_next(request)

    # ── Health check ───────────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "ok", "signals_received": state["signal_count"]}

    # ── Webhook receiver ───────────────────────────────────────────────────────

    @app.post("/webhook")
    async def receive_webhook(
        payload: WebhookPayload,
        x_webhook_secret: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        # Secret verification
        if config.secret:
            if x_webhook_secret != config.secret:
                log.warning(
                    "Webhook rejected: bad secret from %s:%s",
                    payload.symbol, payload.exchange,
                )
                raise HTTPException(status_code=403, detail="Invalid webhook secret")

        # Deduplication
        dedup_key = f"{payload.symbol}:{payload.exchange}:{payload.action}:{payload.strategy}"
        now = time.time()
        last = state["dedup_cache"].get(dedup_key, 0.0)
        if now - last < dedup_window_secs:
            log.debug("Duplicate webhook suppressed: %s", dedup_key)
            return {"status": "duplicate", "key": dedup_key}
        state["dedup_cache"][dedup_key] = now

        # Build TradingSignal
        signal = TradingSignal(
            source=SignalSource.MANUAL,
            symbol=payload.symbol,
            exchange=Exchange(payload.exchange),
            action=SignalAction(payload.action),
            strategy_name=payload.strategy,
            price=payload.price,
            stop_loss=payload.stop_loss,
            take_profit=payload.take_profit,
            quantity=payload.quantity,
        )
        state["signal_count"] += 1
        log.info(
            "Webhook signal: %s %s:%s [%s]",
            signal.action.value, signal.exchange.value,
            signal.symbol, signal.strategy_name,
        )

        # Forward to handler
        if signal_handler is not None:
            try:
                signal_handler(signal)
            except Exception as exc:
                log.exception("Signal handler error: %s", exc)
                return {"status": "handler_error", "signal_id": signal.signal_id}

        return {"status": "accepted", "signal_id": signal.signal_id}

    return app


def run_server(
    config: WebhookConfig,
    signal_handler: Optional[Callable[[TradingSignal], Any]] = None,
    dedup_window_secs: int = 60,
) -> None:
    """Start the uvicorn server (blocking call)."""
    import uvicorn

    app = create_app(config, signal_handler, dedup_window_secs)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )
