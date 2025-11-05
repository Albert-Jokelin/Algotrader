#ifndef OMS_ORDER_MANAGER_H
#define OMS_ORDER_MANAGER_H

#include "ExecutionReport.h" // Added include
#include "Order.h"
#include "Interface/IBrokerConnector.h" // For interaction with brokers
#include <string>
#include <vector>
#include <map>
#include <memory> // For std::unique_ptr or std::shared_ptr
#include <mutex>  // Added for thread safety

namespace oms {
namespace core {

class OrderManager {
public:
    // Takes a primary broker connector. Could be extended for multiple.
    // The IBrokerConnector implementation should be responsible for invoking processExecutionReport on this OrderManager.
    explicit OrderManager(std::shared_ptr<interfaces::IBrokerConnector> broker);

    // Submit a new order
    bool submitOrder(Order& order); // Order might be modified with IDs, status

    // Cancel an existing order
    bool cancelOrder(const std::string& clientOrderId);

    // Get order status
    std::optional<Order> getOrderStatus(const std::string& clientOrderId) const;

    // Handle execution reports from the broker (to be called by connector or event bus)
    void processExecutionReport(const ExecutionReport& report); // Uncommented and type specified

private:
    std::string generateClientOrderId(); // Added declaration

    std::shared_ptr<interfaces::IBrokerConnector> primaryBroker_;
    std::map<std::string, Order> activeOrders_; // Keyed by clientOrderId
    mutable std::mutex ordersMutex_; // Added to protect access to activeOrders_
    // Add FSM logic, idempotency checks, retry mechanisms, etc.
};

} // namespace core
} // namespace oms
#endif // OMS_ORDER_MANAGER_H
