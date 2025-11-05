add_library(oms_core
    # Core OMS logic
    core/Order.cpp
    core/OrderManager.cpp
    # Add other .cpp files here
)

target_include_directories(oms_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR} # Allows #include "interfaces/IBrokerConnector.h"
    PRIVATE
        # Private include dirs if any
)

# Example of how you might add a specific connector later
# add_library(zerodha_connector plugins/zerodha/ZerodhaConnector.cpp)
# target_link_libraries(zerodha_connector PRIVATE oms_core)

# For executables (e.g., a trading engine or backtester)
# add_executable(trading_engine main.cpp)
# target_link_libraries(trading_engine PRIVATE oms_core)
