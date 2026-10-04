#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the Rust USB/IP CCID path without kernel modules or physical devices."""
import argparse
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time

EP_CCID = 0x03
CCID_HEADER_BYTES = 10
USBIP_HEADER_BYTES = 48
USBIP_SPEED_FULL = 0x02
LIBCCID_RECEIVE_BYTES = 65554


class Device:
    def __init__(self, port):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=15)
        self.sequence = 0
        self.ccid_sequence = 0
        # USB/IP 1.1 OP_REQ_IMPORT with bus ID 1-1.
        self.socket.sendall(bytes.fromhex('0111800300000000') + b'1-1'.ljust(32, b'\0'))
        assert self.read(8) == bytes.fromhex('0111000300000000')
        imported = self.read(312)
        assert int.from_bytes(imported[296:300], 'big') == USBIP_SPEED_FULL
        self.transfer(0, False, b'', bytes.fromhex('0005010000000000'))
        self.transfer(0, False, b'', bytes.fromhex('0009010000000000'))

    def read(self, count):
        result = bytearray()
        while len(result) < count:
            chunk = self.socket.recv(count - len(result))
            if not chunk:
                raise EOFError('USB/IP server disconnected')
            result.extend(chunk)
        return bytes(result)

    def transfer(self, ep, incoming, data=b'', setup=b'\0' * 8, capacity=4096):
        self.sequence += 1
        length = capacity if incoming else len(data)
        header = struct.pack('!10I8s', 1, self.sequence, 0x10002, int(incoming), ep,
                             0, length, 0xFFFFFFFF, 0, 0, setup)
        self.socket.sendall(header + (b'' if incoming else data))
        response = struct.unpack('!10I8s', self.read(USBIP_HEADER_BYTES))
        assert response[0] == 3 and response[1] == self.sequence, response
        assert response[5] == 0, response
        return self.read(response[6]) if incoming else b''

    def command(self, kind, data=b''):
        sequence = self.ccid_sequence
        self.ccid_sequence = (sequence + 1) & 0xFF
        header = struct.pack('<BI5B', kind, len(data), 0, sequence, 0, 0, 0)
        self.transfer(EP_CCID, False, header + data)
        response = self.transfer(EP_CCID, True, capacity=LIBCCID_RECEIVE_BYTES)
        assert len(response) >= CCID_HEADER_BYTES
        size = int.from_bytes(response[1:5], 'little')
        assert response[6] == sequence and response[7] & 0xC0 == 0, response
        assert len(response) == size + CCID_HEADER_BYTES, response
        return response[CCID_HEADER_BYTES:]

    def apdu(self, command):
        return self.command(0x6F, command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rust-usbip-') as temporary:
        with socket.socket() as candidate:
            candidate.bind(('127.0.0.1', 0))
            port = candidate.getsockname()[1]
        with (Path(temporary) / 'server.log').open('w') as log:
            process = subprocess.Popen([str(args.binary.resolve()), str(Path(temporary) / 'image'),
                                        str(port), 'touch'], stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    try:
                        device = Device(port)
                        break
                    except ConnectionRefusedError:
                        if process.poll() is not None:
                            raise RuntimeError((Path(temporary) / 'server.log').read_text())
                        time.sleep(0.05)
                else:
                    raise TimeoutError('USB/IP readiness')
                assert device.command(0x62)
                assert device.apdu(bytes.fromhex('00a4040005f000000000')) == b'\x90\x00'
                assert device.apdu(bytes.fromhex('0031000000')) == b'4.0.0\x90\x00'
                assert device.apdu(bytes.fromhex('0020000006313233343536')) == b'\x90\x00'
                assert device.apdu(bytes.fromhex('0043000000')).endswith(b'\x90\x00')
                device.socket.close()
                print('Rust USB/IP: enumeration, CCID power, Admin identity/auth/PASS read passed')
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
