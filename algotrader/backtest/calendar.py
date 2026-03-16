"""Calendar / monthly P&L analysis for backtests.

Builds a monthly P&L breakdown from a list of Fill objects and optionally
renders a heatmap.  Requires matplotlib (``pip install matplotlib``).

Functions
---------
monthly_pnl(fills) -> Dict[(year, month), float]
    Aggregate realised P&L per calendar month.

plot_calendar_heatmap(fills, save_path)
    Save a month × year heatmap of P&L to a PNG.

plot_daily_pnl(fills, save_path)
    Save a bar chart of daily P&L.

daily_stats(fills) -> pandas.DataFrame
    Return a DataFrame indexed by date with columns: pnl, trades, win_rate.

Edge constraints
----------------
* Fill timestamps may be timezone-aware or naive (treated as IST).
* If fills is empty the functions return immediately / return empty structures.
* matplotlib 'Agg' backend — no display required.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from algotrader.signals.models import Fill, OrderSide


# ── Analytics ─────────────────────────────────────────────────────────────────

def _fill_date(fill: Fill) -> date:
    ts = fill.timestamp
    if isinstance(ts, datetime):
        return ts.date()
    return ts  # already a date


def daily_pnl(fills: List[Fill]) -> Dict[date, float]:
    """Compute net daily P&L from a list of fills.

    Uses a simplified FIFO approach: each SELL fill realises a P&L equal to
    (sell_price - avg_buy_price) * qty — charges.  For simplicity here we
    just aggregate: sell value − buy value − total charges per day.

    Returns:
        Dict mapping date → net P&L (INR).
    """
    by_date: Dict[date, float] = defaultdict(float)
    for f in fills:
        d = _fill_date(f)
        if f.side == OrderSide.SELL:
            by_date[d] += f.value - f.charges
        else:
            by_date[d] -= f.value + f.charges
    return dict(by_date)


def monthly_pnl(fills: List[Fill]) -> Dict[Tuple[int, int], float]:
    """Aggregate daily P&L into monthly buckets.

    Returns:
        Dict mapping (year, month) → net P&L (INR).
    """
    dpnl = daily_pnl(fills)
    by_month: Dict[Tuple[int, int], float] = defaultdict(float)
    for d, pnl in dpnl.items():
        by_month[(d.year, d.month)] += pnl
    return dict(by_month)


# ── Visualisation ─────────────────────────────────────────────────────────────

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def plot_calendar_heatmap(
    fills: List[Fill],
    save_path: str = "calendar_heatmap.png",
    title: str = "Monthly P&L Heatmap (₹)",
) -> Optional[str]:
    """Save a month × year heatmap of P&L to *save_path*.

    Green = profitable month, red = loss-making month.
    Cells without data are left white/grey.

    Returns:
        Absolute path of the saved file, or ``None`` if fills is empty.
    """
    if not fills:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    mpnl = monthly_pnl(fills)
    if not mpnl:
        return None

    years = sorted({y for y, m in mpnl})
    n_years = len(years)
    year_idx = {y: i for i, y in enumerate(years)}

    # Matrix: rows = months (0-11), cols = years
    matrix = np.full((12, n_years), np.nan)
    for (y, m), pnl in mpnl.items():
        matrix[m - 1, year_idx[y]] = pnl

    # Colour scale centred at zero
    vmax = np.nanmax(np.abs(matrix)) if not np.all(np.isnan(matrix)) else 1.0
    vmax = max(vmax, 1.0)

    fig, ax = plt.subplots(figsize=(max(6, n_years * 1.5 + 2), 6))
    cmap = plt.cm.RdYlGn  # type: ignore[attr-defined]
    im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=-vmax, vmax=vmax)

    # Annotate cells
    for mi in range(12):
        for yi in range(n_years):
            val = matrix[mi, yi]
            if not np.isnan(val):
                text_color = "black" if abs(val) < vmax * 0.5 else "white"
                ax.text(
                    yi, mi,
                    f"₹{val:,.0f}" if abs(val) < 100_000 else f"₹{val/1000:.0f}K",
                    ha="center", va="center", fontsize=7, color=text_color,
                )

    ax.set_xticks(range(n_years))
    ax.set_xticklabels([str(y) for y in years])
    ax.set_yticks(range(12))
    ax.set_yticklabels(_MONTH_ABBR)
    ax.set_title(title, fontsize=13, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.04)
    cbar.set_label("P&L (₹)", fontsize=9)
    cbar.formatter = mticker.FuncFormatter(lambda x, _: f"₹{x:,.0f}")
    cbar.update_ticks()

    fig.tight_layout()
    abs_path = os.path.abspath(save_path)
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return abs_path


def plot_daily_pnl(
    fills: List[Fill],
    save_path: str = "daily_pnl.png",
    title: str = "Daily P&L",
) -> Optional[str]:
    """Save a bar chart of daily P&L to *save_path*.

    Returns:
        Absolute path of the saved file, or ``None`` if fills is empty.
    """
    if not fills:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    dpnl = daily_pnl(fills)
    if not dpnl:
        return None

    dates = sorted(dpnl.keys())
    pnls = [dpnl[d] for d in dates]
    colors = ["#4CAF50" if p >= 0 else "#F44336" for p in pnls]
    labels = [d.strftime("%d-%b") for d in dates]

    fig, ax = plt.subplots(figsize=(max(8, len(dates) * 0.5 + 2), 4))
    ax.bar(range(len(dates)), pnls, color=colors, edgecolor="white", linewidth=0.3)
    ax.axhline(0, color="black", linewidth=0.8)

    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_ylabel("P&L (₹)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"₹{x:,.0f}"))
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    fig.tight_layout()
    abs_path = os.path.abspath(save_path)
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return abs_path
