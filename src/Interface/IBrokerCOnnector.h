#ifndef OMS_IBROKER_CONNECTOR_H
#define OMS_IBROKER_CONNECTOR_H

#include "../core/ExecutionReport.h" // Definition of ExecutionReport
#include <string>
#include <vector>
#include <functional>
#include <optional>

namespace oms {
namespace interfaces {

// Basic placeholder structs, to be fleshed out later or moved to separate files.
struct MarketData {
    std::string symbol;
    double lastPrice = 0.0;
    double bidPrice = 0.0;
    double askPrice = 0.0;
    double volume = 0.0;
    std::chrono::system_clock::time_point timestamp;
};

struct Position { // Provide a full definition
    std::string symbol;
    double quantity = 0.0;
    double averagePrice = 0.0;
    // Add P&L, etc.
};
// ExecutionReport is now included from ../core/ExecutionReport.h

class IBrokerConnector {
public:
    virtual ~IBrokerConnector() = default;

    virtual bool connect(const std::string& apiKey, const std::string& apiSecret, const std::string& endpoint) = 0;
    virtual void disconnect() = 0;
    virtual bool isConnected() const = 0;

    virtual std::optional<std::string> sendOrder(const oms::core::Order& order) = 0;
    virtual bool cancelOrder(const std::string& orderId) = 0;
    virtual bool modifyOrder(const oms::core::Order& order) = 0;

    virtual std::vector<oms::core::Order> getOpenOrders() const = 0;
    virtual std::vector<Position> getPositions() const = 0;
    virtual std::optional<double> getAccountBalance() const = 0;

    // Callbacks for asynchronous events (market data, order updates)
    virtual void setMarketDataCallback(std::function<void(const MarketData&)> callback) = 0;
    virtual void setExecutionReportCallback(std::function<void(const oms::core::ExecutionReport&)> callback) = 0;
    // Add other callbacks for order updates (ACK/NACK, fills), errors, etc.
};

} // namespace interfaces
} // namespace oms
#endif // OMS_IBROKER_CONNECTOR_H
