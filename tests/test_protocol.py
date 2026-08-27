"""Regression checks for the dependency-free protocol helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


COMPONENT = Path(__file__).parents[1] / "custom_components" / "ykkap_smart_lock"
PACKAGE = "_ykkap_protocol_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", COMPONENT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


const = _load("const")
protocol = _load("protocol")


class ProtocolTests(unittest.TestCase):
    def test_general_smartphone_request_passes_zero_id(self) -> None:
        self.assertEqual(
            protocol.build_frame(const.BASE_SETTINGS, 0x51, b"\x00"),
            bytes.fromhex("8351006ce7"),
        )

    def test_registration_response_lengths_match_apk_fields(self) -> None:
        self.assertEqual(protocol.response_length(const.BASE_SETTINGS, 0x51), 15)
        self.assertEqual(protocol.response_length(const.BASE_SETTINGS, 0x23), 34)

    def test_decode_lock_name(self) -> None:
        payload = "玄関".encode("utf-16-be").ljust(30, b"\x00")
        self.assertEqual(protocol.decode_lock_name(payload), "玄関")

    def test_decode_advertisement_state(self) -> None:
        key = bytes(range(16))
        product_code = 0x42
        plaintext = bytearray((0, product_code, const.LOCKED, *range(3, 16)))
        plaintext[0] = 0
        for value in plaintext[1:16]:
            plaintext[0] ^= value
        encryptor = Cipher(
            algorithms.AES(key), modes.CBC(bytes(16))
        ).encryptor()
        encrypted = encryptor.update(bytes(plaintext)) + encryptor.finalize()
        advertisement = bytes((product_code, 0, 0, 0, 0, 1)) + encrypted

        self.assertEqual(protocol.decode_advertisement_state(advertisement, key.hex()), 1)
        self.assertEqual(
            protocol.decode_advertisement_state(advertisement + b"\xaa\x55", key), 1
        )
        self.assertEqual(
            protocol.decode_advertisement_state(b"\x9d\x09" + advertisement, key), 1
        )
        with self.assertRaises(protocol.YKKApSmartLockProtocolError):
            protocol.decode_advertisement_state(advertisement[:-1], key)
        invalid_plaintext = bytes((plaintext[0] ^ 1, *plaintext[1:]))
        invalid_encryptor = Cipher(
            algorithms.AES(key), modes.CBC(bytes(16))
        ).encryptor()
        invalid_encrypted = (
            invalid_encryptor.update(invalid_plaintext)
            + invalid_encryptor.finalize()
        )
        with self.assertRaises(protocol.YKKApSmartLockProtocolError):
            protocol.decode_advertisement_state(
                bytes((product_code, 0, 0, 0, 0, 1)) + invalid_encrypted, key
            )


if __name__ == "__main__":
    unittest.main()
