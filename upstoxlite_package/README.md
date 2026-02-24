# Upstoxlite

A robust, type-safe Python wrapper for the Upstox API, supporting both Synchronous and Asynchronous usage.

## Features
- **Full Coverage**: Supports Orders, Holdings, Positions, Market Quotes, Option Chain, Historical Data, and more.
- **Type Safe**: Uses Pydantic models for all requests and responses.
- **Resilient**: Automatic retries for rate limits and network errors.
- **Sandbox Support**: Built-in support for Upstox Sandbox for safe testing.
- **Interactive Tester**: CLI tool to explore the API without writing code.

## System Design
For a detailed overview of the architectural decisions and system design, please refer to [DESIGN.md](DESIGN.md).

---

## Installation

### Prerequisites
- Python 3.9+
- Upstox API Credentials (API Key, Secret, Redirect URI)

### Setting up a Virtual Environment (Recommended)

It is highly recommended to run this project in a virtual environment to avoid conflicts with other packages.

1.  **Create a Virtual Environment**:
    ```bash
    python3 -m venv venv
    ```

2.  **Activate the Virtual Environment**:
    - **macOS/Linux**:
      ```bash
      source venv/bin/activate
      ```
    - **Windows**:
      ```bash
      .\venv\Scripts\activate
      ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    # OR if installing the package in editable mode:
    pip install -e .
    ```

---

## Configuration

Create a `.env` file in the root directory with your credentials:

```env
UPSTOX_API_KEY=your_api_key
UPSTOX_API_SECRET=your_api_secret
UPSTOX_REDIRECT_URI=your_redirect_uri

# Optional: For Sandbox Mode
UPSTOX_SANDBOX_ACCESS_TOKEN=your_sandbox_access_token
```

---

## Usage

### Interactive Tester
To explore the API capabilities interactively:

```bash
python tester.py
```
Follow the on-screen menu to select Sandbox or Production mode and perform actions like placing orders, checking quotes, etc.

### Library Usage

```python
from upstoxlite.config import UpstoxConfig
from upstoxlite.clients import UpstoxSyncClient

# Configure
cfg = UpstoxConfig(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri",
    sandbox=True  # Set to False for Production
)

# Initialize Client
client = UpstoxSyncClient(cfg)

# Authorize (if no access token)
print(client.build_authorize_url())
# ... exchange code for token ...

# Use API
profile = client.get_profile()
print(profile.name)

# Fetch historical candles
candles = client.get_historical_candle_data(
    instrument_key="NSE_EQ|INE002A01018",  # RELIANCE
    interval="1day",
    from_date="2024-01-01",
    to_date="2024-12-31",
)
```

---

## Integration with Algotrader

This package is used as the live broker backend for the parent **Algotrader** system. The `UpstoxBroker` class in `algotrader/broker/upstox_broker.py` wraps `UpstoxSyncClient` to implement the `IBroker` interface:

```
algotrader (Pine Script engine)
    └─► algotrader.broker.UpstoxBroker
            └─► upstoxlite.UpstoxSyncClient
                    └─► Upstox REST API
```

### Broker modes

| `BROKER_MODE` | Upstoxlite usage |
|---|---|
| `paper` | Not used — local simulation only |
| `sandbox` | `UpstoxConfig(sandbox=True)` |
| `live` | `UpstoxConfig(sandbox=False)` |

### Symbol mapping

`UpstoxBroker` maintains a built-in `_SYMBOL_MAP` that translates NSE ticker symbols (e.g. `RELIANCE`) to Upstox instrument keys (e.g. `NSE_EQ|INE002A01018`). To add support for a new instrument, update `_SYMBOL_MAP` in `algotrader/broker/upstox_broker.py`.

### Historical data

`algotrader.data.HistoricalDataService` calls `UpstoxSyncClient.get_historical_candle_data()` to fetch OHLCV bars for backtesting. If the API call fails (network error, missing token), it falls back to CSV files in `data_cache/{EXCHANGE}_{SYMBOL}_{interval}.csv`.

---

## Running Tests

```bash
# From the upstoxlite_package directory
pip install -e ".[dev]"
pytest tests/ -v
```
