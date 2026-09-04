// SPDX-License-Identifier: Apache-2.0
#if defined(CANOKEY_ADMIN_VENDOR_NFC_ENABLE_WITH_PIN) || defined(CANOKEY_ADMIN_VENDOR_NFC_ENABLE_LEGACY)

#include <admin.h>
#include <apdu.h>
#include <stdbool.h>

static bool nfc_enabled = true;

static int admin_vendor_nfc_enable_impl(const CAPDU *capdu, RAPDU *rapdu, bool pin_validated) {
  if (P1 != 0x00 && P1 != 0x01) EXCEPT(SW_WRONG_P1P2);
  if (P2 != 0x00 && P2 != 0x01) EXCEPT(SW_WRONG_P1P2);
  if (LC != 0x00) EXCEPT(SW_WRONG_LENGTH);

  if (P1 == 0x01 && !pin_validated) EXCEPT(SW_SECURITY_STATUS_NOT_SATISFIED);

  if (P1 == 0x00) {
    RDATA[0] = nfc_enabled;
    LL = 1;
  } else {
    nfc_enabled = P2;
  }

  return 0;
}

#ifdef CANOKEY_ADMIN_VENDOR_NFC_ENABLE_WITH_PIN
int admin_vendor_nfc_enable(const CAPDU *capdu, RAPDU *rapdu, bool pin_validated) {
  return admin_vendor_nfc_enable_impl(capdu, rapdu, pin_validated);
}
#else
int admin_vendor_nfc_enable(const CAPDU *capdu, RAPDU *rapdu) {
  // Legacy cores dispatch this command only after their common admin PIN gate.
  return admin_vendor_nfc_enable_impl(capdu, rapdu, true);
}
#endif

#endif
