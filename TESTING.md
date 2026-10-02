# Verification record

Updated **2026-10-03**, release **1.2.0**. A protocol model, a browser page and a
real radio/VM are separate kinds of evidence. The current demo client has local
tests; the current driver also has the real ESP32-S3 evidence described below.

## Static / local — passed for 1.2.0

- **73 driver tests** against the MIT copy in `firmware/`: fixed buffers, deferred
  processing, native interrupt boundaries, fragmented/coalesced input, MTU,
  checksums, sequence numbers, deadlines, joystick, overflow and reconnect.
  Regressions cover direct `run_code()` after EOF, duplicate final DATA without
  another file write, Stop protection, fault quarantine and recovery.
- **31 JavaScript tests** against the current protocol client and highlighter:
  MTU-23/247 chunking, fragmented UTF-8/capabilities/ACKs, binary identity,
  header/window/final-ACK recovery, empty files, editor Run/stdin/Stop, terminal
  controls, bounded input, disconnect cleanup, upload protection, fragmented
  `BLE Error reason=file errno=20`, subsequent upload on the same connection,
  preservation of the original error and recovery of the GATT write queue.
- **2 client/driver integration tests**, at MTU 23 and 247. The actual JavaScript
  client exchanges bytes with the actual packaged Python driver through fake
  GATT/CPython adapters. A 4097-byte upload completes despite Stop/Ctrl+C and a
  dropped final ACK; a real filesystem ENOTDIR is reported, followed by a
  successful upload and REPL command without reconnecting. The tiny prompt
  stand-in is not a MicroPython VM, and these tests do not measure BLE radio behavior.
- Python AST, JavaScript syntax, local documentation/asset links, MIT marker,
  40,000-byte source budget and physical-report source hashes are checked by
  `check-package.py`. The current firmware is byte-identical to the MIT driver
  used for the recorded S3 checks. RX/TX/IN remain **2048/2048/4096** bytes.

Run from this repository root (Python 3.10+ and Node 18+):

```sh
python -B -m unittest discover -s tests -p test_driver.py
npm test
python -B tests/check-package.py
```

No `npm install` is required. `npm test` runs all **33 JavaScript tests**, including
the Python peer tests; Python must also be on PATH. Package checks compare archived
reports with their immutable source snapshots, the S3 record with the current
driver, and any newly generated demo hardware report with the current client/driver.
Old hashes have not been changed to make an untested revision appear tested.

## Live browser / MCP — historical page checks; current device UI check remains open

On **2026-10-03**, the current local page loaded `app.js?v=1.2.0` without console
errors/warnings and switched to the driver-diagnostics editor example. Document
height was again 1363px, terminal height 388.5px and document/viewport width 1265px
without horizontal overflow. No board connection was opened for this check.

On **2026-10-02**, the local demo page passed checks for its light theme, Python
colors, editor input/example switching, four-space Tab indentation and HTML
escaping. Joystick pads use radial gradients without borders or crosshairs.
Screenshot: [demo.png](docs/demo.png).

The interactive terminal's resize feedback loop was reproduced and fixed. The
desktop document stayed at 1363px across repeated observations/reloads, with
388.5px terminal height and 16 rows, without horizontal overflow. A 390px viewport
passed before the terminal update; the subsequent viewport-resize attempt did
not take effect, so that is not a current narrow-screen terminal result.

The isolated [terminal fixture](tests/terminal-browser.html) passed fragmented
ANSI, carriage return/backspace/color and typed keys including arrows, Home/End,
Tab and Ctrl+A/B/E without local echo. Synthetic Ctrl+C/paste handlers passed;
native OS Ctrl+C/Ctrl+V were not delivered by the automation provider. The
browser did not expose its Bluetooth chooser, so no browser GATT session was
established in those demo UI checks; a Save `.py` download observation timed out.

The renderer and styling are unchanged in 1.2.0. The new upload protection and
error-recovery UI still need a full current-demo run in supported Chrome/Edge:
select the board, type directly, Run/Stop, load/save `.py`, upload a binary file,
recover after a file error and exercise both pads. Touch and narrow phone layouts
remain manual checks. There is no service worker; versioned module URLs invalidate
the changed JavaScript on reload.

## Physical device / host — current driver passed with production ESP IDE

On **2026-10-02**, the exact driver now in `firmware/ble_repl.py` was tested on a
real **ESP32-S3 / MicroPython 1.29.0 (2026-08-24)** using production ESP IDE Web
Bluetooth v2.1.13/v2.1.14. [Driver evidence](tests/driver-validation-s3.json)
records the driver SHA-256 and clearly identifies that separate client.

- Uploads of 0, 1, 239, 240, 241, 961, 8192, 32768, 65536 and 262144 bytes matched
  independent byte sizes/SHA-256 checks.
- Ten Stop attempts during a 256 KiB upload did not cancel it.
- A missing final ACK recovered without reconnecting.
- A real `BLE Error reason=file errno=20` was followed by a successful upload on
  the same connection.
- Three File Manager listings each included all 40 test files; a 64 KiB download
  matched SHA-256. Original startup/program files were preserved.

This validates the **driver**, not the newly updated demo JavaScript or its UI.
The demo 1.2.0 physical harness has been updated to expect a completed upload
after Stop, but has **not been rerun on hardware**. No current Android/iOS,
Wi-Fi coexistence, RF-interference, overnight or power-loss result is claimed.

## Historical demo 1.1.0 — 18 physical groups passed

The earlier demo client/driver were tested on **ESP32-C3 / MicroPython 1.29.0**
through Node/Bleak on Windows, at actual negotiated MTUs **247 and 23**. The
[MTU 247 report](tests/historical/v1.1.0/hardware-results.json) and
[MTU 23 report](tests/historical/v1.1.0/hardware-results-mtu23.json) remain unchanged.
Their exact [driver](tests/historical/v1.1.0/firmware/ble_repl.py) and
[client](tests/historical/v1.1.0/web/ble-client.js) snapshots are archived only for
hash verification; they are not loaded by the current demo.

Nine groups per MTU covered CONFIG, interactive/raw/paste REPL, 4097-byte binary
upload with USB SHA-256 readback, empty files, Run/stdout/stderr, `input()`, both
joysticks, busy-loop interruption followed by another Run, and the then-current
upload cancellation/replacement behavior. **That last historical behavior is
superseded by upload protection in 1.2.0.** The 4097-byte upload took about 1.6 s
at MTU 247 and 31.4 s at MTU 23 on that host, not a general throughput benchmark.
Busy-loop timing includes the following Run, not isolated Ctrl+C latency.

Writes stayed within the owned `/__ble_demo_test` namespace. Both reports record
matching baseline/restored original file hashes and BLE startup state. They do
not certify the present state of a board that may since have been changed.

## Repeat the opt-in physical test

Use a dedicated fresh ESP32-C3 with MicroPython 1.29 and only `boot.py` at the root.
The harness refuses any other root contents, so it cannot overwrite an existing
project. Close other serial/BLE clients first and install test dependencies:

```sh
python -m pip install pyserial bleak
python -B tests/hardware.py --port COM4 --mtu 247
python -B tests/hardware.py --port COM4 --mtu 23
```

Node must be on PATH. These commands write new `tests/hardware-results*.json`
for the current sources, create/clean only the owned namespace and verify
original file hashes. Archived 1.1.0 reports are not overwritten. For an already
configured board, add `--preserve-root`; this accepts regular root files, hashes
them, refuses root directories/existing test namespace and temporarily closes
only an identified BLE REPL with no client connected. Reset restores/checks the
original advertisement and dupterm slot. Interrupted tests may need USB recovery;
check the ownership marker before cleaning any test directory.

For manual browser verification:

```sh
python -B tests/browser-session.py --port COM4
```

After READY, connect the browser to `MPY-BLE-DEMO-UI` and set its Run destination
to `/__ble_demo_test/ui.py`. Keep uploads in `/__ble_demo_test/` too. Disconnect
the browser and press Enter in that terminal to restore the board. This fixture
prepares a temporary driver without startup changes; it does not itself validate UI.
