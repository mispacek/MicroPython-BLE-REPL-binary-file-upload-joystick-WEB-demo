# SPDX-License-Identifier: MIT
# Execute AFTER the driver is running. A second BLE owner is intentionally refused.
from ble_repl import Joystick, joy_read
import time

left = Joystick(0)       # Any selector except 10 uses the left pair.
right = Joystick(10)     # The legacy selector 10 means the right pair.

try:
    while True:
        axes, age_ms = joy_read()  # axes is the driver's SHARED mutable list.
        print("Left:", left.get_joyX(), left.get_joyY(),
              "Right:", right.get_joyX(), right.get_joyY(),
              "Up pressed:", left.joy_check(1), "Age:", age_ms)
        # Sleep yields between samples; Ctrl+C/Stop can also interrupt Python loops.
        # Class getters return zero once the latest input is >= 3000 ms old.
        time.sleep_ms(250)
except KeyboardInterrupt:
    print("Joystick example stopped; BLE remains active.")
