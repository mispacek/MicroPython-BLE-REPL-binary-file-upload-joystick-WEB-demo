# BLE wire protocol

All UUIDs and framing below describe the driver shipped in `firmware/ble_repl.py`.
The NUS interpreter is MicroPython's existing REPL. The upload and joystick
extensions preserve the ESP IDE wire format; they are not generic NUS features.

## GATT and MTU

| Purpose | UUID | Properties |
| --- | --- | --- |
| Nordic UART service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | Advertised service |
| RX: client → board | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | Write / write without response |
| TX: board → client | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | Notify |
| Joystick service | `23f10010-5f90-11ee-8c99-0242ac120002` | Optional service permission |
| Joystick axes | `23f10012-5f90-11ee-8c99-0242ac120002` | Write / write without response |

Register the notification listener before enabling notifications. Request both
services in the browser device selection permission. The driver sends:

```text
BLE Config mtu=247 chunk=244\n
```

Use the reported **chunk**, not the preferred MTU. Initially assume chunk 20,
MTU 23. The confirmed link may use MTU 23 or 247 (or another supported value).
The driver caps payload at 244. The client cannot set an ATT MTU through Web Bluetooth.
CONFIG can be explicitly requested after notification subscription and reconnect.
Treat TX as a byte stream: notifications may split or combine capability lines,
UTF-8 characters, three-byte replies and REPL text. Reset parser state per connection.

## REPL controls and editor execution

Interactive terminal keys are forwarded as UTF-8/control bytes, without a REPL
reset before each key. MicroPython supplies remote echo, line editing, history
and Tab completion. Preserve all returned ANSI cursor sequences; notifications
can split an escape sequence just as they can split a UTF-8 codepoint.
The demo's `resumeRepl()` synchronizes once on connection/recovery;
`writeTerminal()` preserves the active friendly/raw/paste mode. During managed
Run it also supplies stdin for `input()` after raw REPL acknowledges execution.
Uploads and REPL setup block text input. During managed execution Ctrl+C delegates
to Stop; during upload both are ignored until completion. Manual REPL forwards `03`.

| Byte | Standard MicroPython meaning |
| --- | --- |
| `03` | Ctrl+C: interrupt user Python code |
| `01` | Ctrl+A: enter raw REPL |
| `02` | Ctrl+B: return to friendly REPL |
| `04` | Ctrl+D: execute raw/paste input; soft reset in friendly REPL |
| `05` | Ctrl+E: enter paste mode from friendly REPL |

Do not send Ctrl+D casually in friendly mode: it can soft-reset the interpreter
and remove a manually started driver. For raw execution, wait for
`raw REPL; CTRL-B to exit` and the prompt, send a small Python wrapper, then Ctrl+D.
The response is `OK`, stdout, `04`, stderr, `04`, `>`.
The demo uploads the editor source first, closes the source file via `with`, then
compiles and executes it in REPL globals with `__name__='__main__'` and `__file__` set.
The wrapper reserves `_ble_demo_file` and `_ble_demo_code`; avoid those names in
your programs. Return to friendly REPL after completion. Stop aborts an indefinite
run wait without closing the transport. There is no execution time limit.

After upload EOF, the driver still recognizes STATUS/retry packets. Return to
REPL with the complete sequence `03 0D 02` (Ctrl+C, CR, Ctrl+B), not a lone control
byte: a single byte can wait up to 1 s while the legacy parser distinguishes DATA.
The driver also accepts directly following ASCII commands such as `run_code()\r\n`;
the last DATA sequence still takes precedence if its bytes resemble a command.
During active upload, never inject REPL control bytes. The demo protects Stop/Ctrl+C
until the upload and its final synchronization finish.

## Upload header

```text
FA CE B0 0C | NAME_LEN_FLAGS | SIZE_LE24 | UTF8_NAME
   4 B            1 B            3 B       0..48 B
```

Filename length is the low 7 bits; bit `80` selects the optional discard/sink mode.
Zero name length defaults to `data.bin`. The demo uses explicit names and writes
real files. Names are counted in **UTF-8 bytes**, not JavaScript characters; NUL
is refused. Missing parent folders are created. Size is unsigned little-endian
24 bit, at most `FF FF FF` = 16,777,215 bytes.

Example: `/a.bin` with three bytes:

```text
FA CE B0 0C 06 03 00 00 2F 61 2E 62 69 6E
```

The header interrupts running Python before deferred file open. The board opens
the target directly in `wb`. A successful open returns **`06 FF FF`**. An empty
file completes with this same response after close/sync. Retrying the identical
header before the first DATA packet is idempotent. **Do not retry a header after
DATA has started**: it may reopen and truncate the target.

## DATA and replies

```text
SEQ_LE16 | LENGTH_U8 | PAYLOAD | SUM8
   2 B        1 B     1..255 B  1 B
```

SUM8 is the sum of both sequence bytes, length and payload, modulo 256.
Sequence starts at zero. Payload normally uses `chunk - 4`, capped at 240 bytes
by this client, so a complete DATA frame fits in one GATT write. Headers may need
multiple writes; a frame must finish before cancellation is sent. Driver parsing
also supports fragmented/coalesced DATA, with a **1 s frame deadline**.

DATA sequence zero, bytes `01 02 03`:

```text
00 00 03 01 02 03 09
```

| Reply | Meaning |
| --- | --- |
| `06 FF FF` | Header accepted / empty file completed |
| `06 seq_low seq_high` | DATA ACK: **last accepted packet** |
| `15 seq_low seq_high` | NAK: **next expected packet** |
| `15 FF FF` | CANCEL response |

DATA ACKs are sent after absolute sequences **3, 7, 11, …**, and after the final
packet. The final ACK follows file close and `os.sync()`. Keep at most one window
of four packets in flight and use short pacing gaps (demo: 10 ms). After recovery
at sequence 1, the next ACK boundary remains 3; it does not move to 4.

The wire format has no transaction IDs or typed reply envelope. Keep one query
in flight, let stale queued NAKs drain, and interpret a reply in its operation
context. A NAK at the final expected sequence alone is not proof of successful
file close: query STATUS and require its ACK.

## Control frames and recovery

Each control is exactly eight bytes:

```text
FA CE B0 0C | OPCODE | 00 00 00
```

| Opcode | Operation | Response |
| --- | --- | --- |
| `FF` | CONFIG | ASCII capability line |
| `FD` | STATUS | ACK/NAK + **next expected sequence**, including after EOF |
| `FE` | CANCEL | Close partial file, `15 FF FF`, return parser to REPL |
| `FC` | STOP extension | Interrupt user code and reply `06 FC FF`; ignored during pending upload/final ACK or fault phase 5 |

The demo uses ordinary Ctrl+C for Stop outside upload. CANCEL is reserved for
error recovery, not the Stop button. If an ACK is lost, query STATUS;
do not reopen the file. On STATUS ACK sequence `n`, resume at `n * dataSize`, keeping
dataSize unchanged throughout the transfer. STATUS ACK equal to the packet count
confirms EOF. STATUS NAK indicates a file error; abort rather than claiming success.
Bound retries. File inactivity times out after **10 s**; query promptly.

Header wait in the demo is 2200 ms, retried at most three times before DATA.
Window and STATUS waits are 1600 ms, with at most six unsuccessful recovery windows.
Fewer than 65,536 DATA packets are allowed, preventing sequence wrap. At chunk 20
the largest demo file is therefore 65,535 × 16 bytes, smaller than the LE24 bound.
An increased MTU mid-transfer may allow larger GATT writes; keep the DATA size fixed.

Repeated final DATA with a valid checksum receives its ACK again without reopening,
writing or syncing the file a second time. This resolves a lost final acknowledgement.

On a driver fault, `BLE Error reason=<name> errno=<number>\n` reports the cause;
the line may follow a REPL prompt and span notifications. Treat it separately from
three-byte ACK/NAK replies. File/ingress faults enter phase 5 and keep BLE connected.
Ordinary text is quarantined until a valid new file header or CANCEL resets the parser.
The demo preserves the original error even if subsequent REPL cleanup fails.

SUM8 is a frame checksum, not an end-to-end hash. On explicit CANCEL/disconnect/error,
the driver closes the file but does not restore its old content or remove partial
data. An application's atomic update policy belongs above this direct-write protocol.

## Joystick

Write four **signed Int8** bytes to the joystick characteristic:

```text
Lx Ly Rx Ry
```

Example left `(-50,100)`, right `(0,-100)` → `CE 64 00 9C`.
Class getters clamp −100…100 and return zero when input is at least 3000 ms old.
`joy_read()` exposes the shared raw list plus age; apply stale checks yourself if
using the raw list. A bounded heartbeat while held preserves freshness, and send
zeros on release/blur. The demo coalesces updates at 80 ms and pauses them during
file upload. This service has no button bitmask.

## Integration boundaries

Serialize GATT operations across both services; browser stacks can reject parallel
writes. Cancel between whole protocol frames, reset session state on disconnect,
and catch asynchronous failures. RX overflow quarantines text and reports a fault
without automatically disconnecting, preventing truncated Python execution.
Stdout may be dropped under heavy output pressure; diagnostics
track it. NUS itself does not provide authentication or encryption policy.
