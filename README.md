# Algotrader — Pine Script Algo Trading for Indian Markets

A complete, locally-running algorithmic trading system for NSE/BSE that interprets TradingView Pine Script v5 strategies, backtests them against historical data, and executes orders via the Upstox API.

## Features

- **Local Pine Script interpreter** — no TradingView webhooks; runs entirely on your machine
- **Bar-by-bar backtest engine** with equity curve, Sharpe ratio, CAGR, max drawdown
- **Three broker modes**: paper (local simulation), Upstox sandbox, Upstox live
- **Full signal pipeline**: Pine Script → signal validation → risk management → order execution
- **NSE/BSE market hours** aware (09:15–15:30 IST, holiday calendar)
- **301 tests**, all passing (TDD from the ground up)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        CLI / Entry Point                      │
│              python -m algotrader backtest / run-script       │
└───────────────────────────┬──────────────────────────────────┘
                            │
              ┌─────────────▼──────────────┐
              │   Pine Script Interpreter   │
              │  Lexer → Parser → AST       │
              │  → Bar-by-bar Evaluator     │
              └─────────────┬──────────────┘
                            │  TradingSignal objects
              ┌─────────────▼──────────────┐
              │      Signal Processor       │
              │  (validation, dedup,        │
              │   market-hours filter)      │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │       Risk Manager          │
              │  (position cap, portfolio   │
              │   exposure, daily loss)     │
              └─────────────┬──────────────┘
                            │
         ┌──────────────────┼────────────────────┐
         │                  │                     │
   ┌─────▼─────┐    ┌───────▼──────┐    ┌────────▼───────┐
   │   Paper   │    │   Upstox     │    │  Upstox Live   │
   │  Broker   │    │  Sandbox     │    │  (production)  │
   └───────────┘    └──────────────┘    └────────────────┘
```

---

## Directory Structure

```
Algotrader/
├── algotrader/                  # Main Python package
│   ├── __init__.py
│   ├── __main__.py              # python -m algotrader entry point
│   ├── cli.py                   # Argument parsing & command dispatch
│   ├── config.py                # Settings dataclasses (RiskConfig, UpstoxConfig, …)
│   ├── exceptions.py            # Full custom exception hierarchy
│   ├── pine/
│   │   ├── lexer.py             # Tokeniser (regex-based)
│   │   ├── ast_nodes.py         # Typed AST node dataclasses
│   │   ├── parser.py            # Recursive-descent parser
│   │   ├── builtins.py          # ta.* indicator implementations
│   │   └── evaluator.py        # Bar-by-bar AST evaluator
│   ├── signals/
│   │   ├── models.py            # Pydantic v2 models (TradingSignal, Order, Fill, Position)
│   │   └── processor.py        # Signal validation, dedup, market-hours filter
│   ├── strategy/
│   │   └── market_hours.py     # IST-aware NSE/BSE market hours + holiday calendar
│   ├── risk/
│   │   ├── manager.py           # Risk gates: position cap, exposure, daily loss
│   │   └── position_sizer.py   # Fixed qty / % of capital / risk-based sizing
│   ├── broker/
│   │   ├── base.py              # IBroker ABC
│   │   ├── paper_broker.py      # Local simulation broker
│   │   └── upstox_broker.py    # Upstox API broker (wraps upstoxlite)
│   ├── backtest/
│   │   ├── engine.py            # Bar-by-bar backtest engine
│   │   └── metrics.py          # Performance metrics computation
│   └── data/
│       └── historical.py        # Historical data: Upstox API + CSV fallback
│
├── strategies/                  # Sample Pine Script v5 strategies
│   ├── ema_crossover.pine       # 9 EMA / 21 EMA crossover
│   ├── rsi_reversal.pine        # RSI(14) oversold/overbought
│   └── bollinger_breakout.pine  # Bollinger Band breakout
│
├── tests/
│   ├── unit/                    # 293 unit tests across all modules
│   │   ├── test_signal_models.py
│   │   ├── test_pine_lexer.py
│   │   ├── test_pine_parser.py
│   │   ├── test_pine_builtins.py
│   │   ├── test_pine_evaluator.py
│   │   ├── test_market_hours.py
│   │   ├── test_signal_processor.py
│   │   ├── test_position_sizer.py
│   │   ├── test_risk_manager.py
│   │   ├── test_paper_broker.py
│   │   ├── test_upstox_broker.py
│   │   └── test_backtest_engine.py
│   └── integration/
│       └── test_full_pipeline.py  # 8 end-to-end tests
│
├── upstoxlite_package/          # Upstox Python SDK (see its own README)
├── src/                         # C++ OMS core (legacy, see below)
├── Backtesting/                 # Original backtesting scripts
├── data_cache/                  # CSV fallback for historical data
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Quick Start

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install the local Upstox SDK
pip install -e upstoxlite_package/
```

### 2. Configure environment

```bash
export UPSTOX_ACCESS_TOKEN=your_token_here   # required for live/sandbox mode
export BROKER_MODE=paper                     # paper | sandbox | live
export INITIAL_CAPITAL=1000000
```

### 3. Run a backtest

```bash
python -m algotrader backtest \
  --script strategies/ema_crossover.pine \
  --symbol RELIANCE \
  --exchange NSE \
  --from 2024-01-01 \
  --to 2024-12-31 \
  --capital 500000
```

Example output:
```
Backtest Results — RELIANCE (NSE)
  Period       : 2024-01-01 → 2024-12-31
  Capital      : ₹500,000
  Final Equity : ₹541,230
  Total Return : +8.25%
  CAGR         : 8.25%
  Max Drawdown : -4.12%
  Sharpe Ratio : 1.34
  Trades       : 22  (Win rate: 59.1%)
```

### 4. Inspect a Pine Script (AST dump)

```bash
python -m algotrader run-script --script strategies/rsi_reversal.pine
```

### 5. Run all tests

```bash
pytest tests/ -v
# 301 tests, ~8s
```

---

## Pine Script Support

The interpreter supports a useful subset of Pine Script v5:

| Feature | Status |
|---|---|
| `var` / `varip` persistent variables | Supported |
| `if` / `else` blocks | Supported |
| `:=` reassignment | Supported |
| Series indexing `close[1]` | Supported |
| Arithmetic & comparison operators | Supported |
| `strategy()` declaration | Supported |
| `strategy.entry()` / `.exit()` / `.close()` | Supported |
| `ta.sma()` / `ta.ema()` | Supported |
| `ta.rsi()` | Supported |
| `ta.macd()` | Supported |
| `ta.bbands()` | Supported |
| `ta.atr()` | Supported |
| `ta.crossover()` / `ta.crossunder()` | Supported |
| `ta.highest()` / `ta.lowest()` | Supported |
| `ta.stdev()` | Supported |
| Built-in series: `open`, `high`, `low`, `close`, `volume` | Supported |
| `math.*` functions | Partial |
| `for` loops | Not yet |
| User-defined functions | Not yet |
| Arrays / matrices | Not yet |

---

## Broker Modes

| Mode | Description |
|---|---|
| `paper` | Local simulation; fills immediately at market price. No API calls. |
| `sandbox` | Uses Upstox Sandbox API. Simulated trades against real market data. |
| `live` | Uses Upstox Production API. Real money, real orders. Use with caution. |

Set via environment variable `BROKER_MODE` or `--broker-mode` CLI flag.

---

## Risk Management

Configured via `RiskConfig` (environment variables or code):

| Parameter | Default | Description |
|---|---|---|
| `MAX_POSITION_PCT` | 5% | Max capital in a single instrument |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | 80% | Max total exposure across all positions |
| `MAX_DAILY_LOSS_PCT` | 2% | Daily drawdown circuit-breaker |
| `DEFAULT_STOP_LOSS_PCT` | 2% | Default stop-loss if not set in signal |
| `DEFAULT_TAKE_PROFIT_PCT` | 4% | Default take-profit if not set in signal |

---

## Signal Pipeline

```
Pine Script file
      │
      ▼ Lexer → Parser → AST
      │
      ▼ Evaluator (bar-by-bar)
      │   └─ emits TradingSignal objects
      │
      ▼ SignalProcessor
      │   ├─ validates required fields
      │   ├─ checks market hours (09:15–15:30 IST, weekdays, non-holiday)
      │   └─ deduplicates signals within configurable window
      │
      ▼ RiskManager
      │   ├─ daily loss limit check
      │   ├─ portfolio exposure check
      │   └─ position size calculation (fixed / % capital / risk-based)
      │
      ▼ IBroker.submit_order()
          └─ PaperBroker | UpstoxBroker
```

---

## Sample Strategies

### EMA Crossover (`strategies/ema_crossover.pine`)
```pine
//@version=5
strategy("EMA Crossover", overlay=true)

fast = ta.ema(close, 9)
slow = ta.ema(close, 21)

if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fast, slow)
    strategy.close("Long")
```

### RSI Reversal (`strategies/rsi_reversal.pine`)
```pine
//@version=5
strategy("RSI Reversal", overlay=false)

rsi = ta.rsi(close, 14)

if rsi < 30
    strategy.entry("Long", strategy.long)

if rsi > 70
    strategy.close("Long")
```

---

## C++ OMS Core (Legacy)

The `src/` directory contains a C++ Order Management System built as the original foundation. It is kept for reference and low-latency use cases.

- **`src/core/OrderManager.h/.cpp`** — Thread-safe OMS with FSM order lifecycle
- **`src/Interface/IBrokerConnector.h`** — Broker abstraction interface
- **`src/mvp_main.cpp`** — Demo executable

Build with:
```bash
make          # release build → bin/mvp_main
make test     # run C++ unit tests (requires libgtest-dev)
make clean    # remove build artifacts
```

---

## Project Dependencies

| Package | Purpose |
|---|---|
| `pydantic>=2` | Data models & validation |
| `pytz` | IST timezone handling |
| `pandas` / `numpy` | Numerical computations |
| `httpx` | Async HTTP client |
| `pytest` + `freezegun` | Testing framework |
| `upstoxlite` | Upstox API wrapper (local package) |
