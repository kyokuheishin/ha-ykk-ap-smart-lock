"""Regression checks for the registration transaction boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime
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
dt.now = lambda: datetime(2026, 1, 2, 3, 4)
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
    responses: dict[tuple[int, int], object] = {}

    def __init__(self, hass, address, name):
        del hass, address, name
        self.commands: list[int] = []
        self.calls: list[tuple[int, int, bytes]] = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb

    async def command(self, base, command, payload=b"", **kwargs):
        del kwargs
        self.commands.append(command)
        self.calls.append((base, command, payload))
        response = type(self).responses[(base, command)]
        if isinstance(response, Exception):
            raise response
        return response


coordinator = _load("coordinator")
coordinator.YKKApSmartLockBleClient = FakeClient


def _response(base: int, command: int, payload: bytes):
    return protocol.parse_frame(
        protocol.build_frame(
            base, command, payload, request=False
        )
    )


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.instances.clear()
        FakeClient.responses = {}

    def test_missing_identity_does_not_request_smartphone_slot(self) -> None:
        FakeClient.responses = {
            (const.BASE_SETTINGS, const.CMD_REQUEST_GENERAL_LOCK_ID): _response(
                const.BASE_SETTINGS, 0x52, b""
            )
        }

        with self.assertRaises(coordinator.YKKApSmartLockRegistrationError):
            asyncio.run(
                coordinator.async_register_general_device(
                    object(), "AA:BB:CC:DD:EE:FF", "lock", request_adv_key=False
                )
            )

        self.assertEqual(FakeClient.instances[0].commands, [0x52])

    def test_registration_uses_zero_id_without_exit_command(self) -> None:
        FakeClient.responses = {
            (const.BASE_SETTINGS, const.CMD_REQUEST_GENERAL_LOCK_ID): _response(
                const.BASE_SETTINGS, 0x52, b"12A34" b"1234"
            ),
            (const.BASE_SETTINGS, const.CMD_REQUEST_GENERAL_SMARTPHONE_ID): _response(
                const.BASE_SETTINGS, 0x51, b"\x01"
            ),
        }

        result = asyncio.run(
            coordinator.async_register_general_device(
                object(), "AA:BB:CC:DD:EE:FF", "lock", request_adv_key=False
            )
        )

        self.assertEqual(result[const.CONF_SMARTPHONE_ID], 1)
        self.assertEqual(result[const.CONF_LOT_NUMBER], "12A34")
        self.assertEqual(result[const.CONF_SERIAL_NUMBER], 1234)
        self.assertEqual(FakeClient.instances[0].commands, [0x52, 0x51])
        self.assertEqual(
            FakeClient.instances[0].calls[-1],
            (const.BASE_SETTINGS, const.CMD_REQUEST_GENERAL_SMARTPHONE_ID, b"\x00"),
        )

    def test_lock_payload_uses_identity_read_after_operation_lock(self) -> None:
        entry = types.SimpleNamespace(
            title="lock",
            data={
                const.CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
                const.CONF_SMARTPHONE_ID: 1,
                const.CONF_LOT_NUMBER: "12A34",
                const.CONF_SERIAL_NUMBER: 1234,
            },
        )
        lock_coordinator = coordinator.YKKApSmartLockCoordinator(
            object(), entry, lambda: None
        )
        FakeClient.responses = {
            (const.BASE_INFORMATION, const.CMD_SET_TIMESTAMP): _response(
                const.BASE_INFORMATION, const.CMD_SET_TIMESTAMP, b""
            ),
            (const.BASE_MAIN, const.CMD_LOCK_REQUEST): _response(
                const.BASE_MAIN, const.CMD_LOCK_REQUEST, bytes((const.LOCKED,))
            ),
        }

        async def run() -> None:
            await lock_coordinator._operation_lock.acquire()
            task = asyncio.create_task(
                lock_coordinator.async_set_lock_state(const.LOCKED)
            )
            try:
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                lock_coordinator._data = {
                    **lock_coordinator._data,
                    const.CONF_SMARTPHONE_ID: 2,
                    const.CONF_LOT_NUMBER: "98B76",
                    const.CONF_SERIAL_NUMBER: 5678,
                }
            finally:
                lock_coordinator._operation_lock.release()
            await task

        asyncio.run(run())

        lock_call = next(
            call
            for call in FakeClient.instances[0].calls
            if call[:2] == (const.BASE_MAIN, const.CMD_LOCK_REQUEST)
        )
        self.assertEqual(
            lock_call[2],
            protocol.encode_lock_payload(const.LOCKED, 2, "98B76", 5678),
        )

    def test_unlock_adopts_locked_response_and_raises(self) -> None:
        updates: list[None] = []
        lock_coordinator = coordinator.YKKApSmartLockCoordinator(
            object(),
            types.SimpleNamespace(
                title="lock",
                data={
                    const.CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
                    const.CONF_SMARTPHONE_ID: 1,
                    const.CONF_LOT_NUMBER: "12A34",
                    const.CONF_SERIAL_NUMBER: 1234,
                },
            ),
            lambda: updates.append(None),
        )
        lock_coordinator.lock_state = const.UNLOCKED
        FakeClient.responses = {
            (const.BASE_INFORMATION, const.CMD_SET_TIMESTAMP): _response(
                const.BASE_INFORMATION, const.CMD_SET_TIMESTAMP, b""
            ),
            (const.BASE_MAIN, const.CMD_LOCK_REQUEST): _response(
                const.BASE_MAIN, const.CMD_LOCK_REQUEST, bytes((const.LOCKED,))
            ),
        }

        with self.assertRaisesRegex(
            coordinator.YKKApSmartLockCommandError,
            r"lock returned state 1, expected 2",
        ):
            asyncio.run(lock_coordinator.async_set_lock_state(const.UNLOCKED))

        self.assertEqual(lock_coordinator.lock_state, const.LOCKED)
        self.assertEqual(lock_coordinator.last_command, "unlock")
        self.assertEqual(
            lock_coordinator.last_error, "lock returned state 1, expected 2"
        )
        self.assertEqual(len(updates), 1)

    def test_extra_state_attributes_exclude_registration_identity(self) -> None:
        lock_coordinator = coordinator.YKKApSmartLockCoordinator(
            object(),
            types.SimpleNamespace(
                title="lock",
                data={
                    const.CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
                    const.CONF_SMARTPHONE_ID: 1,
                    const.CONF_LOT_NUMBER: "12A34",
                    const.CONF_SERIAL_NUMBER: 1234,
                },
            ),
            lambda: None,
        )

        self.assertEqual(
            lock_coordinator.extra_state_attributes(),
            {
                "last_command": None,
                "last_error": None,
                "registered": True,
                "advertising_key_available": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
