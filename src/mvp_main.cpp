#include "core/OrderManager.h"
#include "core/Order.h"
#include "core/ExecutionReport.h"
#include "Interface/IBrokerConnector.h" // Corrected path

#include <iostream>
#include <memory>
#include <vector>
#include <functional>
#include <optional>
#include <thread> // For std::this_thread::sleep_for
#include <chrono> // For std::chrono::seconds

// Mock Broker Connector (similar to the one in tests)
class MVPBrokerConnector : public oms::interfaces::IBrokerConnector {
public:
    std::function<void(const oms::core::ExecutionReport&)> execReportCallback_;

    bool connect(const std::string&, const std::string&, const std::string&) override {
        std::cout << "[MVPBrokerConnector] Connected." << std::endl;
        return true;
    }
    void disconnect() override { std::cout << "[MVPBrokerConnector] Disconnected." << std::endl; }
    bool isConnected() const override { return true; }

    std::optional<std::string> sendOrder(const oms::core::Order& order) override {
        std::cout << "[MVPBrokerConnector] Received order: " << order.clientOrderId << " for " << order.symbol << std::endl;
        // Simulate broker accepting the order and assigning an ID
        std::string brokerId = "broker_" + order.clientOrderId;

        // Simulate an immediate acknowledgement (optional, could be an execution report)
        oms::core::ExecutionReport ackReport;
        ackReport.clientOrderId = order.clientOrderId;
        ackReport.brokerOrderId = brokerId;
        ackReport.symbol = order.symbol;
        ackReport.status = oms::core::OrderStatus::SUBMITTED; // Or OPEN if directly accepted by exchange mock
        ackReport.side = order.side;
        ackReport.quantityOrdered = order.quantity;
        ackReport.timestamp = std::chrono::system_clock::now();
        if(execReportCallback_) execReportCallback_(ackReport);

        return brokerId;
    }
    bool cancelOrder(const std::string& orderId) override {
        std::cout << "[MVPBrokerConnector] Received cancel request for: " << orderId << std::endl;
        return true;
    }
    bool modifyOrder(const oms::core::Order&) override { return true; }

    std::vector<oms::core::Order> getOpenOrders() const override { return {}; }
    std::vector<oms::interfaces::Position> getPositions() const override { return {}; }
    std::optional<double> getAccountBalance() const override { return 1000000.0; }

    void setMarketDataCallback(std::function<void(const oms::interfaces::MarketData&)>) override {}
    void setExecutionReportCallback(std::function<void(const oms::core::ExecutionReport&)> cb) override {
        execReportCallback_ = cb;
    }

    // Helper to simulate a fill from the broker
    void simulateFill(const oms::core::Order& originalOrder, const std::string& brokerOrderId) {
        if (execReportCallback_) {
            oms::core::ExecutionReport fillReport;
            fillReport.clientOrderId = originalOrder.clientOrderId;
            fillReport.brokerOrderId = brokerOrderId;
            fillReport.symbol = originalOrder.symbol;
            fillReport.status = oms::core::OrderStatus::FILLED;
            fillReport.side = originalOrder.side;
            fillReport.quantityOrdered = originalOrder.quantity;
            fillReport.quantityFilledThisExecution = originalOrder.quantity;
            fillReport.cumulativeQuantityFilled = originalOrder.quantity;
            fillReport.lastExecutedPrice = (originalOrder.price.has_value() ? originalOrder.price.value() : 100.0); // Use order price or a mock price
            fillReport.averageFillPrice = fillReport.lastExecutedPrice;
            fillReport.timestamp = std::chrono::system_clock::now();
            std::cout << "[MVPBrokerConnector] Simulating FILL for " << originalOrder.clientOrderId << std::endl;
            execReportCallback_(fillReport);
        }
    }
};

int main() {
    std::cout << "--- OMS MVP Application Start ---" << std::endl;

    auto brokerConnector = std::make_shared<MVPBrokerConnector>();
    oms::core::OrderManager orderManager(brokerConnector);

    // Connect the execution report callback from the broker to the OrderManager
    brokerConnector->setExecutionReportCallback(
        [&orderManager](const oms::core::ExecutionReport& report) {
            orderManager.processExecutionReport(report);
        }
    );

    // 1. Simple Strategy: Create and submit an order
    std::cout << "\n[Strategy] Creating a BUY order for RELIANCE_NSE_EQ..." << std::endl;
    oms::core::Order myStrategyOrder;
    myStrategyOrder.symbol = "RELIANCE_NSE_EQ";
    myStrategyOrder.type = oms::core::OrderType::LIMIT;
    myStrategyOrder.side = oms::core::OrderSide::BUY;
    myStrategyOrder.quantity = 10;
    myStrategyOrder.price = 2800.0;

    if (orderManager.submitOrder(myStrategyOrder)) {
        std::cout << "[Strategy] Order submitted successfully. Client Order ID: " << myStrategyOrder.clientOrderId
                  << ", Broker Order ID: " << myStrategyOrder.brokerOrderId << std::endl;

        // 2. Simulate a delay and then a fill from the broker
        std::cout << "\n[MVP] Simulating exchange activity..." << std::endl;
        std::this_thread::sleep_for(std::chrono::seconds(2)); // Simulate time passing

        // The broker needs the brokerOrderId to map the fill, but our mock can use clientOrderId if needed
        // For this simulation, we pass the original order and its broker ID.
        brokerConnector->simulateFill(myStrategyOrder, myStrategyOrder.brokerOrderId);

        // 3. Check final order status
        std::this_thread::sleep_for(std::chrono::seconds(1)); // Allow OMS to process
        auto finalStatus = orderManager.getOrderStatus(myStrategyOrder.clientOrderId);
        if (finalStatus) {
            std::cout << "\n[Strategy] Final order status for " << finalStatus->clientOrderId << ": "
                      << static_cast<int>(finalStatus->status)
                      << " (Filled: " << finalStatus->filledQuantity << "/" << finalStatus->quantity << ")"
                      << std::endl;
        } else {
            std::cout << "[Strategy] Could not retrieve final status for order " << myStrategyOrder.clientOrderId << std::endl;
        }

    } else {
        std::cout << "[Strategy] Failed to submit order." << std::endl;
    }

    std::cout << "\n--- OMS MVP Application End ---" << std::endl;
    return 0;
}
