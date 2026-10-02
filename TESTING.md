# Verification record

Recorded **2026-10-02**. Evidence is split below so a protocol model, a browser
page and a real radio/VM are never presented as the same test.

## Static / local — passed

- **64 driver tests** against the MIT copy in `firmware/`: fixed buffers, soft timer,
  native boundary markers, ownership/cleanup, early MTU events, retry, fragmented
  and coalesced input, checksum/sequence errors, deadlines, file errors, joystick,
  overflow and reconnect. The fake environment is not a MicroPython VM or radio.
- **25 JavaScript tests** against `web/ble-client.js`: MTU-23/247 chunking, bytewise
  fragmented UTF-8/capability/ACK notifications, binary identity, header retry,
  lost window/final ACK, STATUS recovery, absolute ACK boundaries after NAK,
  empty file, Run stdout/stderr, Stop during execution/fragmented header,
  disconnect cleanup and rejected file-error completion. Includes highlighter
  escaping and wire bounds. Interactive regressions verify UTF-8/control-byte
  order, the 4096-byte input queue, cancellation before upload, blocked transfer
  input, stdin during managed execution, mode-control guards, reconnect isolation
  and Ctrl+C bypassing queued paste without switching the manual raw mode.
- Python AST and JavaScript syntax, relative documentation/asset links, MIT marker,
  40,000-byte source budget and recorded hardware hashes checked by `check-package.py`.
- The firmware copy has the same parsed Python AST as the original new driver;
  licensing comments differ. Production ESP IDE and the legacy reference were untouched.

Run from this repository root (Python 3.10+ and Node 18+):

```sh
python -B -m unittest discover -s tests -p test_driver.py
npm test
python -B tests/check-package.py
```

No `npm install` is required. `check-package.py` refuses a stale physical report
if the client or driver no longer matches its recorded SHA-256.

## Live browser / MCP — page checks passed; full device UI check remains open

The actual page was served by `serve.py` at localhost and opened in the Codex
in-app browser. Verified: load without JavaScript console errors, Python colors,
example switching, editor input, four-space Tab insertion and HTML escaping.
The initial narrow viewport rendered stacked panels.

The visual refresh on 2026-10-02 adds a light background, white surfaces and blue
accents. Verified the default desktop layout and a 390px viewport without page
overflow, example switching, Tab indentation and matching editor/highlight font
metrics. Joystick pads/thumbs use only radial gradients: both borders are 0px and
both crosshair pseudo-elements are absent. Updated screenshot: [demo.png](docs/demo.png).
The later interactive terminal adds local xterm.js 6.0.0 and FitAddon 0.11.0,
direct input and ANSI cursor rendering. A resize feedback loop was reproduced:
the terminal reached more than 6000px in height while FitAddon repeatedly grew
its flex/grid container. CSS now contains renderer sizing, and the observer only
fits when host dimensions change. The physical reports below were rerun for the
new protocol-client hash.

The isolated [terminal fixture](tests/terminal-browser.html) imports the same
renderer without a BLE backend. Passed: fragmented ANSI, line replacement,
carriage return, backspace and color; actual typed characters, Enter, four arrows,
Home/End, Backspace, Tab and Ctrl+A/B/E reach its input callback without local echo.
Synthetic Ctrl+C and paste events separately pass the actual handlers, including
one interrupt byte and CRLF/LF-to-CR conversion with indentation preserved.
The in-app automation provider did not deliver native Ctrl+C/Ctrl+V key events;
those OS shortcuts still need a manual browser check. No board response is simulated.

The current desktop page remained at 1363px document height across repeated
observations and reloads; terminal height was 388.5px with 16 rows. It has no
horizontal page overflow and retains the light theme without dark viewport edges.
The current attempt to set a 390px viewport left the browser at 1280px, so a new
narrow-viewport result is not claimed for this terminal version.

Web Bluetooth selection was attempted, but the in-app browser did not expose a
Bluetooth device chooser in the accessible page/screenshot. No browser GATT session
was established. The local Save `.py` download observation also timed out; no file
download was claimed. Do not infer a browser/device result from the Node test below.

Still perform in supported Chrome/Edge: select the board, type directly, Run/Stop,
load/save a local `.py`, upload binary through the file input, and hold/release both
pads using pointer and keyboard. Touch, narrow phone viewport and GitHub Pages
deployment have not been tested. Reload after editing assets; there is no service worker.

## Physical device / Windows host — 18 groups passed

**Real ESP32-C3 on COM4**, MicroPython 1.29.0 dated 2026-08-24, Windows BLE via
Bleak, with the **same `web/ble-client.js`** running in Node through a thin GATT
adapter. Both actual negotiated **MTU 247 (244B payload)** and **MTU 23 (20B payload)**
were verified. At MTU 23 the harness changes only the test stack preference before
connection; the distributed driver default remains 247.

Nine groups in each report:

1. CONFIG capability and real negotiated MTU.
2. Interactive typing without Enter, cursor/backspace editing, Home/End, history,
   Tab completion, multiline paste and manual raw/friendly transitions on the real VM.
3. **4097-byte binary upload** into a new nested folder, with independent USB
   SHA-256 readback: `3b34ef01bf0b26b74e23f75c8752791b406c4befbc271b68dd7f015fbd4b7891`.
4. Empty file creation.
5. Editor Run mechanism, UTF-8 stdout, real `ValueError` stderr, then a successful Run.
6. Terminal stdin for `input()` during editor Run, with the reply `Ada`.
7. Both signed joystick pairs `[-70, 80, 90, -100]`.
8. Terminal Ctrl+C stopping a Python busy loop, then a successful next Run over the same connection.
9. Stop during upload, followed by replacement of the partial file.

Reports: [MTU 247](tests/hardware-results.json) and [MTU 23](tests/hardware-results-mtu23.json).
They include source/client hashes, firmware identity, durations and restored state.
Timing for “Stop busy Python” includes the following Run; it is **not isolated Ctrl+C latency**.
This conservative demo uses GATT writes with response where supported. The 4097-byte
upload took about 1.6 s at MTU 247 and 31.4 s at MTU 23 on this host; this is not a
general BLE throughput benchmark.

The first MTU-23 joystick fixture sent axes before the slower upload/REPL setup and
then read them after 3 s. The getters correctly returned zero due to stale protection.
The fixture was corrected to send fresh axes after the program announced readiness;
the client and driver did not need a change for that observation.

All test writes stayed inside the marked `/__ble_demo_test` namespace. The board
now contains existing `boot.py`, `ble_repl.py` and `ble_demo_run.py`; the test used
`--preserve-root`. After closing the test radio, removing only that namespace and
soft-resetting, all three original file hashes matched their baseline. The original
BLE advertisement `MPY-BLE-DEMO` in slot 0 was restored by the untouched boot file.
Its current SHA-256, recorded before and after both test runs, is:

```text
c61da2f80e8ab7119a4e5ed895a9c28ed34b1d2cc4a9461e58cb08df3e098eef
```

No firmware flashing, startup editing, other ESP32 models, browser picker success,
overnight endurance, RF interference or power-loss testing is claimed here.

## Repeat the opt-in physical test

Use a dedicated fresh ESP32-C3 with MicroPython 1.29 and only `boot.py` at the root.
The harness refuses any other root contents, so it cannot overwrite an existing project.
Close other serial/BLE clients first. Install Python test dependencies explicitly:

```sh
python -m pip install pyserial bleak
python -B tests/hardware.py --port COM4 --mtu 247
python -B tests/hardware.py --port COM4 --mtu 23
```

Node must be on PATH. These commands overwrite the corresponding result JSON,
create/clean only the owned namespace, and verify original file hashes.
For an already configured board, add `--preserve-root`. This mode accepts regular
root files, hashes every one, refuses root directories/existing test namespace,
and temporarily closes only an identified BLE REPL with no client connected.
After reset it restores/checks the original advertising name and dupterm slot.
Example for the recorded board: `python -B tests/hardware.py --port COM4 --mtu 247 --preserve-root`.
Interrupted tests may need USB recovery; never delete a directory without checking
its ownership marker and keeping cleanup inside this namespace.

For manual browser verification, a terminal can run:

```sh
python -B tests/browser-session.py --port COM4
```

After READY, connect the browser to `MPY-BLE-DEMO-UI` and change its Run destination
to `/__ble_demo_test/ui.py`. Keep uploads inside `/__ble_demo_test/` too. Disconnect
the browser and press Enter in that terminal to restore the clean board. This fixture
does not validate the UI itself; it prepares a temporary driver without startup changes.
