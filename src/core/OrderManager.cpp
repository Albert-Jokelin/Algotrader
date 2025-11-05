#include "OrderManager.h"
#include "ExecutionReport.h" // Added include
#include <iostream> // For basic logging/debug
#include <stdexcept> // For error handling
#include <random>    // For clientOrderId generation
#include <sstream>   // For clientOrderId generation
#include <iomanip>   // For clientOrderId generation

namespace oms {
namespace core {

OrderManager::OrderManager(std::shared_ptr<interfaces::IBrokerConnector> broker)
    : primaryBroker_(std::move(broker)) {
    if (!primaryBroker_) {
        throw std::invalid_argument("Broker connector cannot be null.");
    }
}

std::string OrderManager::generateClientOrderId() {
    // Placeholder: Generate a somewhat unique ID.
    // In a production system, use a robust UUID library (e.g., Boost.UUID).
    auto now = std::chrono::system_clock::now();
    auto epoch_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> distrib(0, 0xFFFFFF);

    std::stringstream ss;
    ss << "OMS_" << epoch_ms << "_" << std::hex << std::setw(6) << std::setfill('0') << distrib(gen);
    return ss.str();
}

bool OrderManager::submitOrder(Order& order) {
    std::lock_guard<std::mutex> lock(ordersMutex_);

    if (order.clientOrderId.empty()) {
        order.clientOrderId = generateClientOrderId();
    }

    // Basic validation (can be expanded)
    if (order.symbol.empty() || order.quantity <= 0) {
        std::cerr << "OrderManager: Invalid order parameters for " << order.clientOrderId << std::endl;
        order.status = OrderStatus::REJECTED; // OMS internal rejection
        order.lastUpdateTimestamp = std::chrono::system_clock::now();
        // Optionally store rejected orders if needed for audit
        return false;
    }

    order.creationTimestamp = std::chrono::system_clock::now();
    order.lastUpdateTimestamp = order.creationTimestamp;
    order.status = OrderStatus::PENDING_NEW;
    order.filledQuantity = 0.0;
    order.remainingQuantity = order.quantity; // Initialize remaining quantity

    activeOrders_[order.clientOrderId] = order;

    std::cout << "OrderManager: Submitting order " << order.clientOrderId << std::endl;
    // Send a copy or const ref to the broker; broker should not modify OMS's canonical Order object.
    auto brokerOrderId = primaryBroker_->sendOrder(activeOrders_[order.clientOrderId]);

    if (brokerOrderId) {
        activeOrders_[order.clientOrderId].brokerOrderId = *brokerOrderId;
        activeOrders_[order.clientOrderId].status = OrderStatus::SUBMITTED; // Or PENDING_BROKER_ACK
        activeOrders_[order.clientOrderId].lastUpdateTimestamp = std::chrono::system_clock::now();

        // Update the input order reference as well
        order.brokerOrderId = *brokerOrderId;
        order.status = activeOrders_[order.clientOrderId].status;
        order.lastUpdateTimestamp = activeOrders_[order.clientOrderId].lastUpdateTimestamp;
        return true;
    }

    // Broker rejected submission immediately
    activeOrders_[order.clientOrderId].status = OrderStatus::REJECTED;
    activeOrders_[order.clientOrderId].lastUpdateTimestamp = std::chrono::system_clock::now();
    order.status = activeOrders_[order.clientOrderId].status;
    order.lastUpdateTimestamp = activeOrders_[order.clientOrderId].lastUpdateTimestamp;
    std::cerr << "OrderManager: Broker failed to accept order " << order.clientOrderId << std::endl;
    return false;
}

bool OrderManager::cancelOrder(const std::string& clientOrderId) {
    std::lock_guard<std::mutex> lock(ordersMutex_);
    auto it = activeOrders_.find(clientOrderId);
    if (it == activeOrders_.end()) {
        std::cerr << "OrderManager: Cannot cancel. Order not found: " << clientOrderId << std::endl;
        return false;
    }

    Order& orderToCancel = it->second;
    // Check if order is in a cancellable state (FSM logic can be more detailed)
    if (orderToCancel.status == OrderStatus::OPEN ||
        orderToCancel.status == OrderStatus::SUBMITTED ||
        orderToCancel.status == OrderStatus::PARTIALLY_FILLED) {

        std::cout << "OrderManager: Requesting cancel for order " << clientOrderId << std::endl;
        // Use brokerOrderId if available, otherwise clientOrderId (depends on broker API)
        const std::string& idForBroker = orderToCancel.brokerOrderId.empty() ? clientOrderId : orderToCancel.brokerOrderId;
        if (primaryBroker_->cancelOrder(idForBroker)) {
            orderToCancel.status = OrderStatus::PENDING_CANCEL;
            orderToCancel.lastUpdateTimestamp = std::chrono::system_clock::now();
            return true;
        } else {
            std::cerr << "OrderManager: Broker rejected cancel request for " << clientOrderId << std::endl;
            // Status remains unchanged, or move to a specific "CANCEL_REJECTED" state
            return false;
        }
    } else {
        std::cerr << "OrderManager: Order " << clientOrderId << " not in a cancellable state. Current status: "
                  << static_cast<int>(orderToCancel.status) << std::endl;
        return false;
    }
}

std::optional<Order> OrderManager::getOrderStatus(const std::string& clientOrderId) const {
    std::lock_guard<std::mutex> lock(ordersMutex_);
    auto it = activeOrders_.find(clientOrderId);
    if (it != activeOrders_.end()) {
        return it->second;
    }
    std::cerr << "OrderManager: Order status requested for unknown order " << clientOrderId << std::endl;
    return std::nullopt;
}

void OrderManager::processExecutionReport(const ExecutionReport& report) {
    std::lock_guard<std::mutex> lock(ordersMutex_);
    auto it = activeOrders_.find(report.clientOrderId);
    if (it == activeOrders_.end()) {
        std::cerr << "OrderManager: Received execution report for unknown clientOrderId: " << report.clientOrderId << std::endl;
        return;
    }

    Order& order = it->second;
    std::cout << "OrderManager: Processing execution report for " << report.clientOrderId
              << ". New status: " << static_cast<int>(report.status)
              << ", Filled: " << report.cumulativeQuantityFilled << "/" << order.quantity << std::endl;

    // Update order based on report (this is a simplified FSM)
    order.status = report.status;
    order.brokerOrderId = report.brokerOrderId; // Ensure brokerOrderId is up-to-date

    // Use the Order's updateQuantities method if appropriate, or directly set from report
    // For simplicity here, directly using cumulative values from report.
    // A more robust solution would use order.updateQuantities(report.quantityFilledThisExecution)
    // and then verify consistency.
    order.filledQuantity = report.cumulativeQuantityFilled;
    order.remainingQuantity = order.quantity - report.cumulativeQuantityFilled;
    if (order.remainingQuantity < 0) order.remainingQuantity = 0; // Safety check

    order.lastUpdateTimestamp = std::chrono::system_clock::now();

    // If order is in a terminal state, it might be moved from activeOrders_ to a historical store.
    if (order.isTerminal()) {
        std::cout << "OrderManager: Order " << order.clientOrderId << " reached terminal state: " << static_cast<int>(order.status) << std::endl;
        // In a real system, consider moving to a different container/DB or marking for archival.
    }
}

} // namespace core
} // namespace oms
