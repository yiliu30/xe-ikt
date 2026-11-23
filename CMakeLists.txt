cmake_minimum_required(VERSION 3.20)
project(ikt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(IKT_DEVICE "bmg-g31" CACHE STRING "SPIR-V Gen device target")

add_executable(ikt_regions examples/regions.cpp)
target_include_directories(ikt_regions PRIVATE include)
target_compile_options(ikt_regions PRIVATE
  -O3 -fsycl -fsycl-targets=spir64_gen)
target_link_options(ikt_regions PRIVATE
  -fsycl -fsycl-targets=spir64_gen
  -Xsycl-target-backend "-device ${IKT_DEVICE}")

add_executable(ikt_iket_api examples/iket_api.cpp)
target_include_directories(ikt_iket_api PRIVATE include)
target_compile_options(ikt_iket_api PRIVATE
  -O3 -fsycl -fsycl-targets=spir64_gen)
target_link_options(ikt_iket_api PRIVATE
  -fsycl -fsycl-targets=spir64_gen
  -Xsycl-target-backend "-device ${IKT_DEVICE}")
