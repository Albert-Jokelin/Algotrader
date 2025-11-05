#include "core/OrderManager.h"
#include "core/Order.h"
#include "core/ExecutionReport.h"
#include "Interface/IBrokerConnector.h" // Corrected path
#include <iostream>
#include <cassert>
#include <vector>
#include <optional>
#include <memory> // For std::make_shared

// Mock Broker Connector for testing purposes
class MockBrokerConnector : public oms::interfaces::IBrokerConnector {
public:
    std::function<void(const oms::core::ExecutionReport&)> execReportCallback_;
    oms::core::Order lastSentOrder;
    std::string lastCancelledOrderId;

    bool connect(const std::string&, const std::string&, const std::string&) override { return true; }
    void disconnect() override {}
    bool isConnected() const override { return true; }

    std::optional<std::string> sendOrder(const oms::core::Order& order) override {
        lastSentOrder = order;
        // Simulate broker assigning an ID
        return "mockBrokerID_" + order.clientOrderId;
    }
    bool cancelOrder(const std::string& orderId) override {
        lastCancelledOrderId = orderId;
        return true; // Simulate successful cancel request
    }
    bool modifyOrder(const oms::core::Order&) override { return true; }

    std::vector<oms::core::Order> getOpenOrders() const override { return {}; }
    std::vector<oms::interfaces::Position> getPositions() const override { return {}; } // Assuming Position is defined
    std::optional<double> getAccountBalance() const override { return 100000.0; }

    // Corrected signatures to match IBrokerConnector
    void setMarketDataCallback(std::function<void(const oms::interfaces::MarketData&)>) override {}
    void setExecutionReportCallback(std::function<void(const oms::core::ExecutionReport&)> cb) override {
        execReportCallback_ = cb;
    }

    // Helper to simulate an execution report from the broker
    void simulateExecutionReport(const oms::core::ExecutionReport& report) {
        if (execReportCallback_) {
            execReportCallback_(report);
        } else {
            std::cerr << "MockBrokerConnector: execReportCallback_ not set!" << std::endl;
        }
    }
};

void testOrderSubmission() {
    auto mockBroker = std::make_shared<MockBrokerConnector>();
    oms::core::OrderManager orderManager(mockBroker);

    // The OrderManager expects the IBrokerConnector to call its processExecutionReport.
    // So, the connector needs a way to call back to the OrderManager.
    // For this test, we'll set the callback on the mock broker to directly call the orderManager's method.
    // In a real system, this link is established during setup.
    mockBroker->setExecutionReportCallback(
        [&orderManager](const oms::core::ExecutionReport& report){
            orderManager.processExecutionReport(report);
        }
    );

    oms::core::Order newOrder;
    newOrder.symbol = "RELIANCE_NSE_EQ";
    newOrder.type = oms::core::OrderType::LIMIT;
    newOrder.side = oms::core::OrderSide::BUY;
    newOrder.quantity = 100;
    newOrder.price = 2500.0;

    bool submitted = orderManager.submitOrder(newOrder);
    assert(submitted);
    assert(!newOrder.clientOrderId.empty());
    assert(newOrder.status == oms::core::OrderStatus::SUBMITTED); // Or PENDING_BROKER_ACK
    assert(newOrder.brokerOrderId == "mockBrokerID_" + newOrder.clientOrderId);
    assert(mockBroker->lastSentOrder.clientOrderId == newOrder.clientOrderId);

    std::cout << "testOrderSubmission: PASSED" << std::endl;
}

void testOrderCancellation() {
    auto mockBroker = std::make_shared<MockBrokerConnector>();
    oms::core::OrderManager orderManager(mockBroker);
    mockBroker->setExecutionReportCallback(
        [&orderManager](const oms::core::ExecutionReport& report){
            orderManager.processExecutionReport(report);
        }
    );

    oms::core::Order newOrder;
    newOrder.symbol = "SBIN_NSE_EQ";
    newOrder.type = oms::core::OrderType::LIMIT;
    newOrder.side = oms::core::OrderSide::SELL;
    newOrder.quantity = 50;
    newOrder.price = 600.0;

    orderManager.submitOrder(newOrder); // clientOrderId is generated here
    assert(!newOrder.clientOrderId.empty());

    bool cancelRequested = orderManager.cancelOrder(newOrder.clientOrderId);
    assert(cancelRequested);
    assert(mockBroker->lastCancelledOrderId == newOrder.brokerOrderId); // Broker cancels using its ID

    auto statusAfterCancelReq = orderManager.getOrderStatus(newOrder.clientOrderId);
    assert(statusAfterCancelReq.has_value());
    assert(statusAfterCancelReq->status == oms::core::OrderStatus::PENDING_CANCEL);

    // Simulate broker confirming cancellation
    oms::core::ExecutionReport cancelConfirmReport;
    cancelConfirmReport.clientOrderId = newOrder.clientOrderId;
    cancelConfirmReport.brokerOrderId = newOrder.brokerOrderId;
    cancelConfirmReport.symbol = newOrder.symbol;
    cancelConfirmReport.status = oms::core::OrderStatus::CANCELLED;
    cancelConfirmReport.side = newOrder.side;
    cancelConfirmReport.quantityOrdered = newOrder.quantity;
    cancelConfirmReport.cumulativeQuantityFilled = 0; // No fills before cancel
    cancelConfirmReport.timestamp = std::chrono::system_clock::now();
    mockBroker->simulateExecutionReport(cancelConfirmReport);

    auto finalStatus = orderManager.getOrderStatus(newOrder.clientOrderId);
    assert(finalStatus.has_value());
    assert(finalStatus->status == oms::core::OrderStatus::CANCELLED);

    std::cout << "testOrderCancellation: PASSED" << std::endl;
}

void testExecutionReportProcessing_Fill() {
    auto mockBroker = std::make_shared<MockBrokerConnector>();
    oms::core::OrderManager orderManager(mockBroker);
    mockBroker->setExecutionReportCallback(
        [&orderManager](const oms::core::ExecutionReport& report){
            orderManager.processExecutionReport(report);
        }
    );

    oms::core::Order newOrder;
    newOrder.symbol = "TCS_NSE_EQ";
    newOrder.type = oms::core::OrderType::MARKET;
    newOrder.side = oms::core::OrderSide::BUY;
    newOrder.quantity = 20;

    orderManager.submitOrder(newOrder);
    assert(!newOrder.clientOrderId.empty());

    // Simulate partial fill
    oms::core::ExecutionReport partialFillReport;
    partialFillReport.clientOrderId = newOrder.clientOrderId;
    partialFillReport.brokerOrderId = newOrder.brokerOrderId;
    partialFillReport.symbol = newOrder.symbol;
    partialFillReport.status = oms::core::OrderStatus::PARTIALLY_FILLED;
    partialFillReport.side = newOrder.side;
    partialFillReport.quantityOrdered = newOrder.quantity;
    partialFillReport.quantityFilledThisExecution = 10;
    partialFillReport.cumulativeQuantityFilled = 10;
    partialFillReport.lastExecutedPrice = 3500.0;
    partialFillReport.timestamp = std::chrono::system_clock::now();
    mockBroker->simulateExecutionReport(partialFillReport);

    auto statusAfterPartialFill = orderManager.getOrderStatus(newOrder.clientOrderId);
    assert(statusAfterPartialFill.has_value());
    assert(statusAfterPartialFill->status == oms::core::OrderStatus::PARTIALLY_FILLED);
    assert(statusAfterPartialFill->filledQuantity == 10);
    assert(statusAfterPartialFill->remainingQuantity == 10);

    // Simulate full fill
    oms::core::ExecutionReport fullFillReport;
    fullFillReport.clientOrderId = newOrder.clientOrderId;
    fullFillReport.brokerOrderId = newOrder.brokerOrderId;
    fullFillReport.symbol = newOrder.symbol;
    fullFillReport.status = oms::core::OrderStatus::FILLED;
    fullFillReport.side = newOrder.side;
    fullFillReport.quantityOrdered = newOrder.quantity;
    fullFillReport.quantityFilledThisExecution = 10; // The remaining quantity
    fullFillReport.cumulativeQuantityFilled = 20;    // Total quantity
    fullFillReport.lastExecutedPrice = 3501.0;       // Potentially different price for the second fill
    fullFillReport.averageFillPrice = 3500.5;        // Average price for all fills
    fullFillReport.timestamp = std::chrono::system_clock::now();
    mockBroker->simulateExecutionReport(fullFillReport);

    auto statusAfterFullFill = orderManager.getOrderStatus(newOrder.clientOrderId);
    assert(statusAfterFullFill.has_value());
    assert(statusAfterFullFill->status == oms::core::OrderStatus::FILLED);
    assert(statusAfterFullFill->filledQuantity == 20);
    assert(statusAfterFullFill->remainingQuantity == 0);
    std::cout << "testExecutionReportProcessing_Fill: PASSED (Partial and Full)" << std::endl;
}

int main() {
    testOrderSubmission();
    testOrderCancellation();
    testExecutionReportProcessing_Fill();

    std::cout << "All OrderManager tests passed." << std::endl;
    return 0; // Success
}
