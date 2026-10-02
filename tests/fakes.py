# SPDX-License-Identifier: MIT
"""Deterministic host model. Native emitter, VM and radio are NOT emulated hardware."""
import builtins
import collections
import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import types
import errno

ROOT = pathlib.Path(__file__).resolve().parents[1] / 'firmware'


class RingIO:
    def __init__(self, size):
        self.capacity = size if isinstance(size, int) else len(size) - 1
        self.data = collections.deque()
        self.writes = []

    def any(self):
        return len(self.data)

    def write(self, data):
        n = min(len(data), self.capacity - len(self.data))
        self.data.extend(data[:n])
        self.writes.append(n)
        return n

    def readinto(self, buf, nbytes=None):
        n = min(len(buf), len(self.data), len(buf) if nbytes is None else nbytes)
        for i in range(n):
            buf[i] = self.data.popleft()
        return n


class UUID:
    def __init__(self, value):
        self.value = value

    def __bytes__(self):
        return bytes.fromhex(self.value.replace('-', ''))[::-1]


class BLE:
    def __init__(self, ctx):
        self.ctx = ctx
        self.enabled = False
        self.handler = None
        self.configs = {}
        self.attrs = {}
        self.buffers = {}
        self.notifications = []
        self.notify_hook = None
        self.notify_error = None
        self.exchange_hook = None
        self.exchange_error = None
        self.exchange_calls = []
        self.disconnect_error = None
        self.active_error = None
        self.start_error = None
        self.adverts = []
        self.read_error = None

    def active(self, value=None):
        if value is None:
            return self.enabled
        if self.active_error:
            raise OSError(self.active_error)
        self.enabled = value

    def config(self, **kw):
        # ESP32 NimBLE USE_SYNC_EVENTS omits rxbuf; reject unsupported params.
        if any(key not in ('mtu', 'gap_name') for key in kw):
            raise ValueError('unknown config param')
        self.configs.update(kw)

    def irq(self, handler):
        self.handler = handler

    def gatts_register_services(self, services):
        self.services = services
        if self.start_error:
            raise OSError(self.start_error)
        return ((10, 11), (12,))

    def gatts_set_buffer(self, handle, size, append):
        self.buffers[handle] = (size, append)

    def gatts_read(self, handle):
        if self.read_error:
            raise OSError(self.read_error)
        return self.attrs.pop(handle, b'')

    def gap_advertise(self, interval, **kw):
        self.adverts.append((interval, kw))

    def gattc_exchange_mtu(self, handle):
        self.exchange_calls.append(handle)
        if self.exchange_hook:
            self.exchange_hook()
        if self.exchange_error:
            raise OSError(self.exchange_error)

    def gatts_notify(self, handle, attr, data):
        data = bytes(data)
        if self.notify_hook:
            self.notify_hook()
        if self.notify_error:
            raise OSError(self.notify_error)
        self.notifications.append((handle, attr, data))

    def gap_disconnect(self, handle):
        if self.disconnect_error:
            raise OSError(self.disconnect_error)
        self.ctx.event(2, (handle, 0, b''))


class Timer:
    PERIODIC = 1

    def __init__(self, ctx, number):
        self.ctx = ctx
        self.number = number
        self.kw = {}
        self.stopped = False
        self.fail = False

    def init(self, **kw):
        self.kw = kw

    def deinit(self):
        if self.fail:
            raise OSError(errno.EIO)
        self.stopped = True


class File:
    def __init__(self, ctx, f, path):
        self.ctx, self.f, self.path = ctx, f, path
        self.close_count = 0

    def write(self, data):
        self.ctx.check_io('write')
        if self.ctx.write_error:
            raise OSError(self.ctx.write_error)
        n = self.f.write(data)
        if self.ctx.write_hook:
            self.ctx.write_hook()
        return n - 1 if self.ctx.short_write else n

    def close(self):
        self.ctx.check_io('close')
        self.close_count += 1
        if self.ctx.close_failures:
            self.ctx.close_failures -= 1
            raise OSError(errno.EIO)
        self.f.close()
        if self.ctx.close_hook:
            self.ctx.close_hook()


class Context:
    MODULUS = 1 << 30

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.now = 0
        self.queue = collections.deque()
        self.queue_size = 8
        self.slot = None
        self.irq = False
        self.interrupts = 0
        self.interrupt_enabled = True
        self.stdin = bytearray()
        self.notify_hook = None
        self.io_calls = []
        self.open_count = 0
        self.write_error = self.sync_error = self.close_failures = 0
        self.short_write = False
        self.open_hook = self.write_hook = self.close_hook = self.sync_hook = None
        self.ble = BLE(self)
        self.timer = None
        self.old_modules = {}
        self.modules = {}
        mp = types.ModuleType('micropython')
        mp.const = lambda n: n
        mp.RingIO = RingIO
        mp.native = self.native
        mp.schedule = self.schedule
        bt = types.ModuleType('bluetooth')
        bt.UUID = UUID
        bt.BLE = lambda: self.ble
        tm = types.ModuleType('utime')
        tm.ticks_ms = lambda: self.now
        tm.ticks_add = lambda t, d: (t + d) % self.MODULUS
        tm.ticks_diff = lambda a, b: (a - b + self.MODULUS // 2) % self.MODULUS - self.MODULUS // 2
        tm.sleep_ms = lambda n: None
        tm.sleep = lambda n: None
        mc = types.ModuleType('machine')
        def timer(n):
            self.timer = Timer(self, n)
            return self.timer
        timer.PERIODIC = Timer.PERIODIC
        mc.Timer = timer
        mo = types.ModuleType('os')
        mo.dupterm = self.dupterm
        mo.dupterm_notify = self.dupterm_notify
        mo.stat = lambda path: self.fs('stat', path)
        mo.mkdir = lambda path: self.fs('mkdir', path)
        mo.sync = self.sync
        # Firmware errno exports are smaller than CPython's; match the real C3.
        me = types.ModuleType('errno')
        for key in ('EACCES', 'EADDRINUSE', 'EAGAIN', 'EALREADY', 'EBADF',
                    'ECONNABORTED', 'ECONNREFUSED', 'ECONNRESET', 'EEXIST',
                    'EHOSTUNREACH', 'EINPROGRESS', 'EINVAL', 'EIO', 'EISDIR',
                    'ENOBUFS', 'ENODEV', 'ENOENT', 'ENOMEM', 'ENOTCONN',
                    'EOPNOTSUPP', 'EPERM', 'ETIMEDOUT'):
            setattr(me, key, getattr(errno, key))
        self.modules = {'micropython': mp, 'bluetooth': bt, 'utime': tm,
                        'machine': mc, 'os': mo, 'errno': me}

    @staticmethod
    def native(fn):
        fn.is_native = True
        return fn

    def install(self):
        for key, value in self.modules.items():
            self.old_modules[key] = sys.modules.get(key)
            sys.modules[key] = value
        for key in ('ble_repl', 'ble_repl_bletime', 'bletime'):
            self.old_modules[key] = sys.modules.pop(key, None)
        spec = importlib.util.spec_from_file_location('ble_repl', ROOT / 'ble_repl.py')
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules['ble_repl'] = self.mod
        spec.loader.exec_module(self.mod)
        self.mod.open = self.open
        # Keep the driver's OS reference fake without redirecting host traceback/file APIs.
        sys.modules['os'] = self.old_modules['os']
        return self

    def start(self, **kw):
        self.dev = self.mod.start_ble_repl(**kw)
        return self.dev

    def restore(self):
        if hasattr(self, 'dev'):
            self.dev.close()
        # Objects must finish before restoring OS modules used by IOBase finalizers.
        for key, value in self.old_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
        self.tmp.cleanup()

    def schedule(self, fn, arg):
        if len(self.queue) >= self.queue_size:
            raise RuntimeError('schedule queue full')
        self.queue.append((fn, arg))

    def drain(self):
        for _ in range(100):
            if not self.queue:
                return
            fn, arg = self.queue.popleft()
            fn(arg)
        raise AssertionError('unbounded scheduling')

    def tick(self, n=1):
        for _ in range(n):
            self.drain()
            self.now = (self.now + 20) % self.MODULUS
            if self.timer and not self.timer.stopped:
                self.timer.kw['callback'](self.timer)
            self.drain()

    def event(self, event, data):
        self.irq = True
        try:
            self.ble.handler(event, data)
        finally:
            self.irq = False

    def connect(self, handle=1, mtu=247):
        self.event(1, (handle, 0, b''))
        if mtu is not None:
            self.event(21, (handle, mtu))
        self.drain()

    def rx(self, data, handle=1, attr=11, drain=True):
        cap, append = self.ble.buffers[attr]
        old = self.ble.attrs.get(attr, b'') if append else b''
        self.ble.attrs[attr] = (old + bytes(data))[:cap]
        self.event(3, (handle, attr))
        if drain:
            self.drain()

    def stream(self, data, widths=(113,), steps=1):
        i = k = 0
        while i < len(data):
            n = widths[k % len(widths)]
            self.rx(data[i:i + n])
            self.tick(steps)
            i += n
            k += 1

    def dupterm(self, obj, index=0):
        if index != 0:
            raise ValueError('invalid dupterm index')
        prev, self.slot = self.slot, obj
        return prev

    def dupterm_notify(self, _):
        if self.notify_hook:
            self.notify_hook()
        b = bytearray(1)
        while self.slot is not None and self.slot.readinto(b):
            if b[0] == 3 and self.interrupt_enabled:
                self.interrupts += 1
                break
            if len(self.stdin) < 259:
                self.stdin.extend(b)

    def check_io(self, operation):
        if self.irq:
            raise AssertionError('file I/O in BLE IRQ')
        self.io_calls.append(operation)

    def resolve(self, path):
        target = self.root / path.lstrip('/')
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise AssertionError('test path outside temporary root')
        return target

    def fs(self, op, path):
        self.check_io(op)
        return getattr(os, op)(self.resolve(path))

    def open(self, path, mode):
        self.check_io('open')
        self.open_count += 1
        f = File(self, builtins.open(self.resolve(path), mode), path)
        if self.open_hook:
            self.open_hook()
        return f

    def sync(self):
        self.check_io('sync')
        if self.sync_error:
            raise OSError(self.sync_error)
        if self.sync_hook:
            self.sync_hook()

    def output(self):
        return b''.join(n[2] for n in self.ble.notifications)

    def clear_output(self):
        self.ble.notifications.clear()


def header(name, size, sink=False):
    name = name.encode() if isinstance(name, str) else name
    return b'\xfa\xce\xb0\x0c' + bytes([len(name) | (128 if sink else 0)]) + size.to_bytes(3, 'little') + name


def packet(seq, data):
    body = seq.to_bytes(2, 'little') + bytes([len(data)]) + data
    return body + bytes([sum(body) & 255])


def control(op):
    return b'\xfa\xce\xb0\x0c' + bytes([op, 0, 0, 0])
