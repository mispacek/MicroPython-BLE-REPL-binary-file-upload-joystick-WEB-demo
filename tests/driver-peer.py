# SPDX-License-Identifier: MIT
"""Actual packaged driver on fake GATT; CPython prompt stand-in, no radio/VM."""
import json
import sys
from fakes import Context, control

c = Context().install()
d = c.start()
line = bytearray()
interrupts = 0
dropped = False


def prompt_input():
    global interrupts
    if c.interrupts != interrupts:
        line.clear()
        interrupts = c.interrupts
    buf = bytearray(2048)
    n = d.readinto(buf)
    if not n:
        return
    for ch in buf[:n]:
        if ch == 2:
            line.clear()
            d.write(b'\r\n>>> ')
        elif ch in (10, 13):
            if line == b'print(42)':
                d.write(b'42\r\n>>> ')
            line.clear()
        elif ch >= 32:
            line.append(ch)


try:
    for raw in sys.stdin:
        req = json.loads(raw)
        op = req['op']
        if op == 'connect':
            c.connect(mtu=req['mtu'])
        elif op == 'write':
            c.rx(bytes(req['data']))
            c.tick(2)
            prompt_input()
            c.tick(3)
        elif op == 'stop':
            c.rx(control(252))
        elif op not in ('check', 'stats'):
            raise ValueError(op)
        notifications = []
        for _, _, data in c.ble.notifications:
            if req.get('drop_final') and d._ph == 4 and not dropped and len(data) == 3 and data[0] == 6 and data[1:] != b'\xff\xff':
                dropped = True
                continue
            notifications.append(list(data))
        reply = {'notifications': notifications, 'stats': d.stats(), 'dropped_final': dropped}
        if op == 'check':
            reply['bytes'] = list(c.resolve(req['path']).read_bytes())
        c.clear_output()
        print(json.dumps(reply), flush=True)
finally:
    c.restore()
