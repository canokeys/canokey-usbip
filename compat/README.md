# Compatibility infrastructure

`compat/run` is the stable entry point for caller-owned integration tests. It selects or snapshots
`canokey-core`, builds the virtual firmware, creates an isolated LittleFS image, owns the USB/IP
attach/readiness/cleanup lifecycle, and runs one opaque test command.

It deliberately does not contain PKCS#11, Manager, Console, PIV, OATH, OpenPGP, or FIDO business
test suites. Those remain in the caller repository.

## Files

- `run`: complete build/device/test lifecycle.
- `build`: isolated build only; prints the resulting executable path.
- `environment`: checks Linux host prerequisites.
- `list-firmwares`: emits profiles, the supported catalog, or the firmware/core release map without
  third-party Python packages.
- `config/firmwares.yaml`: historical firmware/core mappings plus build-verified refs and USB-level
  metadata used by profiles.
- `config/profiles.yaml`: smoke/nightly firmware selection.
- `scripts/`: lifecycle controls used by a running caller test command.
- `tests/`: hardware-independent runner tests.

The `.yaml` configuration files use JSON syntax, which is a strict YAML subset. This keeps the
matrix helper dependency-free.

Firmware 1.3 uses a build-only compatibility patch from `patches/`. It is allowlisted by exact core
SHA and applied after the core has been cloned or copied into the isolated run workspace. The
caller's core checkout and the canokey-core submodule are never patched in place.

## Lifecycle contract

Only one environment may run on a Linux host at once. A global lock rejects accidental overlap.
By default, readiness requires the selected core's USB VID/PID, CCID (`0b`), HID (`03`),
vendor/WebUSB (`ff`), and hidraw interfaces. If pcsc-lite is installed, the PC/SC reader added by
the current attachment is also required. Callers can repeat `--require` with `usb`, `ccid`, `hid`,
`webusb`, or `pcsc` to make only those layers determine readiness. All probes are still reported,
and a timeout reports the state of each probe rather than relying on a fixed sleep.

The Linux host preflight also requires usbfs device nodes at `/dev/bus/usb`; sysfs enumeration alone
is insufficient for libccid, pcscd, and libusb traffic.

Inside `--test-command`, execute `$CANOKEY_DEVICE_RESTART` to detach, restart the already-built
firmware with the same storage, reattach, and rerun readiness probes. Execute
`$CANOKEY_DEVICE_TOUCH` to simulate touch. The outer runner still performs final cleanup.

## Testing

```bash
python3 -m unittest discover -s compat/tests -v
```

These tests mock only host USB state. Release support in `firmwares.yaml` is based on real
clone/submodule/build/link runs, not these mocks.
