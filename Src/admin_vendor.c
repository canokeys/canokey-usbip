// SPDX-License-Identifier: Apache-2.0
#ifdef CANOKEY_ADMIN_VENDOR_NFC_ENABLE

#include <admin.h>
#include <apdu.h>
#include <stdbool.h>

static bool nfc_enabled = true;

int admin_vendor_nfc_enable(const CAPDU *capdu, RAPDU *rapdu, bool pin_validated) {
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

#endif
