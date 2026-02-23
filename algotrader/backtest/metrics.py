"""Performance metrics for backtesting results.

Computes:
  - Total return %
  - CAGR %
  - Max drawdown %
  - Sharpe ratio (annualised, assuming 252 trading days)
  - Win rate, number of trades
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

from algotrader.signals.models import Fill


@dataclass
class PerformanceMetrics:
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    n_trades: int = 0
    win_rate_pct: float = 0.0

    @classmethod
    def compute(
        cls,
        equity_curve: List[float],
        initial_capital: float,
        fills: List[Fill],
        trading_days_per_year: int = 252,
    ) -> "PerformanceMetrics":
        if not equity_curve or initial_capital <= 0:
            return cls()

        final_equity = equity_curve[-1]
        n_bars = len(equity_curve)

        # Total return
        total_return_pct = (final_equity - initial_capital) / initial_capital * 100.0

        # CAGR — compound annual growth rate
        years = n_bars / trading_days_per_year
        if years > 0 and initial_capital > 0:
            ratio = final_equity / initial_capital
            cagr_pct = (ratio ** (1.0 / years) - 1.0) * 100.0 if ratio > 0 else -100.0
        else:
            cagr_pct = 0.0

        # Max drawdown
        max_drawdown_pct = cls._max_drawdown(equity_curve)

        # Sharpe ratio (annualised daily returns, risk-free ≈ 0)
        sharpe = cls._sharpe(equity_curve, trading_days_per_year)

        # Trade stats from fills (pair buys with sells)
        n_trades, win_rate_pct = cls._trade_stats(fills)

        return cls(
            total_return_pct=total_return_pct,
            cagr_pct=cagr_pct,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe,
            n_trades=n_trades,
            win_rate_pct=win_rate_pct,
        )

    @staticmethod
    def _max_drawdown(equity: List[float]) -> float:
        """Return max drawdown as a positive percentage."""
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _sharpe(equity: List[float], trading_days_per_year: int) -> float:
        """Annualised Sharpe ratio from equity curve."""
        if len(equity) < 2:
            return 0.0

        returns = [
            (equity[i] - equity[i - 1]) / equity[i - 1]
            for i in range(1, len(equity))
            if equity[i - 1] != 0
        ]
        if not returns:
            return 0.0

        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(variance)

        if std_r == 0:
            return 0.0

        return (mean_r / std_r) * math.sqrt(trading_days_per_year)

    @staticmethod
    def _trade_stats(fills: List[Fill]):
        """Compute number of round-trips and win rate from fill history."""
        if not fills:
            return 0, 0.0

        # Very simple: pair consecutive buy/sell fills on same symbol
        wins = 0
        losses = 0
        buy_price: dict = {}

        for fill in fills:
            from algotrader.signals.models import OrderSide
            if fill.side == OrderSide.BUY:
                buy_price[fill.symbol] = fill.price
            elif fill.side == OrderSide.SELL and fill.symbol in buy_price:
                pnl = fill.price - buy_price.pop(fill.symbol)
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1

        total = wins + losses
        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        return total, win_rate
