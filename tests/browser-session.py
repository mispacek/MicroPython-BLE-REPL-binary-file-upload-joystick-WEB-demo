"""MIT. Temporary clean-board fixture for MANUAL Web Bluetooth UI verification.

Requires the same dependencies as hardware.py. Run from a terminal, wait for READY,
connect the browser to MPY-BLE-DEMO-UI, use /__ble_demo_test/ui.py as Run destination,
disconnect, then press Enter here to close BLE and remove this marked namespace.
No startup changes. Refuses a board with anything other than boot.py at the root.
"""
from hardware import USB, ROOT, REMOTE
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", default="COM4")
usb = USB(parser.parse_args().port)
owned = False
try:
    assert usb.execute("import os,bluetooth; print(sorted(os.listdir('/')))") == "['boot.py']"
    assert usb.execute("print(bluetooth.BLE().active())") == "False"
    boot_hash = usb.digest("/boot.py")
    usb.execute(f"os.mkdir({REMOTE!r}); open({REMOTE + '/OWNED'!r},'w').write('ble-repl-demo-ui')")
    owned = True
    usb.put(REMOTE + "/ble_repl.py", (ROOT / "firmware/ble_repl.py").read_bytes())
    usb.execute(f"import sys; sys.path.insert(0,{REMOTE!r}); from ble_repl import start_ble_repl; _demo_ble=start_ble_repl(name='MPY-BLE-DEMO-UI')")
    usb.friendly()
    print("READY: MPY-BLE-DEMO-UI; Run target /__ble_demo_test/ui.py", flush=True)
    input("Disconnect the browser, then press Enter to restore the board: ")
finally:
    try:
        if owned:
            usb.enter(); usb.execute("if '_demo_ble' in globals(): _demo_ble.close()")
            usb.execute(f"assert open({REMOTE + '/OWNED'!r}).read() == 'ble-repl-demo-ui'\n"
                "def _demo_rm(path):\n"
                f" assert path == {REMOTE!r} or path.startswith({REMOTE + '/'!r})\n"
                " for item in os.ilistdir(path):\n"
                "  child=path+'/'+item[0]\n"
                "  if item[1] & 0x4000: _demo_rm(child)\n"
                "  else: os.remove(child)\n"
                " os.rmdir(path)\n"
                f"_demo_rm({REMOTE!r})\n"
                "assert os.listdir('/') == ['boot.py']; assert not bluetooth.BLE().active(); assert os.dupterm(None,0) is None")
            assert usb.digest("/boot.py") == boot_hash
            usb.s.write(b"\x04"); usb.until(b"raw REPL; CTRL-B to exit\r\n>"); usb.raw = True
            print("RESTORED: boot.py unchanged, BLE off, dupterm empty", flush=True)
    finally:
        usb.friendly(); usb.s.close()
