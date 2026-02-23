"""Command-line interface for the algo trading system.

Usage:
  python -m algotrader backtest  --script strategies/ema_cross.pine \\
                                  --symbol RELIANCE --exchange NSE \\
                                  --from 2023-01-01 --to 2024-01-01 \\
                                  --capital 500000

  python -m algotrader live      --script strategies/ema_cross.pine \\
                                  --symbol RELIANCE --exchange NSE \\
                                  --broker paper

  python -m algotrader run-script --script strategies/ema_cross.pine

All paths are relative to the current working directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path


def _print_result(result) -> None:
    m = result.metrics
    print("\n── Backtest Results ──────────────────────────────────────────")
    print(f"  Total return   : {m.total_return_pct:+.2f}%")
    print(f"  CAGR           : {m.cagr_pct:+.2f}%")
    print(f"  Max drawdown   : {m.max_drawdown_pct:.2f}%")
    print(f"  Sharpe ratio   : {m.sharpe_ratio:.2f}")
    print(f"  Trades         : {m.n_trades}")
    print(f"  Win rate       : {m.win_rate_pct:.1f}%")
    print(f"  Signals fired  : {result.n_signals}")
    print(f"  Fills          : {len(result.fills)}")
    print("──────────────────────────────────────────────────────────────\n")


def cmd_backtest(args) -> int:
    from algotrader.backtest.engine import BacktestEngine
    from algotrader.data.historical import HistoricalDataService
    from algotrader.signals.models import Exchange

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: script not found: {args.script}", file=sys.stderr)
        return 1

    pine_src = script_path.read_text()

    access_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
    from_date = date.fromisoformat(args.from_date) if args.from_date else date(2022, 1, 1)
    to_date = date.fromisoformat(args.to_date) if args.to_date else date.today()

    print(f"Fetching historical data: {args.exchange}:{args.symbol} "
          f"{from_date} → {to_date}")

    svc = HistoricalDataService(access_token=access_token)

    try:
        bars = svc.fetch(
            symbol=args.symbol,
            exchange=args.exchange,
            from_date=from_date,
            to_date=to_date,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Loaded {len(bars['close'])} bars. Running backtest…")

    engine = BacktestEngine(
        bars=bars,
        symbol=args.symbol,
        exchange=Exchange(args.exchange),
        initial_capital=args.capital,
    )
    result = engine.run(pine_src)
    _print_result(result)
    return 0


def cmd_run_script(args) -> int:
    """Parse and display the AST of a Pine Script file (for debugging)."""
    from algotrader.pine.lexer import Lexer
    from algotrader.pine.parser import Parser

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: script not found: {args.script}", file=sys.stderr)
        return 1

    pine_src = script_path.read_text()
    tokens = Lexer(pine_src).tokenize()
    ast = Parser(tokens).parse()

    print(f"Script: {args.script}")
    print(f"Parsed {len(ast.body)} top-level statements.")
    for stmt in ast.body:
        print(f"  {type(stmt).__name__}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="algotrader",
        description="Algo trading system for Indian stock markets",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ── backtest ──────────────────────────────────────────────────────────────
    bt = subparsers.add_parser("backtest", help="Run a Pine Script strategy backtest")
    bt.add_argument("--script",   required=True, help="Path to .pine file")
    bt.add_argument("--symbol",   required=True, help="e.g. RELIANCE")
    bt.add_argument("--exchange", default="NSE",  help="NSE|BSE|NFO")
    bt.add_argument("--from",     dest="from_date", help="Start date YYYY-MM-DD")
    bt.add_argument("--to",       dest="to_date",   help="End date YYYY-MM-DD")
    bt.add_argument("--capital",  type=float, default=1_000_000, help="Starting capital (INR)")

    # ── run-script ────────────────────────────────────────────────────────────
    rs = subparsers.add_parser("run-script", help="Parse a Pine Script and display its AST")
    rs.add_argument("--script", required=True, help="Path to .pine file")

    args = parser.parse_args(argv)

    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "run-script":
        return cmd_run_script(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
