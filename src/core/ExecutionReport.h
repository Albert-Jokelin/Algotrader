#ifndef OMS_EXECUTION_REPORT_H
#define OMS_EXECUTION_REPORT_H

#include "Order.h" // For OrderStatus, OrderSide
#include <string>
#include <chrono>
#include <optional>

namespace oms {
namespace core {

struct ExecutionReport {
    std::string clientOrderId;
    std::string brokerOrderId;
    std::string symbol;
    OrderStatus status = OrderStatus::NEW; // Default for safety, but should be set by source
    OrderSide side = OrderSide::BUY;     // Useful for context, default for safety

    double quantityOrdered = 0.0;
    double quantityFilledThisExecution = 0.0;
    double cumulativeQuantityFilled = 0.0;
    std::optional<double> lastExecutedPrice;
    std::optional<double> averageFillPrice;

    std::chrono::system_clock::time_point timestamp;
    std::string message; // Optional message from broker/exchange
};

} // namespace core
} // namespace oms
#endif // OMS_EXECUTION_REPORT_H
