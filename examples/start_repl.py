# SPDX-License-Identifier: MIT
# First upload firmware/ble_repl.py (and optionally firmware/bletime.py) via USB.
# Run this snippet once in the USB REPL. Starting the radio does not run a loop.
# Keep the returned object for diagnostics and an intentional shutdown.
from ble_repl import start_ble_repl

ble = start_ble_repl(name="MPY-BLE-DEMO")
print("BLE REPL is advertising; connect from the web demo.")
# Do not call ble.close() here: that would remove the BLE REPL immediately.
