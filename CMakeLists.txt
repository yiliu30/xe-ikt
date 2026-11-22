cmake_minimum_required(VERSION 3.20)
project(ikp_xe LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(IKP_XE_DEVICE "bmg-g31" CACHE STRING "SPIR-V Gen device target")

add_executable(ikp_xe_regions examples/regions.cpp)
target_include_directories(ikp_xe_regions PRIVATE include)
target_compile_options(ikp_xe_regions PRIVATE
  -O3 -fsycl -fsycl-targets=spir64_gen)
target_link_options(ikp_xe_regions PRIVATE
  -fsycl -fsycl-targets=spir64_gen
  -Xsycl-target-backend "-device ${IKP_XE_DEVICE}")
