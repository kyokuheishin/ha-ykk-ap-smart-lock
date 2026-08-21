"""Regression checks for the BLE transport boundaries and framing."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


COMPONENT = Path(__file__).parents[1] / "custom_components" / "ykkap_smart_lock"
PACKAGE = "_ykkap_ble_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package

bleak_retry_connector = types.ModuleType("bleak_retry_connector")
bleak_retry_connector.BleakClientWithServiceCache = object


async def _establish_connection(*args, **kwargs):
    del args, kwargs
    raise AssertionError("test must replace establish_connection")


bleak_retry_connector.establish_connection = _establish_connection
sys.modules["bleak_retry_connector"] = bleak_retry_connector

homeassistant = types.ModuleType("homeassistant")
components = types.ModuleType("homeassistant.components")
bluetooth = types.ModuleType("homeassistant.components.bluetooth")
bluetooth.async_ble_device_from_address = lambda *args, **kwargs: object()
components.bluetooth = bluetooth
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
homeassistant.components = components
homeassistant.core = core
for name, module in {
    "homeassistant": homeassistant,
    "homeassistant.components": components,
    "homeassistant.components.bluetooth": bluetooth,
    "homeassistant.core": core,
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
ble = _load("ble")


class _WriteCharacteristic:
    properties = {"write"}


class _WriteFailClient:
    async def write_gatt_char(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError("write failed")


class _DisconnectFailClient:
    is_connected = True

    async def stop_notify(self, characteristic):
        del characteristic

    async def disconnect(self):
        raise RuntimeError("disconnect failed")


class _NotifyFailService:
    def get_characteristic(self, uuid):
        del uuid
        return object()


class _NotifyFailServices:
    def get_service(self, uuid):
        del uuid
        return _NotifyFailService()


class _NotifyFailClient:
    services = _NotifyFailServices()
    is_connected = True

    async def start_notify(self, characteristic, callback):
        del characteristic, callback
        raise RuntimeError("notify failed")

    async def stop_notify(self, characteristic):
        del characteristic

    async def disconnect(self):
        self.is_connected = False


class _WriteOkClient:
    def __init__(self, on_write):
        self._on_write = on_write

    async def write_gatt_char(self, *args, **kwargs):
        del args, kwargs
        self._on_write()


class BleTransportTests(unittest.TestCase):
    def _client_with_queue(self):
        client = object.__new__(ble.YKKApSmartLockBleClient)
        client._responses = asyncio.Queue()
        client._rx_buffer = bytearray()
        client._response_timeout = 0.1
        return client

    def test_fixed_response_skips_interleaved_valid_unrelated_frame(self) -> None:
        client = self._client_with_queue()
        target = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01", request=False
        )
        unrelated = protocol.build_frame(
            const.BASE_MAIN, 0x04, b"\x01", request=False
        )
        for packet in (unrelated, target[:2], unrelated, target[2:]):
            client._responses.put_nowait(packet)

        result = asyncio.run(
            client._wait_for_response(
                const.BASE_MAIN, const.CMD_LOCK_REQUEST, timeout=0.1
            )
        )

        self.assertEqual(result.command, const.CMD_LOCK_REQUEST)
        self.assertTrue(result.is_response)

    def test_variable_response_keeps_four_byte_first_fragment(self) -> None:
        client = self._client_with_queue()
        target = protocol.build_frame(
            const.BASE_SETTINGS, 0x51, b"\x01\x02\x03\x04\x05", request=False
        )
        client._responses.put_nowait(target[:4])
        client._responses.put_nowait(target[4:])

        result = asyncio.run(
            client._wait_for_response(const.BASE_SETTINGS, 0x51, timeout=0.1)
        )

        self.assertEqual(result.payload, b"\x01\x02\x03\x04\x05")

    def test_concatenated_unrelated_and_target_in_one_notification(self) -> None:
        client = self._client_with_queue()
        target = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x02", request=False
        )
        unrelated = protocol.build_frame(
            const.BASE_MAIN, 0x04, b"\x01", request=False
        )
        client._responses.put_nowait(unrelated + target)

        result = asyncio.run(
            client._wait_for_response(
                const.BASE_MAIN, const.CMD_LOCK_REQUEST, timeout=0.1
            )
        )

        self.assertEqual(result.payload, b"\x02")

    def test_accept_skips_stale_lock_state_then_takes_desired(self) -> None:
        client = self._client_with_queue()
        stale = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01", request=False
        )
        desired = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x02", request=False
        )
        client._responses.put_nowait(stale + desired)

        result = asyncio.run(
            client._wait_for_response(
                const.BASE_MAIN,
                const.CMD_LOCK_REQUEST,
                timeout=0.1,
                accept=lambda frame: frame.payload[:1] == b"\x02",
            )
        )

        self.assertEqual(result.payload, b"\x02")

    def test_accept_skips_separate_stale_lock_state_notification(self) -> None:
        client = self._client_with_queue()
        stale = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01", request=False
        )
        desired = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x02", request=False
        )
        client._responses.put_nowait(stale)
        client._responses.put_nowait(desired)

        result = asyncio.run(
            client._wait_for_response(
                const.BASE_MAIN,
                const.CMD_LOCK_REQUEST,
                timeout=0.1,
                accept=lambda frame: frame.payload[:1] == b"\x02",
            )
        )

        self.assertEqual(result.payload, b"\x02")

    def test_accept_returns_fallback_when_desired_state_never_arrives(self) -> None:
        client = self._client_with_queue()
        stale = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01", request=False
        )
        client._responses.put_nowait(stale)

        result = asyncio.run(
            client._wait_for_response(
                const.BASE_MAIN,
                const.CMD_LOCK_REQUEST,
                timeout=0.05,
                accept=lambda frame: frame.payload[:1] == b"\x02",
            )
        )

        self.assertEqual(result.payload, b"\x01")

    def test_notification_handler_accepts_one_or_two_arguments(self) -> None:
        client = self._client_with_queue()
        packet = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01", request=False
        )

        client._on_notification(packet)
        client._on_notification(object(), bytearray(packet))

        self.assertEqual(client._responses.get_nowait(), packet)
        self.assertEqual(client._responses.get_nowait(), packet)

    def test_command_discards_stale_pre_write_lock_state(self) -> None:
        client = self._client_with_queue()
        stale = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01", request=False
        )
        desired = protocol.build_frame(
            const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x02", request=False
        )
        client._responses.put_nowait(stale)
        client._client = _WriteOkClient(
            lambda: client._responses.put_nowait(desired)
        )
        client._write_characteristic = _WriteCharacteristic()

        result = asyncio.run(
            client.command(
                const.BASE_MAIN,
                const.CMD_LOCK_REQUEST,
                b"\x02",
                accept=lambda frame: frame.payload[:1] == b"\x02",
            )
        )

        self.assertEqual(result.payload, b"\x02")

    def test_write_error_is_wrapped(self) -> None:
        client = object.__new__(ble.YKKApSmartLockBleClient)
        client._client = _WriteFailClient()
        client._write_characteristic = _WriteCharacteristic()
        client._responses = asyncio.Queue()
        client._rx_buffer = bytearray()

        with self.assertRaises(ble.YKKApSmartLockConnectionError) as raised:
            asyncio.run(
                client.command(
                    const.BASE_MAIN, const.CMD_LOCK_REQUEST, b"\x01"
                )
            )

        self.assertIsInstance(raised.exception.__cause__, RuntimeError)

    def test_disconnect_failure_is_debug_only_and_clears_references(self) -> None:
        client = object.__new__(ble.YKKApSmartLockBleClient)
        client._client = _DisconnectFailClient()
        client._notify_characteristic = object()
        client._write_characteristic = object()
        client._notification_callback = lambda *args: None

        asyncio.run(client.disconnect())

        self.assertIsNone(client._client)
        self.assertIsNone(client._notify_characteristic)
        self.assertIsNone(client._write_characteristic)
        self.assertIsNone(client._notification_callback)

    def test_start_notify_error_is_wrapped_and_connection_cleaned(self) -> None:
        async def establish(*args, **kwargs):
            del args, kwargs
            return _NotifyFailClient()

        original = ble.establish_connection
        ble.establish_connection = establish
        try:
            client = ble.YKKApSmartLockBleClient(object(), "AA:BB", "lock")
            with self.assertRaises(ble.YKKApSmartLockConnectionError) as raised:
                asyncio.run(client.connect())
        finally:
            ble.establish_connection = original

        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertIsNone(client._client)


if __name__ == "__main__":
    unittest.main()
