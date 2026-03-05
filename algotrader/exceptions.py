"""Custom exception hierarchy for the algo trading system."""


class AlgoTraderError(Exception):
    """Base exception for all algo trader errors."""


# ── Signal errors ─────────────────────────────────────────────────────────────

class SignalError(AlgoTraderError):
    """Base for signal-related errors."""


class InvalidSignalError(SignalError):
    """Raised when a trading signal is malformed or missing required fields."""


class DuplicateSignalError(SignalError):
    """Raised when the same signal is received more than once within the dedup window."""


class SignalFilteredError(SignalError):
    """Raised when a signal is deliberately filtered out (market closed, regime, etc.)."""


# ── Risk errors ───────────────────────────────────────────────────────────────

class RiskError(AlgoTraderError):
    """Base for risk management errors."""


class PositionLimitExceededError(RiskError):
    """Raised when a new order would exceed the per-instrument position limit."""


class PortfolioExposureExceededError(RiskError):
    """Raised when a new order would exceed total portfolio exposure."""


class DailyLossLimitExceededError(RiskError):
    """Raised when the system has hit its daily loss limit."""


# ── Order / Broker errors ─────────────────────────────────────────────────────

class BrokerError(AlgoTraderError):
    """Base for broker connectivity / execution errors."""


class OrderRejectedError(BrokerError):
    """Raised when a broker explicitly rejects an order."""


class OrderNotFoundError(BrokerError):
    """Raised when querying or cancelling an order that does not exist."""


class BrokerConnectionError(BrokerError):
    """Raised when the broker API is unreachable."""


# ── Pine Script errors ────────────────────────────────────────────────────────

class PineScriptError(AlgoTraderError):
    """Base for Pine Script interpreter errors."""


class UnsupportedFeatureError(PineScriptError):
    """Raised when a Pine Script construct is not supported by this interpreter.

    Attributes:
        feature:  Short name of the unsupported construct (e.g. "for_loop").
        line:     Source line number, or 0 if not available.
        hint:     Optional suggestion for the user.
    """

    def __init__(self, feature: str, line: int = 0, hint: str = "") -> None:
        self.feature = feature
        self.line = line
        self.hint = hint
        location = f" at line {line}" if line else ""
        detail = f" — {hint}" if hint else ""
        super().__init__(
            f"Unsupported Pine Script feature: {feature!r}{location}{detail}"
        )


# ── Backtest errors ───────────────────────────────────────────────────────────

class BacktestError(AlgoTraderError):
    """Base for backtesting errors."""


class InsufficientDataError(BacktestError):
    """Raised when there is not enough historical data to run the backtest."""


# ── Market data errors ────────────────────────────────────────────────────────

class MarketDataError(AlgoTraderError):
    """Base for market data errors."""


class InstrumentNotFoundError(MarketDataError):
    """Raised when an instrument cannot be resolved to a broker key."""
