cmake_minimum_required(VERSION 3.15)
project(QuantitativeOMS VERSION 1.0
 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED True)
set(CMAKE_CXX_EXTENSIONS OFF)

message(STATUS "Project: ${PROJECT_NAME}")
message(STATUS "CXX Standard: ${CMAKE_CXX_STANDARD}")

add_subdirectory(src)

# Enable testing with CTest (optional, add GoogleTest or Catch2 later)
enable_testing()
add_subdirectory(tests) # Uncommented to enable tests
