cmake_minimum_required(VERSION 3.13)
project(canokey-usbip C)

option(ENABLE_TESTS "Build for tests" OFF)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wall")
set(CMAKE_C_FLAGS_DEBUG "${CMAKE_C_FLAGS_DEBUG} -fno-omit-frame-pointer")

set(VIRTCARD ON CACHE BOOL "Enable VIRTCARD in core" FORCE)
set(USBD_PRODUCT_STRING "CanoKey USB/IP")
add_subdirectory(canokey-core EXCLUDE_FROM_ALL)

file(GLOB_RECURSE SRC Src/*.c)

add_executable(canokey-usbip ${SRC}
        canokey-core/virt-card/device-sim.c
        canokey-core/virt-card/fabrication.c
        canokey-core/littlefs/bd/lfs_filebd.c)
target_include_directories(canokey-usbip SYSTEM PRIVATE canokey-core/virt-card canokey-core)
if (ENABLE_TESTS)
        target_compile_options(canokey-usbip PRIVATE "--coverage" "-fsanitize=address" "-fsanitize=undefined")
        target_link_options(canokey-usbip PRIVATE "--coverage" "-fsanitize=address" "-fsanitize=undefined")
endif ()
target_compile_definitions(canokey-usbip PRIVATE HW_VARIANT_NAME="CanoKey USB/IP")
target_link_libraries(canokey-usbip canokey-core)
add_dependencies(canokey-usbip gitrev)
