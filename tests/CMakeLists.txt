cmake_minimum_required(VERSION 3.15)
project(QuantitativeOMSTests)

add_executable(OrderManager_test
    OrderManager_test.cpp
)

# Link against the core OMS library
target_link_libraries(OrderManager_test PRIVATE oms_core)

# Add test to CTest
add_test(NAME OrderManagerBasicTest COMMAND OrderManager_test)

# If using GoogleTest or Catch2, integration would be different here.
