#include "Order.h"
#include <stdexcept> // For std::logic_error

namespace oms {
namespace core {

// Method to update order quantities based on a new fill
// This is a simplified version; a more robust implementation might also handle average fill price.
void Order::updateQuantities(double quantityFilledInThisExecution) {
    if (quantityFilledInThisExecution < 0) {
        // Or throw an exception, log an error, etc.
        return;
    }
    this->filledQuantity += quantityFilledInThisExecution;
    this->remainingQuantity = this->quantity - this->filledQuantity;

    if (this->remainingQuantity < 0) {
        // This indicates an overfill or an issue with quantity tracking.
        // Handle appropriately (e.g., log error, cap remainingQuantity at 0).
        this->remainingQuantity = 0;
    }
    this->lastUpdateTimestamp = std::chrono::system_clock::now();
}

bool Order::isTerminal() const {
    return status == OrderStatus::FILLED ||
           status == OrderStatus::CANCELLED ||
           status == OrderStatus::REJECTED ||
           status == OrderStatus::EXPIRED;
}

} // namespace core
} // namespace oms
