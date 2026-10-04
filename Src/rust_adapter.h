/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "usb_io.h"
#include "core.h"
#include <stdbool.h>
#define USBD_EP_TYPE_CTRL 0x00
#define USBD_EP_TYPE_BULK 0x02
#define USBD_EP_TYPE_INTR 0x03
#define USBD_VID 0x20A0
#define USBD_PID 0x42D4
#define USBD_MAX_NUM_CONFIGURATION 1
#define HI(value) (((value) >> 8) & 0xFF)
#define LO(value) ((value) & 0xFF)
int ck_host_usbip_open(const char *path, uint8_t touch);
void ck_host_usbip_loop(void);
void ck_usb_boot_reset(void);
uint32_t device_get_tick(void);
int write_exact(int fd, const uint8_t *bytes, size_t length);
int read_exact(int fd, uint8_t *bytes, size_t length);
/* Legacy packet helpers below remain compiled for the historical backend. */
#define usb_device 0
#define USBD_LL_DataOutStage(device, ep, bytes) rust_out(ep, bytes)
