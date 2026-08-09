"""Regression checks for the dependency-free protocol helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


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
    def test_general_smartphone_request_is_header_and_crc_only(self) -> None:
        self.assertEqual(
            protocol.build_frame(const.BASE_SETTINGS, 0x51),
            bytes.fromhex("8351e6ef"),
        )

    def test_general_smartphone_response_length_is_variable(self) -> None:
        self.assertIsNone(protocol.response_length(const.BASE_SETTINGS, 0x51))


if __name__ == "__main__":
    unittest.main()
