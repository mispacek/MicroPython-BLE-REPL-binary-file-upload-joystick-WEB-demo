Thanks @jdfesa for the explanation of `os.dupterm_notify(None)` and the pointers to the upstream BLE REPL example and MicroPython internals. That helped me finish the redesign, and I would like to share a working implementation for others to try.

The original problem is now resolved on my tested ESP32-C3: **Ctrl+C interrupts a `while True: pass` loop, and the BLE connection remains usable afterwards**, without application-level polling or patched `sleep()` functions.

- **Source, documentation and examples (MIT):** [GitHub repository](https://github.com/mispacek/MicroPython-BLE-REPL-binary-file-upload-joystick-WEB-demo)
- **Try the browser demo:** [GitHub Pages](https://mispacek.github.io/MicroPython-BLE-REPL-binary-file-upload-joystick-WEB-demo/)

The implementation uses Nordic UART Service with a non-blocking `os.dupterm()` stream and fixed-size `micropython.RingIO` buffers. BLE receive data is queued; protocol parsing, file I/O and notification delivery run in deferred processing, with a soft timer as a fallback.

For interruption, the driver makes `0x03` available through the stream's `readinto()` and calls `os.dupterm_notify(None)` from a deferred callback. MicroPython's existing interrupt machinery then handles `KeyboardInterrupt`. The stream methods and interrupt-notification boundary use `@micropython.native`, allowing transport state to be restored before returning to VM pending-exception checks. The driver does not implement its own Python REPL interpreter or require firmware patches.

The web demo includes:

- An interactive xterm.js terminal with history, cursor editing, Tab completion, raw/paste modes and Ctrl+C.
- A Python editor with syntax highlighting, Run/Stop and terminal input for `input()` during execution.
- Binary file upload with sequence numbers, checksums and ACK/NAK/STATUS recovery.
  Stop/Ctrl+C are ignored during transfer, including the upload preceding editor Run.
- Two joysticks over the same BLE connection.

The earlier demo passed hardware checks on **ESP32-C3 / MicroPython 1.29.0** at actual ATT MTUs **23 and 247**, using its JavaScript client through a Node/Bleak GATT adapter. Those reports and exact source snapshots are archived. The **current driver** also passed real **ESP32-S3** checks with production ESP IDE: binary uploads through 256 KiB with independent SHA-256 checks, ten ignored Stop attempts during upload, recovery from a missing final ACK, and a subsequent upload after a filesystem error without reconnecting. It accepts direct `run_code()` after EOF and ACKs duplicate final DATA without rewriting the file.

The current demo 1.2.0 has **73 driver tests and 33 JavaScript tests**, including two tests connecting the actual client and driver through fake GATT at MTU 23/247. Its updated client shows fragmented device diagnostics and preserves the original error if REPL cleanup also fails. These local tests are not radio or MicroPython VM tests; a full hardware run of the updated demo UI remains open. [Test details and recorded results](https://github.com/mispacek/MicroPython-BLE-REPL-binary-file-upload-joystick-WEB-demo/blob/main/TESTING.md).

To try it, upload `firmware/ble_repl.py` to a compatible board as `/ble_repl.py`, then start it from the USB REPL:

```python
from ble_repl import start_ble_repl
ble = start_ble_repl(name="MPY-BLE-DEMO")
```

Open the GitHub Pages demo, click **Connect board**, and select **Interrupt a loop** in the editor to try Run/Stop. No local web server is required.

Hardware evidence now includes ESP32-C3 and the current driver on ESP32-S3, with the client/version boundaries recorded above. Other boards, Wi-Fi coexistence and long-duration stress testing still need investigation. Interruption remains subject to MicroPython's VM/event checkpoints, so this does not imply immediate interruption of arbitrary blocking native code.

I hope this provides a useful, reproducible example. Feedback on the architecture and results from other boards would be very welcome. Thanks again!
