# Issue Disposition Log

This file records the disposition of every item in the original `TODO` file
(written during the C++ alpha in late 2024) and of any gaps identified during
the Python rewrite review (March 2025).

---

## Original TODO Items

The `TODO` file describes a C++-based alpha system targeting a hand-off to a
colleague on 20 November 2024.  The project has since been fully rewritten in
Python.  Every item below is therefore addressed either by the current
implementation or explicitly marked out-of-scope.

### Version 0.0.0 Alpha

| Item | Disposition |
|------|-------------|
| Read Upstox documentation to figure out their API | **Implemented** — `algotrader/broker/upstox_broker.py` fully implements the Upstox v2 REST API (order placement, fund-and-margin, order-book reconciliation, instrument master). |
| Understand and rewrite code as per Upstox API | **Implemented** — the entire broker layer is built around Upstox v2. |
| Create a tester that can be used to test buy, sell and view portfolio | **Implemented** — `python -m algotrader backtest` runs full paper-trading backtests; `python -m algotrader live --broker paper` runs in paper-live mode. Unit tests in `tests/unit/test_upstox_broker.py` cover submit, cancel, reconcile, and fund-balance. |
| Create a wiki page to store details about knowledge gained | **Out of scope** — the system is now documented via module-level docstrings, this file, and inline edge-constraint comments.  A GitHub Wiki can be added later if desired. |

### Version 0.0.1 Alpha

| Item | Disposition |
|------|-------------|
| Rewrite code to follow standard C++ design patterns | **Out of scope** — the project was rewritten in Python; Python idioms (dataclasses, Pydantic models, ABC interfaces) are used throughout. |
| Simulate buy/sell/portfolio with paper money; compile two different applications | **Implemented** — `PaperBroker` provides full paper-trading with realistic charges, slippage, and partial fills.  The `--broker paper` flag selects paper mode; `--broker upstox` selects live mode.  No separate compilation is needed. |
| Create a mechanism for users to write strategies (Pine Script v5 → optimised C/C++) | **Implemented** (differently) — `algotrader/pine/` contains a full Pine Script v5 interpreter written in Python.  Compilation to C++ is out of scope; the Python evaluator is fast enough for 1-minute bar strategies. |

---

## Production-Readiness Gaps (March 2025 Review)

These gaps were identified in the code review and have been implemented.

| Gap | Status | Implementation |
|-----|--------|----------------|
| Pine Script interpreter safety — silent failure on unsupported constructs | **Fixed** | `UnsupportedFeatureError` added to `algotrader/exceptions.py`. The evaluator raises this error at script-load time for: unknown AST node types (for-loops, while-loops, user-defined functions/types), unknown `ta.*` functions, unknown namespaces (e.g. `array.*`, `matrix.*`), and unknown bare function calls. See `algotrader/pine/evaluator.py`. |
| Order submission resilience — no idempotency on network-response loss | **Fixed** | `UpstoxBroker` now: (1) tags every order with `order.order_id[:20]` as the Upstox `tag` field; (2) maintains `_submitted_ids` to block duplicate submissions; (3) exposes `reconcile_open_orders()` which matches local orders against the live Upstox order book by `broker_order_id` or tag. See `algotrader/broker/upstox_broker.py`. |
| Crash recovery and state persistence | **Fixed** | `StateStore` in `algotrader/live/state_store.py` persists positions, open orders, equity curve, and daily P&L to `~/.algotrader/state/<symbol>_<exchange>_<strategy>.json` after every bar. Writes are atomic (temp-file + rename). Stale-date detection resets daily P&L on the next trading day while preserving NRML overnight positions. |
| TODO file resolution | **Fixed** — this document. |

---

## Known Remaining Limitations

These are known constraints, not bugs.  They are out of scope for the current
release but should be tracked for future work.

| Limitation | Notes |
|-----------|-------|
| Pine Script for-loops and user-defined functions | Not supported.  The interpreter raises `UnsupportedFeatureError` with a clear message.  Most strategies can be rewritten using `ta.*` series functions. |
| `array.*`, `matrix.*`, `map.*` namespaces | Not supported — raise `UnsupportedFeatureError`. |
| Multi-leg / options strategies | Not supported.  Only NSE/BSE equity (EQ) instruments are handled. |
| Upstox WebSocket streaming | The current live feed uses REST polling.  WebSocket support (`algotrader/data/live_feed.py`) is stubbed but not connected. |
| Persistent order tracking across days for GTC orders | GTC (Good-Till-Cancelled) orders are supported at the model level (`validity="GTC"`) but the Upstox API resets them daily; multi-day GTC requires a manual re-submission strategy. |
