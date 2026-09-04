// SPDX-License-Identifier: Apache-2.0
#include <admin.h>
#include <apdu.h>
#include <applets.h>
#include <device.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int card_fs_init(const char *lfs_root);

static void fail(const char *message, unsigned int got, unsigned int expected) {
  fprintf(stderr, "%s: got 0x%04x, expected 0x%04x\n", message, got, expected);
  exit(1);
}

static void expect_equal(const char *message, unsigned int got, unsigned int expected) {
  if (got != expected) fail(message, got, expected);
}

static RAPDU send_admin(uint8_t ins, uint8_t p1, uint8_t p2, const uint8_t *data, uint16_t lc, uint32_t le,
                        uint8_t *response) {
  CAPDU capdu = {.data = (uint8_t *)data, .cla = 0x00, .ins = ins, .p1 = p1, .p2 = p2, .lc = lc, .le = le};
  RAPDU rapdu = {.data = response};
  expect_equal("admin_process_apdu return value", admin_process_apdu(&capdu, &rapdu), 0);
  return rapdu;
}

static uint8_t read_nfc_enabled(void) {
  uint8_t response[1] = {0xff};
  const RAPDU rapdu = send_admin(ADMIN_INS_NFC_ENABLE, 0x00, 0x00, NULL, 0, 1, response);
  expect_equal("read status", rapdu.sw, SW_NO_ERROR);
  expect_equal("read response length", rapdu.len, 1);
  return response[0];
}

static void expect_write_status(uint8_t enabled, uint16_t expected_sw) {
  uint8_t response[1];
  const RAPDU rapdu = send_admin(ADMIN_INS_NFC_ENABLE, 0x01, enabled, NULL, 0, 0, response);
  expect_equal("write status", rapdu.sw, expected_sw);
  expect_equal("write response length", rapdu.len, 0);
}

int main(void) {
  char storage[] = "/tmp/canokey-usbip-nfc-XXXXXX";
  const int fd = mkstemp(storage);
  if (fd < 0) {
    perror("mkstemp");
    return 1;
  }
  close(fd);
  unlink(storage);

  expect_equal("card_fs_init", card_fs_init(storage), 0);
  init_apdu_buffer();
  device_init();
  expect_equal("applets_install", applets_install(), 0);

  admin_poweroff();
  expect_equal("default enabled state", read_nfc_enabled(), 1);
  expect_write_status(0, SW_SECURITY_STATUS_NOT_SATISFIED);

  uint8_t response[1];
  static const uint8_t default_pin[] = "123456";
  RAPDU rapdu = send_admin(ADMIN_INS_VERIFY, 0x00, 0x00, default_pin, sizeof(default_pin) - 1, 0, response);
  expect_equal("PIN verification status", rapdu.sw, SW_NO_ERROR);

  expect_write_status(0, SW_NO_ERROR);
  expect_equal("disabled state", read_nfc_enabled(), 0);
  set_nfc_state(1); // The main program uses this value when --touch is absent.
  expect_equal("NFC-mode touch simulation does not enable NFC", read_nfc_enabled(), 0);

  expect_write_status(1, SW_NO_ERROR);
  set_nfc_state(0); // The main program uses this value when --touch is present.
  expect_equal("touch mode does not disable NFC", read_nfc_enabled(), 1);

  rapdu = send_admin(ADMIN_INS_NFC_ENABLE, 0x02, 0x00, NULL, 0, 0, response);
  expect_equal("invalid P1 status", rapdu.sw, SW_WRONG_P1P2);
  rapdu = send_admin(ADMIN_INS_NFC_ENABLE, 0x00, 0x02, NULL, 0, 0, response);
  expect_equal("invalid P2 status", rapdu.sw, SW_WRONG_P1P2);
  rapdu = send_admin(ADMIN_INS_NFC_ENABLE, 0x00, 0x00, default_pin, 1, 0, response);
  expect_equal("invalid Lc status", rapdu.sw, SW_WRONG_LENGTH);

  unlink(storage);
  char config_path[sizeof(storage) + sizeof(".config")];
  snprintf(config_path, sizeof(config_path), "%s.config", storage);
  unlink(config_path);
  return 0;
}
