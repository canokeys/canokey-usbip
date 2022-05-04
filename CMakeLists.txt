cmake_minimum_required(VERSION 3.7)
project(canokey-usbip C)

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
target_compile_options(canokey-usbip PRIVATE "-fsanitize=address")
target_compile_definitions(canokey-usbip PRIVATE HW_VARIANT_NAME="CanoKey USB/IP")
target_link_libraries(canokey-usbip general canokey-core "-fsanitize=address")
add_dependencies(canokey-usbip gitrev)
