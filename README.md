# CanoKey virtual hardware integration test infrastructure

`canokey-usbip` is CanoKey's shared virtual hardware test platform. It builds a selected
`canokey-core`, exposes its real USB descriptors and CCID/HID/WebUSB interfaces through Linux
USB/IP, runs a caller-owned test command, collects diagnostics, and always detaches and stops the
device.

Application behavior tests stay in their owning repositories:

- `canokey-pkcs11` owns PKCS#11 and PIV object/signing tests.
- `canokey-manager` owns Manager CLI and applet scenarios.
- `canokey-console` owns Console and WebUSB protocol scenarios.
- `canokey-core` owns firmware behavior tests.

This repository owns firmware selection, virtual device build/storage, USB/IP lifecycle,
readiness, logging, timeout, and cleanup. It is not a CanoKey integration-test monorepo.

## Local Linux quick start

```bash
./compat/run \
  --core-ref 3.0.1 \
  --test-command 'python3 -c "print(\"test\")"'
```

An infrastructure smoke command that checks both USB and PC/SC is:

```bash
./compat/run \
  --core-ref 3.0.1 \
  --test-command 'lsusb -d "$CANOKEY_USB_VID:$CANOKEY_USB_PID" && test -n "$CANOKEY_PCSC_READER"'
```

The command must run on Linux with USB/IP host support. `compat/run` performs `modprobe`, start,
attach, polling, test execution, detach, and process cleanup; callers must not duplicate those
steps.

## CLI

```text
./compat/run [--core-ref REF | --core-dir DIR]
             --test-command COMMAND
             [--require {usb,ccid,hid,webusb,pcsc}]...
             [--storage FILE]
             [--timeout SECONDS]
             [--keep-on-failure]
             [--touch]
             [--output-dir DIR]
```

`--core-ref` fetches a tag, branch, or commit from `canokeys/canokey-core`, including recursive
submodules. An invalid ref fails before build. `--core-dir` snapshots an existing source tree into
the isolated run workspace, including local source edits but excluding Git/build metadata. This is
the mode for a `canokey-core` PR checkout. With neither option, the repository submodule is used.

Each run gets `/tmp/canokey-usbip/<run-id>/device.lfs` and a fresh device by default. An explicit
`--storage` path is never deleted, enabling provisioning and upgrade tests:

```bash
./compat/run --core-ref 3.0.0 --storage "$PWD/device.lfs" --test-command ./provision.sh
./compat/run --core-ref 3.0.1 --storage "$PWD/device.lfs" --test-command ./verify.sh
```

`--timeout` applies independently to readiness and the caller command; command timeout returns
124. The caller command's nonzero code is otherwise returned unchanged. Cleanup errors fail an
otherwise successful run but never replace an existing caller failure code. `--keep-on-failure`
preserves the isolated source/build/storage workspace; artifacts are always preserved.

`--touch` enables firmware user-presence checks. In that mode a caller can invoke
`"$CANOKEY_DEVICE_TOUCH"` when its scenario expects a touch. Without `--touch`, the legacy virtual
device behavior emulates NFC and skips user-presence waits.

Callers that only need selected device layers can repeat `--require` to define readiness explicitly:

```bash
./compat/run \
  --core-ref 3.0.1 \
  --require usb \
  --require ccid \
  --require pcsc \
  --test-command './ckman-smoke.sh'
```

With one or more `--require` options, only the named layers determine readiness. `hid` requires both
the HID interface and its hidraw device; `pcsc` requires pcsc-lite and a reader added by the current
attachment. With no `--require` options, the original full readiness checks remain in effect.

Build without attaching hardware:

```bash
./compat/build --core-dir /workspace/canokey-core
```

Direct CMake also supports external core trees without changing the submodule pointer:

```bash
cmake -S . -B build \
  -DCANOKEY_CORE_DIR=/workspace/canokey-core \
  -DCANOKEY_FIRMWARE_VERSION=3.0.1
cmake --build build --target canokey-usbip
```

Use `compat/run` or `compat/build` for firmware 1.3; direct CMake does not apply historical
core compatibility patches.

## Public test environment

`--test-command` runs from the directory where `compat/run` was invoked with these stable variables:

| Variable | Meaning |
| --- | --- |
| `CANOKEY_USBIP=1` | The command is running against virtual USB/IP hardware. |
| `CANOKEY_CORE_REF` | Requested ref, `external`, or `submodule`. |
| `CANOKEY_CORE_SHA` | Exact core commit used for the build. |
| `CANOKEY_FIRMWARE_VERSION` | Firmware release version reported by the virtual admin applet. |
| `CANOKEY_USBIP_SHA` | Exact harness commit. |
| `CANOKEY_USB_VID` | Four-digit USB vendor ID selected from the core descriptor. |
| `CANOKEY_USB_PID` | Four-digit USB product ID selected from the core descriptor. |
| `CANOKEY_STORAGE` | LittleFS device image in use. |
| `CANOKEY_TEST_OUTPUT` | Artifact directory for caller results. |
| `CANOKEY_PCSC_READER` | PC/SC reader name, when detected. |
| `CANOKEY_USB_BUS` | Enumerated Linux USB bus number. |
| `CANOKEY_USB_DEVICE` | Enumerated Linux USB device number. |
| `CANOKEY_DEVICE_RESTART` | Helper that restarts the built device and reruns readiness. |
| `CANOKEY_DEVICE_TOUCH` | Helper that sends one simulated touch. |

Use `"$CANOKEY_DEVICE_RESTART"` between destructive scenarios to reuse one firmware build. The
same storage is mounted after restart.

## Readiness and artifacts

There are no fixed readiness sleeps. The runner reads VID/PID from the selected core and polls for
that device, CCID class `0b`, HID class `03`, vendor/WebUSB class `ff`, hidraw, and, when pcsc-lite
is present, the PC/SC reader added by this attachment.
Timeout errors list which layers were present. All layer states are collected for diagnostics even
when explicit `--require` options select only some of them.

Every run writes the chosen `--output-dir` (default `artifacts/<UTC timestamp>/`):

```text
metadata.json       exact SHAs, toolchain, kernel, storage, command, result
build.log           CMake configure/build output
usbip.log           virtual device server output
usbip-port.txt      attached vhci port state
lsusb.txt           descriptor diagnostics
pcscd.log           recent pcscd journal, when available
udev.txt            enumerated device properties, when available
test.stdout         caller command stdout
test.stderr         caller command stderr
```

Set `CANOKEY_USBIP_DEBUG=1` to also print collected host diagnostics in the job log. Test wrappers
must not print PINs, private keys, or management keys. Any default credential used here is test-only;
the harness never reads secrets from a physical device. Firmware debug output is disabled by default
because historical core versions may print generated test key material.

## Firmware profiles

The catalog contains only refs that were clone/submodule/build/link verified with this host layer.
It stores USB infrastructure capabilities, not applet semantics such as algorithms or credential
management.

```bash
./compat/list-firmwares --profile smoke
./compat/list-firmwares --profile nightly --compact
./compat/list-firmwares --release-map
```

`smoke` currently expands to oldest supported, latest release, and `master`. `nightly` expands to
every verified release plus `master`. The compact form can be written directly to a GitHub matrix
output. See `compat/config/firmwares.yaml` for the verified SHA and build adapter of each ref.

`--release-map` emits the maintained mapping from shipped firmware version to the exact
`canokey-core` commit recorded by that firmware. It is deliberately separate from build support:
firmware and core tag version numbers must not be assumed to match. For example, firmware `1.6.1`
maps to core tag `1.6.0`. The `1.3` commit predates the harness platform source interface
(`virt-card/device-sim.c`), so the runner applies the repository-owned
`core-1.3-legacy-device-sim.patch` only to its isolated core snapshot.

## GitHub Actions

The reusable workflow checks the caller's current event revision into `caller/` and checks this
harness into a separate directory. This is required because invoking a reusable workflow does not
make the called repository's files the job workspace.

### Host application CI

This example tests the current `canokey-pkcs11` PR against the smoke firmware set. The application
test scripts remain in `canokey-pkcs11`:

```yaml
jobs:
  firmware-matrix:
    strategy:
      fail-fast: false
      matrix:
        firmware:
          - { id: oldest, ref: "5f1e95f8341856d994abb4566995e2379cc0612d" }
          - { id: latest, ref: "69e562bcb07eedda015aae6064870c8548571e2b" }
          - { id: head, ref: "master" }
    uses: canokeys/canokey-usbip/.github/workflows/usbip-integration.yml@master
    with:
      core-ref: ${{ matrix.firmware.ref }}
      test-command: ./tests/usbip/run.sh
      artifact-name: canokey-pkcs11-${{ matrix.firmware.id }}
```

This evaluates `canokey-pkcs11 HEAD x multiple released/current firmware versions` without moving
PKCS#11 assertions into this repository.

To consume the maintained profile instead of repeating refs, generate the matrix in an ordinary
hosted job (this job does not need USB privileges), then pass each item to the reusable workflow:

```yaml
jobs:
  catalog:
    runs-on: ubuntu-latest
    outputs:
      firmwares: ${{ steps.matrix.outputs.firmwares }}
    steps:
      - uses: actions/checkout@v4
        with:
          repository: canokeys/canokey-usbip
          path: harness
      - id: matrix
        run: echo "firmwares=$(./harness/compat/list-firmwares --profile smoke --compact)" >> "$GITHUB_OUTPUT"
  usbip:
    needs: catalog
    strategy:
      matrix:
        firmware: ${{ fromJSON(needs.catalog.outputs.firmwares) }}
    uses: canokeys/canokey-usbip/.github/workflows/usbip-integration.yml@master
    with:
      core-ref: ${{ matrix.firmware.ref }}
      test-command: ./tests/usbip/run.sh
      artifact-name: canokey-pkcs11-${{ matrix.firmware.id }}
```

### canokey-core PR CI

Set `core-dir: .` to build the caller's current PR checkout. A core-owned wrapper may download or
build released Host software before running its integration checks:

```yaml
jobs:
  released-host-compat:
    uses: canokeys/canokey-usbip/.github/workflows/usbip-integration.yml@master
    with:
      core-dir: .
      caller-submodules: true
      test-command: ./tests/host-compat/run-released-pkcs11.sh
      artifact-name: core-pr-released-pkcs11
```

The harness treats that wrapper as opaque. Host release selection and business assertions remain
with the caller/workflow, while the virtual hardware lifecycle remains centralized here.

## Linux runner requirements

Supported host: Linux only. The runner needs:

- `usbip`, a kernel-matched `vhci_hcd` module, `/sys/bus/usb/devices`, and usbfs at `/dev/bus/usb`;
- `pcscd`, `libccid`, pcsc-lite, and preferably `pcsc_scan`;
- `udev`, `hidraw`, `lsusb`, Git, CMake, a C compiler, Bash, Python 3, and the Python `jinja2`/`jsonschema` packages required by current `canokey-core` development builds;
- passwordless privilege for `modprobe vhci_hcd`, `usbip attach/detach`, and starting pcscd.

Run `./compat/environment` before CI registration. The reusable workflow defaults to labels
`[self-hosted, linux, canokey-usbip]`. It can install Ubuntu user-space packages, but cannot add a
missing kernel module. GitHub-hosted runner USB/IP attach has not been verified by this repository,
so no hosted-runner example is claimed to work. A Linux VM or self-hosted runner is the supported
baseline; privileged Docker is not required.

One runner supports one active virtual CanoKey environment. Firmware parallelism belongs in
separate GitHub matrix jobs, not concurrent background devices in one job.

A setup-only composite action is intentionally not provided: composite actions cannot guarantee an
`always()` teardown after arbitrary later caller steps. The reusable workflow owns the test command
and therefore owns reliable cleanup and artifact upload as one lifecycle.

## Legacy server CLI

The low-level executable remains available for development:

```text
canokey-usbip [canokey-file [port [touch]]]
```

It does not attach or clean up the host. Integration users should use `compat/run`.

Implementation details and runner tests are documented in `compat/README.md`.
