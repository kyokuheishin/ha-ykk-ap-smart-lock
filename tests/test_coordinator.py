"""Regression checks for the registration transaction boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


COMPONENT = Path(__file__).parents[1] / "custom_components" / "ykkap_smart_lock"
PACKAGE = "_ykkap_coordinator_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package

homeassistant = types.ModuleType("homeassistant")
components = types.ModuleType("homeassistant.components")
bluetooth = types.ModuleType("homeassistant.components.bluetooth")
bluetooth.async_last_service_info = lambda *args, **kwargs: None
components.bluetooth = bluetooth
config_entries = types.ModuleType("homeassistant.config_entries")
config_entries.ConfigEntry = object
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
util = types.ModuleType("homeassistant.util")
dt = types.ModuleType("homeassistant.util.dt")
dt.now = lambda: None
util.dt = dt
homeassistant.components = components
homeassistant.config_entries = config_entries
homeassistant.core = core
homeassistant.util = util
for name, module in {
    "homeassistant": homeassistant,
    "homeassistant.components": components,
    "homeassistant.components.bluetooth": bluetooth,
    "homeassistant.config_entries": config_entries,
    "homeassistant.core": core,
    "homeassistant.util": util,
    "homeassistant.util.dt": dt,
}.items():
    sys.modules[name] = module


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

ble = types.ModuleType(f"{PACKAGE}.ble")


class YKKApSmartLockConnectionError(Exception):
    pass


ble.YKKApSmartLockConnectionError = YKKApSmartLockConnectionError
ble.YKKApSmartLockBleClient = object
sys.modules[ble.__name__] = ble


class FakeClient:
    instances: list["FakeClient"] = []
    responses: dict[int, object] = {}

    def __init__(self, hass, address, name):
        del hass, address, name
        self.commands: list[int] = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb

    async def command(self, base, command, payload=b""):
        del base, payload
        self.commands.append(command)
        response = type(self).responses[command]
        if isinstance(response, Exception):
            raise response
        return response


coordinator = _load("coordinator")
coordinator.YKKApSmartLockBleClient = FakeClient


def _response(command: int, payload: bytes):
    return protocol.parse_frame(
        protocol.build_frame(
            const.BASE_SETTINGS, command, payload, request=False
        )
    )


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.instances.clear()
        FakeClient.responses = {}

    def test_missing_identity_does_not_request_smartphone_slot(self) -> None:
        FakeClient.responses = {const.CMD_REQUEST_GENERAL_LOCK_ID: _response(0x52, b"")}

        with self.assertRaises(coordinator.YKKApSmartLockRegistrationError):
            asyncio.run(
                coordinator.async_register_general_device(
                    object(), "AA:BB:CC:DD:EE:FF", "lock", request_adv_key=False
                )
            )

        self.assertEqual(FakeClient.instances[0].commands, [0x52])

    def test_exit_failure_keeps_completed_registration(self) -> None:
        FakeClient.responses = {
            const.CMD_REQUEST_GENERAL_LOCK_ID: _response(0x52, b"12A34" b"1234"),
            const.CMD_REQUEST_GENERAL_SMARTPHONE_ID: _response(0x51, b"\x01"),
            const.CMD_EXIT_GENERAL_REGISTRATION: coordinator.YKKApSmartLockConnectionError(
                "connection lost"
            ),
        }

        with self.assertLogs(coordinator._LOGGER, level="WARNING"):
            result = asyncio.run(
                coordinator.async_register_general_device(
                    object(), "AA:BB:CC:DD:EE:FF", "lock", request_adv_key=False
                )
            )

        self.assertEqual(result[const.CONF_SMARTPHONE_ID], 1)
        self.assertEqual(result[const.CONF_LOT_NUMBER], "12A34")
        self.assertEqual(result[const.CONF_SERIAL_NUMBER], 1234)
        self.assertEqual(FakeClient.instances[0].commands, [0x52, 0x51, 0x54])


if __name__ == "__main__":
    unittest.main()
