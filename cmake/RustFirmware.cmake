# SPDX-License-Identifier: Apache-2.0
set(CANOKEY_VERSIONS_FILE "${CANOKEY_CORE_DIR}/versions.cmake")
include("${CANOKEY_VERSIONS_FILE}")
if(NOT CANOKEY_FIRMWARE_VERSION STREQUAL CANOKEY_ADMIN_VERSION)
  message(FATAL_ERROR "Rust firmware identity must match ${CANOKEY_ADMIN_VERSION}")
endif()
set(CANOKEY_VIRTUAL_CARD OFF CACHE BOOL "USB/IP owns virtual hardware" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "Caller owns applet regression execution" FORCE)
add_subdirectory("${CANOKEY_CORE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/canokey-core" EXCLUDE_FROM_ALL)
find_package(Threads REQUIRED)
find_package(OpenSSL REQUIRED)
find_program(CARGO cargo REQUIRED)
set(host_archive "${CMAKE_CURRENT_BINARY_DIR}/rust-usbip/release/libcanokey_rust_host.a")
add_custom_target(rust-usbip-build
  COMMAND "${CMAKE_COMMAND}" -E env
    "CANOKEY_ADMIN_VERSION=${CANOKEY_ADMIN_VERSION}" "CANOKEY_CORE_SHA=${CANOKEY_CORE_SHA}"
    "CANOKEY_OATH_VERSION=${CANOKEY_OATH_VERSION}" "CANOKEY_PIV_VERSION=${CANOKEY_PIV_VERSION}"
    "CANOKEY_FIDO_FIRMWARE_VERSION=${CANOKEY_FIDO_FIRMWARE_VERSION}"
    "CANOKEY_CTAPHID_DEVICE_VERSION=${CANOKEY_CTAPHID_DEVICE_VERSION}" "CANOKEY_USB_BCD_DEVICE=${CANOKEY_USB_BCD_DEVICE}"
    ${CARGO} +nightly-2026-09-04 build --manifest-path "${CANOKEY_CORE_DIR}/crates/host/Cargo.toml"
    --target-dir "${CMAKE_CURRENT_BINARY_DIR}/rust-usbip" --release --features usbip
  BYPRODUCTS "${host_archive}" VERBATIM)
add_library(canokey-rust-usbip STATIC IMPORTED)
set_target_properties(canokey-rust-usbip PROPERTIES IMPORTED_LOCATION "${host_archive}")
add_dependencies(canokey-rust-usbip rust-usbip-build)
add_executable(canokey-usbip Src/usbip.c "${CANOKEY_CORE_DIR}/crates/host/native/crypto.c")
target_compile_definitions(canokey-usbip PRIVATE CANOKEY_RUST_USB CANOKEY_USB_STATIC_LAYOUT
  ENABLE_IFACE_CTAPHID=1 CANOKEY_USB_BCD_DEVICE=${CANOKEY_USB_BCD_DEVICE})
target_include_directories(canokey-usbip PRIVATE "${CANOKEY_CORE_DIR}/native/include")
target_compile_options(canokey-usbip PRIVATE -UNDEBUG)
target_link_libraries(canokey-usbip PRIVATE canokey-rust-usbip host-key-services OpenSSL::Crypto
  Threads::Threads ${CMAKE_DL_LIBS} m)
