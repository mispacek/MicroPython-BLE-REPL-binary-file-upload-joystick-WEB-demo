# SPDX-License-Identifier: MIT
# ESP IDE contributors, MicroPython 1.29 / ESP32 NimBLE.
# Fixed buffers; only the advertised name and dupterm slot are public settings.
import bluetooth, micropython, os, io, utime, sys, errno
from micropython import const
from machine import Timer

_MTU = const(247)
_MAX = const(244)
_RX = const(2048)
_TX = const(2048)
_IN = const(4096)
_ATTR = const(512)
_PERIOD = const(20)
_BUDGET = const(488)
_LIMIT = const(0x1fffffff)
# These POSIX codes exist in ESP32 but are absent from its small errno module.
_BUSY = const(16)
_NOTDIR = const(20)
_BIG = const(27)
_MAGIC = b'\xfa\xce\xb0\x0c'
_NUS = bluetooth.UUID('6E400001-B5A3-F393-E0A9-E50E24DCCA9E')
_SERVICES = ((_NUS, (
    (bluetooth.UUID('6E400003-B5A3-F393-E0A9-E50E24DCCA9E'), 16),
    (bluetooth.UUID('6E400002-B5A3-F393-E0A9-E50E24DCCA9E'), 12))),
    (bluetooth.UUID('23F10010-5F90-11EE-8C99-0242AC120002'), (
    (bluetooth.UUID('23F10012-5F90-11EE-8C99-0242AC120002'), 12),)))
_ACTIVE = None
JOY_POS = [0, 0, 0, 0]
JOY_TS_MS = None


def joy_read():
    age = _LIMIT if JOY_TS_MS is None else utime.ticks_diff(utime.ticks_ms(), JOY_TS_MS)
    return JOY_POS, _LIMIT if age < 0 else age


class Joystick:
    def __init__(self, joy_selector):
        self._j = 2 if joy_selector == 10 else 0

    def get_joyX(self):
        p, age = joy_read()
        return max(-100, min(100, p[self._j])) if age < 3000 else 0

    def get_joyY(self):
        p, age = joy_read()
        return max(-100, min(100, p[self._j + 1])) if age < 3000 else 0

    def joy_check(self, joy_dir):
        x, y = self.get_joyX(), self.get_joyY()
        return ((joy_dir == 1 and y > 40) or (joy_dir == 2 and x > 40) or
                (joy_dir == 3 and y < -40) or (joy_dir == 4 and x < -40) or
                (joy_dir == 5 and x != 0 and y != 0))


def _inc(n, k=1):
    return _LIMIT if k >= _LIMIT - n else n + k


class BLENUSRepl(io.IOBase):
    def __init__(self, *, name=b'MPY-REPL', slot=0):
        nb = name.encode() if isinstance(name, str) else bytes(name)
        if not 1 <= len(nb) <= 29:
            raise ValueError('BLE name: 1..29 UTF-8 bytes')
        nb.decode('utf-8')
        self._name = nb
        self._slot = slot
        self._ble = bluetooth.BLE()
        self._r = micropython.RingIO(_RX)
        self._t = micropython.RingIO(_TX)
        self._in = micropython.RingIO(_IN)
        self._ib = bytearray(_ATTR)
        self._iv = memoryview(self._ib)
        self._b = bytearray(259)
        self._v = memoryview(self._b)
        self._sb = bytearray(_MAX)
        self._sv = memoryview(self._sb)
        self._junk = bytearray(128)
        qb = bytearray(512)
        qv = memoryview(qb)
        self._q = [qv[i:i + 64] for i in range(0, 512, 64)]
        self._ql = bytearray(8)
        self._adv = b'\x02\x01\x06\x11\x07' + bytes(_NUS)
        self._resp = bytes((len(nb) + 1, 9)) + nb
        self._wc = self._work
        self._icb = self._interrupt
        self._tc = self._tick
        self._irqcb = self._irq
        self._mx = getattr(self._ble, 'gattc_exchange_mtu', None)
        self._tm = None
        self._radio = self._dt = self._on = self._closed = 0
        self._ep = self._ws = self._busy = self._is = 0
        self._conn = None
        self._other = None
        self._eh = None
        self._em = 23
        self._ec = self._ef = 0
        self._f = None
        self._fc = 0
        self._last_file_error = self._close_error = 0
        self._drop = self._sf = self._ne = 0
        self._reason = None
        self._reset(False)

    @micropython.native
    def readinto(self, b):
        # No bytecode helpers here: a pending KeyboardInterrupt must not detach dupterm.
        if not self._on or self._conn is None or self._fault:
            return None
        if self._ic:
            b[0] = 3
            self._ic = 0
            return 1
        if self._ng or not self._ready or not self._r.any():
            return None
        return self._r.readinto(b)

    @micropython.native
    def write(self, b):
        n = len(b)
        if self._on and self._conn is not None and self._ready and not self._og and not self._fault:
            lost = n - self._t.write(b)
            if lost >= _LIMIT - self._drop:
                self._drop = _LIMIT
            else:
                self._drop += lost
        return n

    @micropython.native
    def ioctl(self, op, arg):
        if op == 3:
            if not self._on or self._conn is None or self._fault:
                return 0
            rd = self._ic or (self._ready and not self._ng and self._r.any())
            return arg & (4 | (1 if rd else 0))
        return 0

    @micropython.native
    def _interrupt(self, _):
        self._is = 0
        if not self._ip or not self._on or self._conn is None or self._fault:
            return
        ep = self._ep
        self._ip = 0
        self._ng = 1
        self._ic = 1
        os.dupterm_notify(None)
        # Native emitter: restore before returning to VM pending-exception checks.
        if self._ep == ep:
            self._ng = 1 if self._ph == 1 or self._ph == 2 else 0

    @micropython.native
    def _tick(self, _):
        if self._on:
            if self._ip:
                self._icb(None)
            else:
                self._wc(None)

    def _drain(self, q):
        while q.any():
            q.readinto(self._junk)

    def _reset(self, keep):
        global JOY_TS_MS
        self._ep = (self._ep + 1) & _LIMIT
        self._drain(self._r)
        self._drain(self._t)
        if not keep:
            self._drain(self._in)
        self._ix = self._iz = self._bp = self._bn = self._kind = 0
        self._sn = self._so = self._qh = self._qc = self._qo = 0
        self._td = self._qd = self._back = None
        self._ic = self._ip = self._ready = self._ng = self._og = 0
        self._ph = self._seq = self._size = self._rem = self._sink = self._fe = 0
        self._path = None
        self._fd = self._pd = None
        self._mtu = 23
        self._chunk = 20
        self._mc = self._ma = self._me = 0
        self._ms = 'waiting'
        self._md = self._cfg = None
        self._hc = self._force_cfg = self._fault = 0
        self._ad = 0
        self._fc = self._f is not None
        for i in range(4):
            JOY_POS[i] = 0
        JOY_TS_MS = None

    def start(self):
        global _ACTIVE
        if self._on:
            return self
        if self._closed:
            raise ValueError('Create a new driver after close')
        if _ACTIVE is not None or self._ble.active():
            raise OSError(_BUSY)
        prev = os.dupterm(self, self._slot)
        if prev is not None:
            os.dupterm(prev, self._slot)
            raise OSError(_BUSY)
        self._dt = 1
        _ACTIVE = self
        try:
            self._radio = 1
            self._ble.active(True)
            # ESP32 NimBLE uses synchronous events and has no config(rxbuf=...).
            self._ble.config(mtu=_MTU, gap_name=self._name.decode())
            ((self._ht, self._hr), (self._hj,)) = self._ble.gatts_register_services(_SERVICES)
            self._ble.gatts_set_buffer(self._hr, _ATTR, True)
            self._ble.gatts_set_buffer(self._hj, 8, False)
            self._ble.irq(self._irqcb)
            self._on = 1
            self._ble.gap_advertise(200000, adv_data=self._adv, resp_data=self._resp)
            self._tm = Timer(-1)
            self._tm.init(period=_PERIOD, mode=Timer.PERIODIC, callback=self._tc, hard=False)
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise
        # Old Blockly imports resolve to this module and its one joystick state.
        sys.modules['ble_repl_bletime'] = sys.modules[__name__]
        return self

    def close(self):
        global _ACTIVE
        if not hasattr(self, '_ep'):
            return
        self._on = 0
        self._conn = None
        self._reset(False)
        err = 0
        if self._tm is not None:
            try:
                self._tm.deinit()
                self._tm = None
            except Exception as e:
                err = e.args[0] if e.args else errno.EIO
        if self._dt:
            try:
                prev = os.dupterm(None, self._slot)
                if prev is not None and prev is not self:
                    os.dupterm(prev, self._slot)
                self._dt = 0
            except Exception as e:
                err = e.args[0] if e.args else errno.EIO
        if self._radio:
            try:
                self._ble.irq(None)
                self._ble.active(False)
                self._radio = 0
            except Exception as e:
                err = e.args[0] if e.args else errno.EIO
        if not self._close_file():
            err = self._last_file_error
        self._close_error = err
        if err:
            raise OSError(err)
        self._closed = 1
        if _ACTIVE is self:
            _ACTIVE = None

    def _schedule(self):
        if self._on and not self._ws:
            self._ws = 1
            try:
                micropython.schedule(self._wc, None)
            except RuntimeError:
                self._ws = 0
                self._sf = _inc(self._sf)

    def _stop(self):
        self._drain(self._r)
        self._ip = self._ng = 1
        if not self._is:
            self._is = 1
            try:
                micropython.schedule(self._icb, None)
            except RuntimeError:
                self._is = 0
                self._sf = _inc(self._sf)

    def _bad(self, reason, code=errno.ENOBUFS):
        self._fault = code
        self._reason = reason
        self._ng = self._og = 1

    def _mtu_event(self, mtu):
        self._mtu = max(23, mtu)
        self._chunk = min(_MAX, self._mtu - 3)
        self._mc = 1
        self._ms = 'exchanged'
        self._md = None
        self._cfg = utime.ticks_ms()
        self._hc = 0
        self._force_cfg = 1

    def _irq(self, event, data):
        global JOY_TS_MS
        if not self._on or event not in (1, 2, 3, 21):
            return
        h = data[0]
        if event == 1:
            if self._conn is not None:
                if h != self._conn:
                    self._other = h
                    self._schedule()
                return
            keep = h == self._eh
            mtu, confirmed, early_fault = self._em, self._ec, self._ef
            self._reset(keep)
            self._conn = h
            self._ready = bool(keep and self._in.any())
            self._eh = None
            self._ec = self._ef = 0
            now = utime.ticks_ms()
            self._cfg = now
            self._md = utime.ticks_add(now, 100)
            if keep and confirmed:
                self._mtu_event(mtu)
            if keep and early_fault:
                self._bad('early_rx', early_fault)
            self._schedule()
        elif event == 2:
            if h == self._conn:
                self._conn = None
                self._reset(False)
                self._ad = 1
                self._schedule()
            if h == self._eh:
                self._eh = None
                self._ec = self._ef = 0
                self._drain(self._in)
        elif event == 21:
            if h == self._conn:
                self._mtu_event(data[1])
                self._schedule()
            elif self._conn is None:
                if self._eh != h:
                    self._drain(self._in)
                    self._ef = 0
                self._eh, self._em, self._ec = h, data[1], 1
        elif event == 3:
            attr = data[1]
            if attr != self._hr and attr != self._hj:
                return
            try:
                p = self._ble.gatts_read(attr)
            except OSError as e:
                if h == self._conn:
                    self._bad('read', e.args[0])
                    self._schedule()
                elif self._conn is None:
                    if self._eh != h:
                        self._drain(self._in)
                        self._ec = 0
                    self._eh, self._ef = h, e.args[0]
                return
            if attr == self._hj:
                if h == self._conn and len(p) == 4:
                    for i in range(4):
                        v = p[i]
                        JOY_POS[i] = v - 256 if v & 128 else v
                    JOY_TS_MS = utime.ticks_ms()
                return
            if self._conn is not None and h != self._conn:
                return
            if self._conn is None:
                if h != self._eh:
                    self._drain(self._in)
                    self._ec = self._ef = 0
                self._eh = h
                if len(p) >= _ATTR or self._in.write(p) != len(p):
                    self._ef = errno.ENOBUFS
                return
            if len(p) >= _ATTR:
                self._bad('rx_attr')
            elif self._in.write(p) != len(p):
                self._bad('ingress')
            else:
                self._ready = 1
            self._schedule()

    def _queue(self, p):
        if self._qc == 8:
            self._bad('protocol')
            return
        i = (self._qh + self._qc) & 7
        n = len(p)
        self._q[i][:n] = p
        self._ql[i] = n
        self._qc += 1

    def _ack(self, ok, seq):
        self._queue(bytes((6 if ok else 21, seq & 255, seq >> 8)))

    def _caps(self):
        self._queue(('BLE Config mtu=%d chunk=%d\n' % (self._mtu, self._chunk)).encode())

    def _close_file(self):
        if self._f is not None:
            f = self._f
            try:
                f.close()
            except OSError as e:
                self._last_file_error = e.args[0]
                self._fc = 1
                return False
            if self._f is f:
                self._f = None
        self._fc = 0
        return True

    def _file_fail(self, code):
        self._fe = self._last_file_error = code
        self._ph = 3
        self._fd = None
        self._og = self._ng = 0
        self._fc = self._f is not None
        self._ack(False, self._seq)

    def _parents(self, path, ep):
        i = path.find('/', 1)
        while i >= 0:
            p = path[:i]
            try:
                st = os.stat(p)
                if self._ep != ep:
                    return False
                if not st[0] & 0x4000:
                    raise OSError(_NOTDIR)
            except OSError as e:
                if e.args[0] != errno.ENOENT:
                    raise
                os.mkdir(p)
            if self._ep != ep:
                return False
            i = path.find('/', i + 1)
        return True

    def _header(self):
        b = self._b
        raw = b[4]
        size = b[5] | (b[6] << 8) | (b[7] << 16)
        ep = self._ep
        if raw >= 252:
            if size:
                self._ack(False, self._seq)
            elif raw == 255:
                self._caps()
            elif raw == 253:
                self._ack(not self._fe, self._seq)
            elif raw == 254 or raw == 252:
                if not self._close_file():
                    if self._ep == ep:
                        self._file_fail(self._last_file_error)
                    return
                if self._ep != ep:
                    return
                self._ph = self._fe = 0
                self._fd = self._pd = None
                self._og = self._ng = 0
                self._drain(self._r)
                self._drain(self._t)
                self._sn = self._so = 0
                if raw == 252:
                    self._stop()
                    self._ack(True, 0xfffc)
                else:
                    self._ack(False, 0xffff)
            return
        try:
            path = bytes(self._v[8:self._bn]).decode('utf-8') if raw & 127 else 'data.bin'
            if '\x00' in path:
                raise ValueError()
        except (ValueError, UnicodeError):
            self._file_fail(errno.EINVAL)
            return
        sink = bool(raw & 128)
        if (self._path == path and self._size == size and self._sink == sink and
                ((self._ph == 2 and self._seq == 0 and self._rem == size) or
                 (self._ph == 4 and size == 0))):
            self._ack(True, 0xffff)
            return
        if not self._close_file():
            if self._ep == ep:
                self._file_fail(self._last_file_error)
            return
        if self._ep != ep:
            return
        self._path, self._size, self._rem, self._sink = path, size, size, sink
        self._ph = 1
        self._seq = self._fe = 0
        self._og = 1
        self._drain(self._t)
        self._sn = self._so = self._qh = self._qc = self._qo = 0
        self._td = self._qd = self._back = None
        self._fd = utime.ticks_add(utime.ticks_ms(), 10000)
        self._stop()

    def _open(self):
        if self._ip or self._ic:
            return
        ep = self._ep
        try:
            if not self._sink:
                if not self._parents(self._path, ep):
                    return
                self._f = open(self._path, 'wb')
                if self._ep != ep:
                    self._fc = 1
                    return
            self._ph = 2
            if self._rem == 0:
                self._finish(0xffff)
            else:
                self._ack(True, 0xffff)
        except OSError as e:
            if self._ep == ep:
                self._file_fail(e.args[0])

    def _finish(self, seq):
        ep = self._ep
        if not self._close_file():
            if self._ep == ep:
                self._file_fail(self._last_file_error)
            return
        if self._ep != ep:
            return
        try:
            if not self._sink and hasattr(os, 'sync'):
                os.sync()
        except OSError as e:
            if self._ep == ep:
                self._file_fail(e.args[0])
            return
        if self._ep != ep:
            return
        self._seq = (seq + 1) & 65535
        self._ph = 4
        self._og = self._ng = 0
        self._fd = None
        self._ack(True, seq)

    def _packet(self):
        b = self._b
        seq = b[0] | (b[1] << 8)
        n = b[2]
        if (self._ph != 2 or seq != self._seq or n == 0 or n > self._rem or
                (sum(self._v[:n + 3]) & 255) != b[n + 3]):
            self._ack(False, self._seq)
            return
        if seq == 65535 and n < self._rem:
            self._file_fail(_BIG)
            return
        ep = self._ep
        if not self._sink:
            try:
                if self._f.write(self._v[3:n + 3]) != n:
                    raise OSError(errno.EIO)
            except OSError as e:
                if self._ep == ep:
                    self._file_fail(e.args[0])
                return
        if self._ep != ep:
            return
        self._rem -= n
        self._fd = utime.ticks_add(utime.ticks_ms(), 10000)
        if self._rem == 0:
            self._finish(seq)
        else:
            self._seq = (seq + 1) & 65535
            if seq & 3 == 3:
                self._ack(True, seq)

    def _text(self, p):
        if self._r.write(p) != len(p):
            self._bad('rx')

    def _parse(self, limit):
        ep = self._ep
        used = 0
        while used < limit and not self._fault and self._ep == ep and self._ph != 1:
            if self._ix == self._iz:
                self._iz = self._in.readinto(self._ib, min(_ATTR, limit - used))
                self._ix = 0
                if not self._iz:
                    break
            i, end = self._ix, self._iz
            if self._kind == 0:
                c = self._ib[i]
                if self._ph == 0:
                    fa = self._ib.find(b'\xfa', i, end)
                    cc = self._ib.find(b'\x03', i, end)
                    j = end
                    if fa >= 0:
                        j = fa
                    if cc >= 0 and cc < j:
                        j = cc
                    if j > i:
                        self._text(self._iv[i:j])
                        used += j - i
                        self._ix = j
                        continue
                    if c == 3:
                        self._stop()
                        self._ix += 1
                        used += 1
                        continue
                    self._kind = 1
                    self._bp = 1
                    self._b[0] = c
                    self._ix += 1
                    used += 1
                    self._bn = 4
                else:
                    # Successful IDE uploads are followed directly by run_code().
                    # Probe text/control prefixes, but preserve the last DATA retry:
                    # its sequence bytes can also be printable ASCII or Ctrl+A/B/C/D.
                    self._kind = 5 if self._ph in (3, 4) and (c in (1, 2, 3, 4, 13) or 32 <= c < 127) else 2
                    self._bp = 0
                    self._bn = 2 if self._kind == 5 else 4
                self._pd = utime.ticks_add(utime.ticks_ms(), 1000)
                continue
            if self._kind == 1:
                # Only the four-byte magic probe is bytewise; payloads are bulk copies.
                if self._ib[i] != _MAGIC[self._bp]:
                    self._text(self._v[:self._bp])
                    self._kind = self._bp = 0
                    self._pd = None
                    continue
                self._b[self._bp] = self._ib[i]
                self._bp += 1
                self._ix += 1
                used += 1
                if self._bp == 4:
                    self._kind, self._bn = 3, 8
                continue
            take = min(self._bn - self._bp, end - i, limit - used)
            self._v[self._bp:self._bp + take] = self._iv[i:i + take]
            self._bp += take
            self._ix += take
            used += take
            if self._bp < self._bn:
                continue
            if self._kind == 5:
                seq = self._b[0] | (self._b[1] << 8)
                a, b = self._b[0], self._b[1]
                repl = ((a in (1, 2, 3, 4) and b != 0) or
                        (a == 13 and (b in (1, 2, 3, 4, 10) or 32 <= b < 127)) or
                        (32 <= a < 127 and (b in (9, 10, 13) or 32 <= b < 127)))
                if repl and seq != (self._seq - 1) & 65535:
                    self._ph = 0
                    if a == 3:
                        self._stop()
                    else:
                        self._text(self._v[:1])
                    if b == 3:
                        self._stop()
                    else:
                        self._text(self._v[1:2])
                    self._kind = self._bp = self._bn = 0
                    self._pd = None
                    continue
                self._kind, self._bn = 2, 4
                continue
            if self._kind == 2 and self._bn == 4:
                if self._b[:4] == _MAGIC and self._seq != 0xcefa:
                    self._kind, self._bn = 3, 8
                    continue
                self._bn = self._b[2] + 4
                if self._bn > 4:
                    continue
            if self._kind == 3:
                raw = self._b[4]
                if raw < 252 and (raw & 127) > 48:
                    self._file_fail(errno.EINVAL)
                else:
                    self._kind = 4
                    self._bn = 8 if raw >= 252 else 8 + (raw & 127)
                    if self._bp < self._bn:
                        continue
            if self._kind == 4:
                self._header()
            elif self._kind == 2:
                self._packet()
            if self._ep != ep:
                return
            self._bp = self._bn = self._kind = 0
            self._pd = None

    def _exchange(self, now):
        if self._md is None or utime.ticks_diff(now, self._md) < 0:
            return
        self._md = None
        if self._mc:
            return
        if self._mx is None:
            self._ms = 'unavailable'
            return
        ep = self._ep
        self._ma += 1
        try:
            self._mx(self._conn)
        except OSError as e:
            if self._ep != ep or self._mc:
                return
            self._me = e.args[0]
            if self._me == errno.EALREADY:
                self._ms = 'already_requested'
            elif self._me in (_BUSY, errno.ENOMEM) and self._ma < 3:
                self._md = utime.ticks_add(now, 100)
                self._ms = 'retry'
            else:
                self._ms = 'failed'
        else:
            if self._ep == ep and not self._mc:
                self._ms = 'requested'

    def _send(self, now):
        budget = _BUDGET
        calls = 0
        ep = self._ep
        while budget and calls < (_BUDGET + self._chunk - 1) // self._chunk and self._conn is not None and not self._fault:
            pri = self._qc != 0
            deadline = self._qd if pri else self._td
            if deadline is not None and utime.ticks_diff(now, deadline) >= 0:
                self._bad('notify_timeout', errno.ETIMEDOUT)
                return
            if self._back is not None and utime.ticks_diff(now, self._back) < 0:
                return
            if pri:
                v = self._q[self._qh]
                pos, end = self._qo, self._ql[self._qh]
            else:
                if not self._ready:
                    return
                if self._sn == self._so:
                    self._sn = self._t.readinto(self._sb, min(budget, (_MAX // self._chunk) * self._chunk))
                    self._so = 0
                if not self._sn:
                    return
                v, pos, end = self._sv, self._so, self._sn
            n = min(self._chunk, budget, end - pos)
            try:
                self._ble.gatts_notify(self._conn, self._ht, v[pos:pos + n])
            except OSError as e:
                if self._ep != ep:
                    return
                self._ne = _inc(self._ne)
                code = e.args[0]
                if code not in (errno.ENOMEM, errno.EAGAIN, _BUSY):
                    self._bad('notify', code)
                    return
                if deadline is None:
                    deadline = utime.ticks_add(now, 2000)
                if pri:
                    self._qd = deadline
                else:
                    self._td = deadline
                self._back = utime.ticks_add(now, 20)
                return
            if self._ep != ep:
                return
            calls += 1
            self._back = None
            budget -= n
            if pri:
                self._qd = None
                self._qo += n
                if self._qo == end:
                    self._qo = 0
                    self._qh = (self._qh + 1) & 7
                    self._qc -= 1
            else:
                self._td = None
                self._so += n

    def _work(self, _):
        self._ws = 0
        if not self._on or self._busy:
            return
        self._busy = 1
        try:
            if self._other is not None:
                self._ble.gap_disconnect(self._other)
                self._other = None
            if self._fc and not self._close_file():
                self._bad('file_close', self._last_file_error)
            if self._ad:
                self._ble.gap_advertise(200000, adv_data=self._adv, resp_data=self._resp)
                self._ad = 0
            if self._conn is None:
                return
            if self._fault:
                h, ep = self._conn, self._ep
                try:
                    self._ble.gap_disconnect(h)
                except OSError as e:
                    self._close_error = e.args[0]
                    return
                if self._ep == ep:
                    self._conn = None
                    self._reset(False)
                    self._ad = 1
                return
            if self._ip:
                return
            ep = self._ep
            now = utime.ticks_ms()
            self._exchange(now)
            if self._ep != ep:
                return
            if self._cfg is not None and utime.ticks_diff(now, self._cfg) >= 0:
                if self._force_cfg or (not self._ready and self._hc < 60):
                    self._caps()
                    self._hc += 1
                self._force_cfg = 0
                self._cfg = utime.ticks_add(now, 500) if not self._ready and self._hc < 60 else None
            if self._fd is not None and utime.ticks_diff(now, self._fd) >= 0:
                self._file_fail(errno.ETIMEDOUT)
                self._kind = self._bp = self._bn = 0
            if self._pd is not None and utime.ticks_diff(now, self._pd) >= 0:
                if self._kind == 1:
                    self._text(self._v[:self._bp])
                elif self._kind == 5 and self._bp == 1:
                    self._ph = 0
                    if self._b[0] == 3:
                        self._stop()
                    else:
                        self._text(self._v[:1])
                elif self._ph == 2:
                    self._ack(False, self._seq)
                else:
                    self._file_fail(errno.ETIMEDOUT)
                self._kind = self._bp = self._bn = 0
                self._pd = None
            if self._ph == 1:
                self._open()
            if self._ep != ep:
                return
            self._parse(1024)
            if self._ep == ep and not self._fault:
                self._send(now)
        finally:
            self._busy = 0

    def stats(self):
        return {'connected': self._conn is not None, 'ready': bool(self._ready),
                'tx_ready': bool(self._ready), 'mtu': self._mtu, 'notify_chunk': self._chunk,
                'rx_pending': self._r.any(), 'tx_pending': self._t.any() + self._sn - self._so,
                'preferred_mtu': _MTU, 'negotiated_mtu': self._mtu,
                'mtu_confirmed': bool(self._mc), 'chunk': self._chunk,
                'mtu_attempts': self._ma, 'mtu_status': self._ms, 'mtu_error': self._me,
                'rx_queued': self._r.any(), 'ingress_queued': self._in.any() + self._iz - self._ix,
                'tx_queued': self._t.any() + self._sn - self._so,
                'tx_dropped': self._drop, 'protocol_queued': self._qc,
                'schedule_full': self._sf, 'notify_errors': self._ne,
                'phase': self._ph, 'target': self._path, 'sequence': self._seq,
                'remaining': self._rem, 'file_error': self._fe,
                'last_file_error': self._last_file_error, 'fault': self._reason,
                'close_error': self._close_error}

    def mem_usage(self):
        import gc
        gc.collect()
        return {'rx_ring': _RX, 'tx_ring': _TX, 'rx_attr': _ATTR,
                'tx_chunk_buf': _MAX, 'gc_free': gc.mem_free(), 'gc_alloc': gc.mem_alloc()}


IDEBLERepl = BLENUSRepl


def get_active():
    return _ACTIVE


def start_ble_repl(*, name=b'MPY-REPL', slot=0):
    if _ACTIVE is not None:
        raise OSError(_BUSY)
    return BLENUSRepl(name=name, slot=slot).start()


start_ble_repl_bletime = start_ble_repl
