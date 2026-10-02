# SPDX-License-Identifier: MIT
# Foreground diagnostics allocate dictionaries; do not call them from a hard IRQ.
from ble_repl import get_active

ble = get_active()
if ble is not None:
    print(ble.stats())
    print(ble.mem_usage())  # GC totals for the whole VM, not just this driver.
    # preferred_mtu is a request; negotiated_mtu + mtu_confirmed describe the link.
    # notify_errors counts recoverable errors too; fault is historical, not a bool.
