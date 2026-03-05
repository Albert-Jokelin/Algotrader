"""Command-line interface for the algo trading system.

Usage
-----
  # Run a backtest
  python -m algotrader backtest  --script strategies/ema_cross.pine \\
                                  --symbol RELIANCE --exchange NSE \\
                                  --from 2023-01-01 --to 2024-01-01 \\
                                  --capital 500000

  # Live trading (paper or Upstox)
  python -m algotrader live      --script strategies/ema_cross.pine \\
                                  --symbol RELIANCE --exchange NSE \\
                                  --broker paper --interval 1 [--dry-run]

  # Authenticate with Upstox (runs the OAuth flow)
  python -m algotrader login

  # Parse a Pine Script and display its AST (debugging)
  python -m algotrader run-script --script strategies/ema_cross.pine

All paths are relative to the current working directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path


# ── Formatters ─────────────────────────────────────────────────────────────────

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
    print(f"  Total charges  : ₹{result.total_charges:.2f}")
    print("──────────────────────────────────────────────────────────────\n")


# ── Commands ───────────────────────────────────────────────────────────────────

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
    to_date   = date.fromisoformat(args.to_date)   if args.to_date   else date.today()

    print(
        f"Fetching historical data: {args.exchange}:{args.symbol} "
        f"{from_date} → {to_date}"
    )
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


def cmd_live(args) -> int:
    """Start the live trading event loop."""
    import logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from algotrader.live.event_loop import LiveEventLoop
    from algotrader.signals.models import Exchange

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: script not found: {args.script}", file=sys.stderr)
        return 1

    pine_src = script_path.read_text()
    exchange  = Exchange(args.exchange)

    # ── Build broker ────────────────────────────────────────────────────────
    broker_mode = args.broker.lower()
    if broker_mode == "paper":
        from algotrader.broker.paper_broker import PaperBroker
        broker = PaperBroker(initial_capital=args.capital)
    elif broker_mode == "upstox":
        from algotrader.auth.oauth import UpstoxOAuth
        from algotrader.broker.upstox_broker import UpstoxBroker
        from algotrader.config import UpstoxConfig

        client_id     = os.environ.get("UPSTOX_CLIENT_ID", "")
        client_secret = os.environ.get("UPSTOX_CLIENT_SECRET", "")
        redirect_uri  = os.environ.get("UPSTOX_REDIRECT_URI", "https://localhost/")
        sandbox       = os.environ.get("UPSTOX_SANDBOX", "true").lower() == "true"

        oauth = UpstoxOAuth(client_id=client_id, client_secret=client_secret,
                            redirect_uri=redirect_uri)
        access_token = (
            oauth.access_token_from_file()
            or os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        )
        if not access_token:
            print(
                "No Upstox access token found.\n"
                "Run:  python -m algotrader login\n"
                "Or set the UPSTOX_ACCESS_TOKEN environment variable.",
                file=sys.stderr,
            )
            return 1

        cfg = UpstoxConfig(
            client_id=client_id, client_secret=client_secret,
            redirect_uri=redirect_uri, access_token=access_token,
            sandbox=sandbox,
        )
        broker = UpstoxBroker(config=cfg)
    else:
        print(
            f"Error: unknown broker '{args.broker}'. Use 'paper' or 'upstox'.",
            file=sys.stderr,
        )
        return 1

    # ── Build live feed (Upstox only; paper mode needs no feed) ─────────────
    live_feed = None
    if broker_mode == "upstox":
        from algotrader.data.live_feed import LiveDataFeed
        live_feed = LiveDataFeed(access_token=access_token, sandbox=sandbox)

    # ── Start loop ───────────────────────────────────────────────────────────
    loop = LiveEventLoop(
        pine_script=pine_src,
        symbol=args.symbol,
        exchange=exchange,
        broker=broker,
        live_feed=live_feed,
        interval_mins=args.interval,
        dry_run=args.dry_run,
    )
    loop.run()
    return 0


def cmd_login(args) -> int:
    """Run the Upstox OAuth flow and save the access token."""
    from algotrader.auth.oauth import OAuthError, UpstoxOAuth

    client_id     = args.client_id     or os.environ.get("UPSTOX_CLIENT_ID", "")
    client_secret = args.client_secret or os.environ.get("UPSTOX_CLIENT_SECRET", "")
    redirect_uri  = args.redirect_uri  or os.environ.get(
        "UPSTOX_REDIRECT_URI", "http://localhost:8765/callback"
    )

    if not client_id or not client_secret:
        print(
            "Error: Upstox client_id and client_secret are required.\n"
            "  --client-id / --client-secret, or set "
            "UPSTOX_CLIENT_ID / UPSTOX_CLIENT_SECRET.",
            file=sys.stderr,
        )
        return 1

    oauth = UpstoxOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    try:
        oauth.login()
    except OAuthError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
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


# ── Argument parser ────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="algotrader",
        description="Algorithmic trading system for Indian stock markets",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ── backtest ──────────────────────────────────────────────────────────────
    bt = subparsers.add_parser("backtest", help="Run a Pine Script strategy backtest")
    bt.add_argument("--script",   required=True)
    bt.add_argument("--symbol",   required=True)
    bt.add_argument("--exchange", default="NSE")
    bt.add_argument("--from",     dest="from_date")
    bt.add_argument("--to",       dest="to_date")
    bt.add_argument("--capital",  type=float, default=1_000_000)

    # ── live ──────────────────────────────────────────────────────────────────
    lv = subparsers.add_parser("live", help="Run a strategy live")
    lv.add_argument("--script",   required=True)
    lv.add_argument("--symbol",   required=True)
    lv.add_argument("--exchange", default="NSE")
    lv.add_argument("--broker",   default="paper", help="paper|upstox")
    lv.add_argument("--capital",  type=float, default=1_000_000)
    lv.add_argument("--interval", type=int,   default=1, help="Bar interval (minutes)")
    lv.add_argument("--dry-run",  action="store_true")
    lv.add_argument("--verbose",  action="store_true")

    # ── login ─────────────────────────────────────────────────────────────────
    lg = subparsers.add_parser("login", help="Authenticate with Upstox (OAuth)")
    lg.add_argument("--client-id",     dest="client_id",     default="")
    lg.add_argument("--client-secret", dest="client_secret", default="")
    lg.add_argument("--redirect-uri",  dest="redirect_uri",  default="")

    # ── run-script ────────────────────────────────────────────────────────────
    rs = subparsers.add_parser("run-script", help="Parse a Pine Script and display its AST")
    rs.add_argument("--script", required=True)

    args = parser.parse_args(argv)

    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "live":
        return cmd_live(args)
    if args.command == "login":
        return cmd_login(args)
    if args.command == "run-script":
        return cmd_run_script(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
