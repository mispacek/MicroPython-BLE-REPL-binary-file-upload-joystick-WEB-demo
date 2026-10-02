"""MIT. Opt-in C3 hardware test: pip install pyserial bleak; python -B tests/hardware.py.

Never flashes firmware or changes startup. Only creates /__ble_demo_test and
removes its own marked namespace. Default: fresh root containing boot.py alone.
--preserve-root: hash every existing root file, temporarily close an unconnected
BLE REPL, and restore files plus advertising settings afterwards. Refuses root
directories, an existing test namespace or another owner's active BLE session.
Node runs the same web/ble-client.js.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

if os.environ.get("ESPIDE_BLE_TEST_DEPS"):
    sys.path.insert(0, os.environ["ESPIDE_BLE_TEST_DEPS"])
from bleak import BleakClient, BleakScanner
import serial

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/__ble_demo_test"
RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
NAME = "MPY-BLE-DEMO-TEST"


class USB:
    def __init__(self, port):
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, 115200, .1
        self.s.dtr = self.s.rts = False
        self.s.open()
        self.raw = False

    def until(self, suffix, seconds=12):
        data = bytearray()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            data.extend(self.s.read(1))
            if data.endswith(suffix):
                return bytes(data)
        raise TimeoutError(f"USB waiting for {suffix!r}: {data[-1000:]!r}")

    def enter(self):
        self.s.write(b"\x03\r\x03")
        time.sleep(.15)
        self.s.reset_input_buffer()
        self.s.write(b"\x01")
        self.until(b"raw REPL; CTRL-B to exit\r\n>")
        self.raw = True

    def execute(self, source):
        if not self.raw:
            self.enter()
        data = source.encode()
        for i in range(0, len(data), 256):
            self.s.write(data[i:i+256]); time.sleep(.003)
        self.s.write(b"\x04")
        self.until(b"OK")
        stdout = self.until(b"\x04")[:-1]
        stderr = self.until(b"\x04")[:-1]
        self.until(b">")
        if stderr:
            raise RuntimeError(stderr.decode())
        return stdout.decode().strip()

    def put(self, path, data):
        self.execute(f"_demo_f=open({path!r},'wb')")
        try:
            for i in range(0, len(data), 384):
                b64 = base64.b64encode(data[i:i+384])
                self.execute(f"import ubinascii; _demo_f.write(ubinascii.a2b_base64({b64!r}))")
        finally:
            self.execute("_demo_f.close(); del _demo_f")

    def digest(self, path):
        return self.execute("import hashlib,ubinascii\n"
            f"_demo_f=open({path!r},'rb'); _demo_h=hashlib.sha256()\n"
            "while True:\n _demo_b=_demo_f.read(512)\n if not _demo_b: break\n _demo_h.update(_demo_b)\n"
            "_demo_f.close(); print(ubinascii.hexlify(_demo_h.digest()).decode())\n"
            "del _demo_f,_demo_h,_demo_b")

    def friendly(self):
        if self.raw:
            self.s.write(b"\x02"); self.until(b">>> "); self.raw = False


async def run(port, mtu, preserve_root=False):
    usb = USB(port)
    owned = False
    report = {"passed": False, "board": "ESP32-C3", "results": [], "test_mtu": mtu,
              "recorded_utc": datetime.now(timezone.utc).isoformat(), "host": sys.platform}
    boot_hash = None
    baseline = None
    radio_settings = None
    process = None
    try:
        print("Checking board and protecting existing files…", flush=True)
        files = sorted(json.loads(usb.execute("import os,json,sys; print(json.dumps(os.listdir('/')))")))
        if not preserve_root and files != ["boot.py"]:
            raise RuntimeError(f"Expected fresh board with boot.py only, got {files}")
        if "boot.py" not in files or "__ble_demo_test" in files:
            raise RuntimeError("Missing boot.py or existing test namespace")
        usb.execute("assert all(os.stat('/'+p)[0] & 0x8000 for p in os.listdir('/'))")
        identity = usb.execute("import os,sys; print(os.uname()); print(sys.version)")
        if "ESP32C3" not in identity or "1.29" not in identity:
            raise RuntimeError(f"Expected C3 MicroPython 1.29: {identity}")
        report["identity"] = identity
        hashes = {name: usb.digest('/' + name) for name in files}
        boot_hash = hashes["boot.py"]
        radio_active = usb.execute("import bluetooth; print(bluetooth.BLE().active())") == "True"
        if radio_active:
            if not preserve_root or "ble_repl.py" not in files:
                raise RuntimeError("Active BLE requires --preserve-root and an installed ble_repl.py")
            radio_settings = json.loads(usb.execute(
                "_demo_previous=sys.modules.get('ble_repl')\n"
                "assert _demo_previous is not None and _demo_previous.get_active() is not None\n"
                "_demo_old_driver=_demo_previous.get_active()\n"
                "assert not _demo_old_driver.stats()['connected']\n"
                "print(json.dumps({'name':_demo_old_driver._name.decode(), 'slot':_demo_old_driver._slot}))"))
        baseline = {"root": files, "file_sha256": hashes, "ble_active": radio_active,
                    "radio_settings": radio_settings}
        report["baseline"] = baseline
        usb.execute(f"os.mkdir({REMOTE!r}); open({REMOTE + '/OWNED'!r},'w').write('ble-repl-demo')")
        owned = True
        if radio_active:
            usb.execute("_demo_old_driver.close(); del _demo_old_driver,_demo_previous")
        usb.execute("assert not bluetooth.BLE().active(); sys.modules.pop('ble_repl',None); sys.modules.pop('ble_repl_bletime',None)")
        usb.put(REMOTE + "/ble_repl.py", (ROOT / "firmware/ble_repl.py").read_bytes())
        usb.execute(f"sys.path.insert(0,{REMOTE!r}); from ble_repl import start_ble_repl; _demo_ble=start_ble_repl(name={NAME!r})")
        if mtu == 23:
            # Test-only stack preference, BEFORE connection. Driver defaults stay 247.
            usb.execute("_demo_ble._ble.config(mtu=23)")
        usb.friendly()
        print("Finding Windows BLE peripheral…", flush=True)
        device = await BleakScanner.find_device_by_name(NAME, timeout=15)
        if device is None:
            raise RuntimeError("Test board did not advertise")
        async with BleakClient(device, timeout=15) as ble:
            process = await asyncio.create_subprocess_exec("node", str(ROOT / "tests/hardware-client.js"), str(mtu),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            def deliver(message):
                process.stdin.write((json.dumps(message) + "\n").encode())
            def notification(_characteristic, data):
                deliver({"notification": base64.b64encode(data).decode()})
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), 90)
                if not line:
                    break
                request = json.loads(line)
                value = None
                try:
                    operation = request["operation"]
                    if operation == "notify":
                        await ble.start_notify(TX, notification)
                    elif operation == "write":
                        await ble.write_gatt_char(request["uuid"], base64.b64decode(request["bytes"]), response=True)
                    elif operation in ("digest", "size"):
                        path = request["path"]
                        if not path.startswith(REMOTE + "/"):
                            raise RuntimeError("USB path outside test namespace")
                        usb.s.reset_input_buffer()
                        value = usb.digest(path) if operation == "digest" else int(usb.execute(f"print(os.stat({path!r})[6])"))
                        usb.friendly()
                    elif operation in ("done", "failed"):
                        report["results"] = request["results"]
                        report["passed"] = operation == "done"
                        if operation == "failed":
                            report["error"] = request["error"]
                    else:
                        raise RuntimeError("Unknown hardware request")
                    deliver({"id": request["id"], "value": value})
                except Exception as error:
                    deliver({"id": request["id"], "error": str(error)})
                await process.stdin.drain()
            code = await process.wait()
            if code:
                raise RuntimeError("JavaScript hardware checks failed")
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if process and process.returncode is None:
            process.terminate(); await process.wait()
        try:
            if owned:
                print("Closing test radio and restoring the board…", flush=True)
                usb.enter()
                usb.execute("if '_demo_ble' in globals(): _demo_ble.close()")
                # Cleanup is bounded by a literal namespace and an ownership marker.
                usb.execute(f"assert open({REMOTE + '/OWNED'!r}).read() == 'ble-repl-demo'\n"
                    "def _demo_remove(path):\n"
                    f" assert path == {REMOTE!r} or path.startswith({REMOTE + '/'!r})\n"
                    " for item in os.ilistdir(path):\n"
                    "  child=path+'/'+item[0]\n"
                    "  if item[1] & 0x4000: _demo_remove(child)\n"
                    "  else: os.remove(child)\n"
                    " os.rmdir(path)\n"
                    f"_demo_remove({REMOTE!r}); del _demo_remove\n"
                    f"assert sorted(os.listdir('/')) == {files!r}; assert not bluetooth.BLE().active(); assert os.dupterm(None,0) is None")
                restored_hashes = {name: usb.digest('/' + name) for name in files}
                assert restored_hashes == baseline["file_sha256"], "An existing board file changed"
                usb.s.write(b"\x04"); usb.until(b"raw REPL; CTRL-B to exit\r\n>"); usb.raw = True
                # Soft reset executes the untouched boot.py. A driver started
                # manually before the test is restarted with its original settings.
                restored_radio = usb.execute("import bluetooth; print(bluetooth.BLE().active())") == "True"
                if baseline["ble_active"] and not restored_radio:
                    usb.execute(f"from ble_repl import start_ble_repl; ble=start_ble_repl(name={radio_settings['name']!r},slot={radio_settings['slot']!r})")
                    restored_radio = True
                assert restored_radio == baseline["ble_active"], "BLE state differs after reset"
                if restored_radio:
                    restored_settings = json.loads(usb.execute("import sys,json; _demo_d=sys.modules['ble_repl'].get_active(); print(json.dumps({'name':_demo_d._name.decode(),'slot':_demo_d._slot})); del _demo_d"))
                    assert restored_settings == radio_settings
                report["restored"] = {"root": files, "file_sha256": restored_hashes,
                    "boot_sha256": boot_hash, "ble_active": restored_radio, "radio_settings": radio_settings}
        except Exception as error:
            report["passed"] = False
            report["cleanup_error"] = str(error)
            raise
        finally:
            usb.friendly(); usb.s.close()
            report["source_sha256"] = hashlib.sha256((ROOT / "firmware/ble_repl.py").read_bytes()).hexdigest()
            report["client_sha256"] = hashlib.sha256((ROOT / "web/ble-client.js").read_bytes()).hexdigest()
            name = "hardware-results.json" if mtu == 247 else "hardware-results-mtu23.json"
            (ROOT / "tests" / name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--mtu", type=int, choices=(23, 247), default=247)
    parser.add_argument("--preserve-root", action="store_true", help="Preserve/hash existing root files and restore an unconnected BLE REPL")
    args = parser.parse_args()
    asyncio.run(run(args.port, args.mtu, args.preserve_root))
