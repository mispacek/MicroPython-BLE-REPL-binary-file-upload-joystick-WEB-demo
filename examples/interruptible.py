# SPDX-License-Identifier: MIT
import time

try:
    i = 0
    while True:
        print("Tick", i)
        i += 1
        time.sleep_ms(500)
except KeyboardInterrupt:
    print("Stopped. BLE REPL still works.")
