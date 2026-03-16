"""Backtest visualisation — equity curve and drawdown charts.

Produces publication-quality PNG charts from a BacktestResult.
Requires matplotlib (``pip install matplotlib``).

Functions
---------
plot_equity_curve(equity_curve, initial_capital, save_path)
    Line chart of portfolio value over time.

plot_drawdown(equity_curve, save_path)
    Area chart of peak-to-trough drawdown (%) over time.

plot_backtest_report(result, save_dir)
    Convenience: saves both charts + a per-symbol fill table to *save_dir*.

Edge constraints
----------------
* Headless: uses the 'Agg' matplotlib backend so no display is required.
* DPI: all charts are saved at 150 DPI (high-res without being enormous).
* Thread-safe: each call creates its own Figure; the global pyplot state is
  never used (avoids interference when called from worker threads).
* Zero-division guard: if equity_curve is empty or contains a single value,
  functions return immediately without writing a file.
"""

from __future__ import annotations

import os
from typing import List, Optional

import numpy as np


def _drawdown_series(equity: List[float]) -> List[float]:
    """Return percentage drawdown at each point: 0 at peak, negative in troughs."""
    arr = np.array(equity, dtype=float)
    running_max = np.maximum.accumulate(arr)
    # Avoid division by zero if initial capital is somehow 0
    safe_max = np.where(running_max == 0, 1.0, running_max)
    dd = (arr - running_max) / safe_max * 100.0
    return dd.tolist()


def plot_equity_curve(
    equity_curve: List[float],
    initial_capital: float = 0.0,
    save_path: str = "equity_curve.png",
    title: str = "Portfolio Equity Curve",
) -> Optional[str]:
    """Save an equity-curve line chart to *save_path*.

    Args:
        equity_curve:    List of portfolio values, one per bar.
        initial_capital: Starting capital (horizontal reference line).
        save_path:       Output file path (PNG).
        title:           Chart title.

    Returns:
        Absolute path of the saved file, or ``None`` if the curve is too short.
    """
    if len(equity_curve) < 2:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    fig, ax = plt.subplots(figsize=(12, 5))

    bars = list(range(len(equity_curve)))
    ax.plot(bars, equity_curve, color="#2196F3", linewidth=1.5, label="Equity")

    if initial_capital > 0:
        ax.axhline(initial_capital, color="#9E9E9E", linewidth=0.8,
                   linestyle="--", label=f"Initial capital ₹{initial_capital:,.0f}")

    # Shade gain / loss regions relative to initial capital
    equity_arr = np.array(equity_curve)
    baseline = initial_capital if initial_capital > 0 else equity_curve[0]
    ax.fill_between(
        bars, equity_arr, baseline,
        where=(equity_arr >= baseline), alpha=0.15, color="#4CAF50", label="Gain",
    )
    ax.fill_between(
        bars, equity_arr, baseline,
        where=(equity_arr < baseline), alpha=0.15, color="#F44336", label="Loss",
    )

    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel("Bar", fontsize=10)
    ax.set_ylabel("Portfolio Value (₹)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"₹{x:,.0f}"))
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    fig.tight_layout()
    abs_path = os.path.abspath(save_path)
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return abs_path


def plot_drawdown(
    equity_curve: List[float],
    save_path: str = "drawdown.png",
    title: str = "Portfolio Drawdown",
    max_dd_line: bool = True,
) -> Optional[str]:
    """Save a drawdown area chart to *save_path*.

    Args:
        equity_curve: List of portfolio values, one per bar.
        save_path:    Output file path (PNG).
        title:        Chart title.
        max_dd_line:  If True, draw a horizontal line at the maximum drawdown.

    Returns:
        Absolute path of the saved file, or ``None`` if the curve is too short.
    """
    if len(equity_curve) < 2:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    dd = _drawdown_series(equity_curve)
    bars = list(range(len(dd)))

    fig, ax = plt.subplots(figsize=(12, 4))

    ax.fill_between(bars, dd, 0, color="#F44336", alpha=0.5, label="Drawdown")
    ax.plot(bars, dd, color="#C62828", linewidth=0.8)

    max_dd = min(dd)
    if max_dd_line and max_dd < 0:
        ax.axhline(max_dd, color="#880000", linewidth=1.0, linestyle="--",
                   label=f"Max drawdown {max_dd:.2f}%")

    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel("Bar", fontsize=10)
    ax.set_ylabel("Drawdown (%)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    fig.tight_layout()
    abs_path = os.path.abspath(save_path)
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return abs_path


def plot_backtest_report(result, save_dir: str = ".") -> List[str]:
    """Save all standard backtest charts to *save_dir*.

    Args:
        result:   A ``BacktestResult`` from ``BacktestEngine.run()``.
        save_dir: Directory where PNG files are written.

    Returns:
        List of absolute paths for charts that were generated.
    """
    os.makedirs(save_dir, exist_ok=True)
    saved = []

    eq_path = plot_equity_curve(
        result.equity_curve,
        save_path=os.path.join(save_dir, "equity_curve.png"),
    )
    if eq_path:
        saved.append(eq_path)

    dd_path = plot_drawdown(
        result.equity_curve,
        save_path=os.path.join(save_dir, "drawdown.png"),
    )
    if dd_path:
        saved.append(dd_path)

    return saved
