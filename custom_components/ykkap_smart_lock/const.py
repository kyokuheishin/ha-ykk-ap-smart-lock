"""Constants for the YKKApSmartLock BLE integration."""

from typing import Final

DOMAIN: Final = "ykkap_smart_lock"

SERVICE_UUID: Final = "a437df7b-60cc-4b5c-98d1-c05e85c88c77"
NOTIFY_CHAR_UUID: Final = "a4370001-60cc-4b5c-98d1-c05e85c88c77"
WRITE_CHAR_UUID: Final = "a4370002-60cc-4b5c-98d1-c05e85c88c77"
MANUFACTURER_ID: Final = 0x099D

BASE_MAIN: Final = 0
BASE_INFORMATION: Final = 1
BASE_SETTINGS: Final = 3

CMD_LOCK_REQUEST: Final = 0x03
CMD_ADV_DATA_KEY: Final = 0x10
CMD_SET_TIMESTAMP: Final = 0x02

CMD_REQUEST_GENERAL_SMARTPHONE_ID: Final = 0x51
CMD_REQUEST_GENERAL_LOCK_ID: Final = 0x52

LOCKED: Final = 1
UNLOCKED: Final = 2

CONF_ADDRESS: Final = "address"
CONF_SMARTPHONE_ID: Final = "smartphone_id"
CONF_LOT_NUMBER: Final = "lot_number"
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_ADVERTISING_KEY: Final = "advertising_key"

SERVICE_REGISTER_DEVICE: Final = "register_device"

DEFAULT_RESPONSE_TIMEOUT: Final = 5.0
DEFAULT_CONNECTION_TIMEOUT: Final = 15.0
