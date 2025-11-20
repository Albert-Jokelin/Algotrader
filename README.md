# Algorithmic Trading System: OMS Core & Market Analysis

This repository contains the building blocks for an algorithmic trading system, featuring a C++ core for a high-performance Order Management System (OMS) and Python scripts for market analysis.

The system is designed with a clear separation of concerns:
-   **C++ Core**: Handles the critical, low-latency tasks of creating, sending, and managing the lifecycle of trading orders.
-   **Python Scripts**: Used for higher-level financial analysis, data fetching, and strategy prototyping.

## System Architecture and Design Patterns

The project follows a modular architecture where different components can be developed and tested independently. The design heavily relies on established software design patterns to ensure flexibility and maintainability.


### Core Design Patterns

*   **Strategy Pattern**: The `IBrokerConnector` interface allows the system's trading behavior to be changed at compile time. The `OrderManager` is configured with a "strategy" for communicating with a broker—either a `MockBrokerConnector` for testing or a `UpstoxBrokerConnector` for live trading. This decouples the core logic from the specific broker implementation.

*   **Factory Method Pattern**: A `BrokerFactory` class provides a static method (`createBrokerConnector`) to construct the appropriate broker object. This abstracts the instantiation logic and decides which connector to create based on the compile-time `MODE` flag, cleanly separating configuration from use.

*   **Facade Pattern**: The `OrderManager` class acts as a Facade, providing a simple, unified interface (`submitOrder`, `cancelOrder`) to the more complex underlying subsystems of order validation, state management, and broker communication.

*   **State Pattern (Finite State Machine)**: The lifecycle of an `Order` (from `PENDING_NEW` to `FILLED` or `CANCELLED`) is managed as a finite state machine within the `OrderManager`. The `processExecutionReport` function drives the transitions between these states based on incoming broker reports.



## Architecture Overview

The project follows a modular architecture where different components can be developed and tested independently.

1.  **Analysis (Python)**: Scripts like `market_regime.py` analyze market data (e.g., from Yahoo Finance) to determine the market's current state (e.g., "Trending," "Volatile").
2.  **Strategy (Conceptual)**: A trading strategy (which would be built on top of this framework) uses the analysis to make decisions (e.g., "If the market is Trending, buy RELIANCE").
3.  **Execution (C++)**: The strategy instructs the C++ `OrderManager` to submit an order.
4.  **Broker Interaction (C++)**: The `OrderManager` sends the order to a financial broker via a connector that implements the `IBrokerConnector` interface.

5.  **State Management (C++)**: The `OrderManager` listens for feedback from the broker (via `ExecutionReport` callbacks) to maintain the real-time status of each order (e.g., `SUBMITTED`, `FILLED`, `CANCELLED`).

## Directory Structure

```
.
├── Backtesting/
│   └── test.py           # Python script to fetch historical data from Upstox.
├── src/
│   ├── core/
│   │   ├── Order.h
│   │   ├── OrderManager.h
│   │   ├── OrderManager.cpp
│   │   └── ...             # Other core C++ components.
│   ├── Interface/
│   │   └── IBrokerConnector.h # Abstraction for connecting to brokers.
│   └── mvp_main.cpp        # Main application demonstrating OMS usage.
├── tests/
│   └── OrderManager_test.cpp # Unit tests for the OrderManager.
├── market_regime.py        # Python script for market regime analysis.
├── Makefile                # GNU Makefile for building the C++ code.
└── README.md               # This documentation file.
```

## C++ Core: Order Management System (OMS)

The C++ core is a robust, thread-safe system for managing the entire lifecycle of trading orders.

### Key Components

#### `OrderManager` (`src/core/OrderManager.h`, `src/core/OrderManager.cpp`)
This is the central class of the OMS.
-   **Responsibilities**:
    -   `submitOrder`: Validates an order, assigns it a unique `clientOrderId`, and sends it to the broker.
    -   `cancelOrder`: Sends a cancellation request for an active order.
    -   `processExecutionReport`: A critical callback function that processes status updates from the broker (e.g., fills, cancellations) and updates the internal state of the corresponding order.
    -   `getOrderStatus`: Retrieves the current status of any tracked order.
-   **Thread Safety**: It uses a `std::mutex` to protect its internal order map (`activeOrders_`), making it safe to use in a multi-threaded environment where orders might be submitted and status reports received concurrently.

#### `IBrokerConnector` (`src/Interface/IBrokerConnector.h`)
This is an abstract base class (an interface) that defines the contract for any broker connection.
-   **Purpose**: It decouples the `OrderManager` from any specific broker's API. This allows the system to switch between different brokers (e.g., Upstox, Interactive Brokers) simply by providing a new concrete implementation of this interface. This is an application of the **Dependency Inversion Principle**.

#### `mvp_main.cpp` (`src/mvp_main.cpp`)
This is a sample executable that demonstrates how to use the `OrderManager`. It simulates a simple trading scenario:
1.  A mock broker connector is created.
2.  The `OrderManager` is initialized with the mock broker.
3.  A sample BUY order for "RELIANCE" is created.
4.  The order is submitted via the `OrderManager`.
5.  The mock broker simulates receiving the order and later sending back a "FILLED" execution report.
6.  The `OrderManager` processes the report and updates the order's status to `FILLED`.

## Python Scripts

The Python scripts are used for data analysis and prototyping, providing the intelligence that would drive trading decisions.

### `market_regime.py`
-   **Purpose**: To identify the market's current "regime" or state. This is a common technique used to select the most appropriate trading strategy for current conditions.
-   **Functionality**:
    1.  Downloads historical price data for the Nifty 50 index (`^NSEI`) from Yahoo Finance.
    2.  Calculates three key technical indicators:
        -   **Simple Moving Average (SMA)**: To gauge the trend direction.
        -   **Average True Range (ATR)**: To measure market volatility.
        -   **Average Directional Index (ADX)**: To measure the strength of the current trend.
    3.  Classifies each day into one of three regimes based on the indicators:
        -   **Trending**: Strong, directional market movement.
        -   **Volatile**: Choppy price action with large swings but no clear trend.
        -   **Ranging**: Calm, sideways market with low volatility.
    4.  Generates a plot of the price history, color-coded by the identified regime, and saves the data to `market_regimes.csv`.

### `Backtesting/test.py`
-   **Purpose**: A utility script to fetch historical candlestick data from the **Upstox API**.
-   **Usage**: This script demonstrates how to connect to a specific broker's API to gather data. This data is essential for backtesting trading strategies to evaluate their historical performance before deploying them live.

## How to Compile the C++ Code

The C++ application is built using GNU Make.

### Prerequisites

1.  **g++ Compiler**: Ensure you have a modern C++ compiler that supports C++17. On Debian/Ubuntu, you can install it with:
    ```sh
    sudo apt-get update
    sudo apt-get install build-essential
    ```

2.  **Google Test (for running tests)**: To compile and run the unit tests, you need to install the Google Test framework.
    ```sh
    sudo apt-get install libgtest-dev libgmock-dev
    ```

### Build Commands

All commands should be run from the root directory of the project.

-   **Compile the main application (Release Mode)**:
    This builds an optimized executable at `bin/mvp_main`.
    ```sh
    make
    ```

-   **Compile in Debug Mode**:
    This includes debugging symbols for use with tools like `gdb`.
    ```sh
    make DEBUG=1
    ```

-   **Compile and Run Tests**:
    This builds the test runner at `bin/run_tests` and executes it.
    ```sh
    make test
    ./bin/run_tests
    ```

-   **Clean Build Artifacts**:
    This removes the `build` and `bin` directories to clean up all compiled files.
    ```sh
    make clean
    ```
#### Interactive Tester

-   **Build and Run the Interactive Tester**:
    This special application lets you simulate buy/sell commands against the `UpstoxBrokerConnector`. It always builds in `PROD` mode.
    ```sh
    make upstox_tester && ./bin/upstox_tester
    ```

    # Algorithmic Trading System: OMS Core & Market Analysis

This repository contains the building blocks for an algorithmic trading system, featuring a C++ core for a high-performance Order Management System (OMS) and Python scripts for market analysis.

The system is designed with a clear separation of concerns:
-   **C++ Core**: Handles the critical, low-latency tasks of creating, sending, and managing the lifecycle of trading orders.
-   **Python Scripts**: Used for higher-level financial analysis, data fetching, and strategy prototyping.


## Directory Structure
