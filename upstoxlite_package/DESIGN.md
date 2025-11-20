# System Design & Decisions

## Overview
Upstoxlite is a lightweight, Pythonic wrapper for the Upstox API. It is designed to be robust, type-safe, and easy to use for both synchronous and asynchronous applications.

## Architectural Decisions

### 1. Sync and Async Clients
**Decision**: Provide both `UpstoxSyncClient` and `UpstoxAsyncClient`.
**Reasoning**:
- **Sync Client**: Best for scripts, data analysis notebooks, and simple trading bots where concurrency is not the primary bottleneck. It uses `requests` for simplicity and reliability.
- **Async Client**: Essential for high-frequency trading, websocket integration, and building responsive web applications (e.g., with FastAPI). It uses `aiohttp` for non-blocking I/O.

### 2. Type Safety with Pydantic
**Decision**: Use `pydantic` models for all API requests and responses.
**Reasoning**:
- **Validation**: Automatically validates incoming data from the API, catching unexpected changes or errors early.
- **Developer Experience**: Provides excellent IDE support (autocompletion, type hinting) compared to raw dictionaries.
- **Documentation**: The models serve as self-documenting code, making it clear what fields are expected.

### 3. Robust Error Handling & Retries
**Decision**: Implement automatic retries using `tenacity`.
**Reasoning**:
- **Rate Limiting**: Financial APIs often have strict rate limits. The client automatically handles `429 Too Many Requests` errors by parsing the `Retry-After` header and waiting.
- **Transient Failures**: Network glitches are common. Automatic backoff ensures stability without cluttering user code with retry loops.

### 4. Interactive Tester
**Decision**: Include a CLI-based `tester.py` with an interactive menu.
**Reasoning**:
- **Ease of Verification**: Allows users to manually test API endpoints (buy, sell, check margins) without writing a single line of code.
- **Safety**: Includes a "Sandbox Mode" by default to prevent accidental real-money trades during exploration.

### 5. Sandbox Support
**Decision**: First-class support for Upstox Sandbox.
**Reasoning**:
- **Safe Testing**: Essential for developing trading strategies without financial risk.
- **Implementation**: The `UpstoxConfig` class handles URL switching transparently based on the `sandbox` flag. The tester script supports switching modes easily.

### 6. Modular Structure
**Decision**: Split code into `clients.py`, `models.py`, `config.py`, `utils.py`, and `ws_client.py`.
**Reasoning**:
- **Maintainability**: Keeps files small and focused.
- **Separation of Concerns**: Configuration is separate from logic; data models are separate from API calls.

## Future Considerations
- **Websocket Improvements**: Enhance the websocket client with more robust reconnection logic and event-driven callbacks.
- **Coverage**: Continue adding remaining API endpoints as needed.
