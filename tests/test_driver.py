# SPDX-License-Identifier: MIT
import ast
import errno
import hashlib
import importlib.util
import pathlib
import random
import sys
import unittest
from fakes import Context, ROOT, header, packet, control


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.c = Context().install()
        self.d = self.c.start()
        self.c.connect()
        self.c.rx(b'\r')
        self.d._drain(self.d._r)
        self.c.clear_output()

    def tearDown(self):
        self.c.ble.active_error = None
        self.c.ble.disconnect_error = None
        self.c.close_failures = 0
        self.c.restore()

    def start_file(self, name='data.bin', size=6, sink=False):
        self.c.rx(header(name, size, sink))
        self.c.tick(2)
        self.assertIn(b'\x06\xff\xff', self.c.output())
        self.c.clear_output()

    def read_rx(self):
        b = bytearray(2048)
        n = self.d.readinto(b)
        return bytes(b[:n]) if n else b''

    def reconnect(self, h=1):
        self.c.event(2, (self.d._conn, 0, b''))
        self.c.connect(h)

    def test_fixed_buffers_and_soft_timer(self):
        self.assertEqual((self.d._r.capacity, self.d._t.capacity, self.d._in.capacity), (2048, 2048, 4096))
        self.assertEqual(self.c.timer.number, -1)
        self.assertEqual(self.c.timer.kw['period'], 20)
        self.assertFalse(self.c.timer.kw['hard'])
        self.assertEqual(len(self.d._q), 8)
        self.assertEqual(len(self.d._q[0]), 64)

    def test_native_boundary_methods(self):
        for name in ('readinto', 'write', 'ioctl', '_interrupt', '_tick'):
            self.assertTrue(getattr(type(self.d), name).is_native, name)

    def test_raw_controls_and_long_input_bulk(self):
        data = b'\x01\x02\x04\x05A\x01' + b'x' * 1794
        self.c.stream(data, (251, 179, 101))
        self.assertEqual(self.read_rx(), data)
        self.assertEqual(self.c.stdin, b'')
        self.assertGreater(max(self.d._r.writes), 100)

    def test_ctrlc_priority_keeps_tail_and_dupterm(self):
        self.c.rx(b'x' * 400)
        self.c.rx(b'\x03print(42)\r')
        self.assertEqual(self.c.interrupts, 1)
        self.assertIs(self.c.slot, self.d)
        self.assertEqual(self.read_rx(), b'print(42)\r')
        self.assertEqual(self.c.stdin, b'')

    def test_interrupt_disabled_remains_native_input(self):
        self.c.interrupt_enabled = False
        self.c.rx(b'\x03tail')
        self.assertEqual(self.c.interrupts, 0)
        self.assertEqual(self.c.stdin, b'\x03')
        self.assertEqual(self.read_rx(), b'tail')

    def test_native_interrupt_reentrant_session(self):
        def reconnect():
            self.c.notify_hook = None
            self.reconnect()
            self.d._ng = 1
        self.c.notify_hook = reconnect
        self.c.rx(b'\x03')
        self.assertEqual(self.d._ng, 1)
        self.assertFalse(self.d._ic)

    def test_magic_mismatch_and_overlap(self):
        data = b'a\xfa\xceQ\xfa\xfa\xce\xb0Zend'
        self.c.stream(data, (1,))
        self.assertEqual(self.read_rx(), data)

    def test_partial_magic_timeout_replays(self):
        self.c.rx(b'\xfa\xce')
        self.c.tick(51)
        self.assertEqual(self.read_rx(), b'\xfa\xce')

    def test_partial_header_timeout_stays_out_of_repl(self):
        self.c.rx(header('x.bin', 3)[:-2])
        self.c.tick(51)
        self.assertEqual(self.d._fe, errno.ETIMEDOUT)
        self.assertEqual(self.read_rx(), b'')

    def test_fragmented_and_coalesced_upload(self):
        data = bytes(range(256)) * 4
        wire = header('/gfx/sub/all.bin', len(data))
        wire += b''.join(packet(i, data[j:j+240]) for i, j in enumerate(range(0, len(data), 240)))
        self.c.stream(wire, (1, 7, 113, 31, 251, 5))
        self.c.tick(3)
        self.assertEqual(self.c.resolve('/gfx/sub/all.bin').read_bytes(), data)
        self.assertEqual(self.read_rx(), b'')
        self.assertEqual(self.d._ph, 4)
        self.assertEqual(self.d._seq, 5)

    def test_random_fragment_boundaries(self):
        rng = random.Random(19736)
        data = bytes(rng.randrange(256) for _ in range(8192))
        wire = header('/fixture.bin', len(data)) + b''.join(
            packet(i, data[j:j+240]) for i, j in enumerate(range(0, len(data), 240)))
        self.c.stream(wire, tuple(rng.randrange(1, 252) for _ in range(80)))
        self.c.tick(3)
        got = self.c.resolve('/fixture.bin').read_bytes()
        self.assertEqual(hashlib.sha256(got).digest(), hashlib.sha256(data).digest())

    def test_header_does_not_open_before_native_stop(self):
        self.c.rx(header('/file.bin', 3) + packet(0, b'abc'), drain=False)
        fn, arg = self.c.queue.popleft()
        fn(arg)
        self.assertEqual(self.c.open_count, 0)
        self.assertEqual(self.d._ph, 1)
        self.assertTrue(self.d._ip)
        self.assertEqual(self.d._iz - self.d._ix, 7)
        self.c.drain()
        self.assertEqual(self.c.open_count, 0)
        self.assertEqual(self.c.interrupts, 1)
        self.c.tick(2)
        self.assertEqual(self.c.resolve('/file.bin').read_bytes(), b'abc')

    def test_header_retry_does_not_reopen(self):
        self.start_file('/retry.bin', 3)
        self.c.stream(header('/retry.bin', 3), (1,))
        self.assertEqual(self.c.open_count, 1)
        self.assertIn(b'\x06\xff\xff', self.c.output())
        self.c.rx(packet(0, b'abc'))
        self.assertEqual(self.c.resolve('/retry.bin').read_bytes(), b'abc')

    def test_other_header_is_new_transfer(self):
        self.start_file('/old.bin', 9)
        self.c.rx(packet(0, b'abc'))
        self.c.rx(header('/new.bin', 3))
        self.c.tick(2)
        self.c.rx(packet(0, b'xyz'))
        self.assertEqual(self.c.resolve('/old.bin').read_bytes(), b'abc')
        self.assertEqual(self.c.resolve('/new.bin').read_bytes(), b'xyz')

    def test_batch_backup_asset_program(self):
        for name, data in [('/backup.blk.gz', b'\x1f\x8bbackup'), ('/gfx/font.mfnt', bytes(range(256))), ('/idecode', b'print(42)')]:
            self.start_file(name, len(data))
            self.c.stream(b''.join(packet(i, data[j:j+240]) for i, j in enumerate(range(0, len(data), 240))))
            self.assertEqual(self.c.resolve(name).read_bytes(), data)

    def test_parent_file_reports_enotdir(self):
        self.c.resolve('/gfx').write_bytes(b'keep')
        self.c.rx(header('/gfx/font.bin', 3))
        self.c.tick(2)
        self.assertEqual(self.d._fe, errno.ENOTDIR)
        self.assertEqual(self.c.resolve('/gfx').read_bytes(), b'keep')
        self.reconnect()
        self.assertEqual(self.d.stats()['last_file_error'], errno.ENOTDIR)

    def test_empty_file_commit_and_retry(self):
        self.start_file('/empty.bin', 0)
        self.assertEqual(self.c.resolve('/empty.bin').read_bytes(), b'')
        self.c.rx(header('/empty.bin', 0))
        self.assertEqual(self.c.open_count, 1)
        self.assertIn(b'\x06\xff\xff', self.c.output())

    def test_sink_never_touches_filesystem(self):
        self.start_file('/not/created.bin', 3, True)
        self.c.rx(packet(0, b'abc'))
        self.assertEqual(self.c.io_calls, [])
        self.assertIn(b'\x06\x00\x00', self.c.output())

    def test_ack_cadence_final_and_status(self):
        self.start_file('/acks.bin', 27)
        for seq in range(9):
            self.c.rx(packet(seq, b'abc'))
        self.assertEqual(self.c.output(), b'\x06\x03\x00\x06\x07\x00\x06\x08\x00')
        self.c.clear_output()
        self.c.rx(control(253))
        self.assertEqual(self.c.output(), b'\x06\x09\x00')

    def test_checksum_sequence_and_length_errors(self):
        self.start_file('/bad.bin', 3)
        for data in (packet(1, b'abc'), packet(0, b'abcd'), packet(0, b'abc')[:-1] + b'\x00', packet(0, b'')):
            with self.subTest(data=data):
                self.c.clear_output()
                self.c.rx(data)
                self.assertEqual(self.d._seq, 0)
                self.assertEqual(self.c.output(), b'\x15\x00\x00')
        self.c.rx(packet(0, b'abc'))
        self.assertEqual(self.c.resolve('/bad.bin').read_bytes(), b'abc')

    def test_duplicate_data_does_not_write_twice(self):
        self.start_file('/dupe.bin', 6)
        self.c.rx(packet(0, b'abc'))
        self.c.rx(packet(0, b'abc'))
        self.assertIn(b'\x15\x01\x00', self.c.output())
        self.c.rx(packet(1, b'def'))
        self.c.rx(packet(1, b'def'))
        self.c.rx(control(253))
        self.assertEqual(self.c.resolve('/dupe.bin').read_bytes(), b'abcdef')
        self.assertTrue(self.c.output().endswith(b'\x15\x02\x00\x06\x02\x00'))

    def test_cr_fragment_resumes_repl_after_eof(self):
        self.start_file('/resume.bin', 3)
        self.c.rx(packet(0, b'abc'))
        self.c.stream(b'\r\x02print(42)\r', (1,))
        self.assertEqual(self.read_rx(), b'\r\x02print(42)\r')

    def test_duplicate_sequence_13_is_not_repl_escape(self):
        self.start_file('/thirteen.bin', 3)
        self.d._seq = 13
        self.c.rx(packet(13, b'abc'))
        self.c.stream(packet(13, b'abc'), (1,))
        self.assertEqual(self.read_rx(), b'')
        self.assertEqual(self.c.resolve('/thirteen.bin').read_bytes(), b'abc')

    def test_filemanager_ctrlc_crlf_resumes_after_eof(self):
        self.start_file('/fm.bin', 3)
        self.c.rx(packet(0, b'abc'))
        self.c.rx(b'\x03')
        self.c.rx(b'\r\n')
        self.assertEqual(self.c.interrupts, 2)
        self.assertEqual(self.read_rx(), b'\r\n')

    def test_lone_ctrlc_after_eof_has_bounded_wait(self):
        self.start_file('/lone.bin', 3)
        self.c.rx(packet(0, b'abc'))
        self.c.rx(b'\x03')
        self.c.tick(51)
        self.assertEqual(self.c.interrupts, 2)
        self.assertEqual(self.d._ph, 0)

    def test_sequence_rollover_before_eof_is_rejected(self):
        self.start_file('/wrap.bin', 6)
        self.d._seq = 65535
        self.c.rx(packet(65535, b'abc'))
        self.assertEqual(self.d._fe, errno.EFBIG)
        self.assertNotIn(b'\x06\xff\xff', self.c.output())

    def test_second_connection_is_rejected_without_resetting_primary(self):
        self.c.event(1, (2, 0, b''))
        self.c.drain()
        self.assertEqual(self.d._conn, 1)

    def test_magic_collision_is_expected_data(self):
        self.start_file('/magic.bin', 176)
        self.d._seq = 0xcefa
        data = b'\x0c' + b'x' * 175
        self.assertTrue(packet(0xcefa, data).startswith(b'\xfa\xce\xb0\x0c'))
        self.c.stream(packet(0xcefa, data), (1, 17, 113))
        self.assertEqual(self.c.resolve('/magic.bin').read_bytes(), data)

    def test_cancel_keeps_partial_target_and_parents(self):
        self.start_file('/gfx/partial.bin', 6)
        self.c.rx(packet(0, b'abc'))
        self.c.rx(control(254))
        self.assertEqual(self.c.resolve('/gfx/partial.bin').read_bytes(), b'abc')
        self.assertEqual(self.d._ph, 0)
        self.assertIsNone(self.d._f)
        self.c.rx(b'print(42)\r')
        self.assertEqual(self.read_rx(), b'print(42)\r')

    def test_stop_ack_and_print_flood(self):
        self.d.write(b'x' * 100000)
        self.assertGreater(self.d.stats()['tx_dropped'], 90000)
        self.c.rx(control(252))
        self.c.tick(2)
        self.assertEqual(self.c.interrupts, 1)
        self.assertIn(b'\x06\xfc\xff', self.c.output())
        self.assertIs(self.c.slot, self.d)

    def test_file_error_uses_failed_sequence_and_status_not_success(self):
        for kind in ('write', 'short', 'close', 'sync'):
            with self.subTest(kind=kind):
                self.start_file('/error.bin', 6)
                self.c.rx(packet(0, b'abc'))
                self.c.write_error = errno.ENOSPC if kind == 'write' else 0
                self.c.short_write = kind == 'short'
                self.c.close_failures = 1 if kind == 'close' else 0
                self.c.sync_error = errno.EIO if kind == 'sync' else 0
                self.c.rx(packet(1, b'def'))
                self.assertNotIn(b'\x06\x01\x00', self.c.output())
                self.assertIn(b'\x15\x01\x00', self.c.output())
                self.c.clear_output()
                self.c.rx(control(253))
                self.assertEqual(self.c.output(), b'\x15\x01\x00')
                self.c.write_error = self.c.sync_error = self.c.close_failures = 0
                self.c.short_write = False
                self.c.rx(control(254))
                self.c.clear_output()

    def test_reentrant_file_io_does_not_ack_new_session(self):
        for kind in ('open', 'write', 'close', 'sync'):
            with self.subTest(kind=kind):
                if kind != 'open':
                    self.start_file('/epoch.bin', 3)
                def hook():
                    setattr(self.c, kind + '_hook', None)
                    self.reconnect()
                setattr(self.c, kind + '_hook', hook)
                self.c.clear_output()
                if kind == 'open':
                    self.c.rx(header('/epoch.bin', 3))
                    self.c.tick(2)
                else:
                    self.c.rx(packet(0, b'abc'))
                    self.c.tick(2)
                self.assertNotIn(b'\x06\x00\x00', self.c.output())
                self.assertNotIn(b'\x06\xff\xff', self.c.output())
                self.assertEqual(self.d._seq, 0)
                self.assertIsNone(self.d._f)

    def test_timeout_at_zero_and_rollover(self):
        self.c.now = self.c.MODULUS - 10000
        self.start_file('/timeout.bin', 6)
        self.assertLess(self.d._fd, 100)
        self.c.tick(501)
        self.assertEqual(self.d._fe, errno.ETIMEDOUT)
        self.c.rx(control(254))
        self.c.now = self.c.MODULUS - 1000
        self.c.rx(b'\xfa')
        self.assertEqual(self.d._pd, 0)
        self.c.tick(50)
        self.assertEqual(self.read_rx(), b'\xfa')

    def test_partial_data_packet_timeout_and_retry(self):
        self.start_file('/fragment.bin', 3)
        self.c.rx(packet(0, b'abc')[:5])
        self.c.tick(51)
        self.assertIn(b'\x15\x00\x00', self.c.output())
        self.c.rx(packet(0, b'abc'))
        self.assertEqual(self.c.resolve('/fragment.bin').read_bytes(), b'abc')

    def test_attr_ingress_and_repl_overflow_are_faults(self):
        for kind in ('rx_attr', 'ingress', 'rx'):
            with self.subTest(kind=kind):
                if self.d._conn is None:
                    self.c.connect()
                if kind == 'rx_attr':
                    self.c.rx(b'a' * 512, drain=False)
                elif kind == 'ingress':
                    for _ in range(11):
                        self.c.rx(b'a' * 400, drain=False)
                else:
                    for _ in range(6):
                        self.c.rx(b'a' * 400)
                self.c.tick(2)
                self.assertIsNone(self.d._conn)
                self.assertEqual(self.d.stats()['fault'], kind)
                self.assertEqual(self.read_rx(), b'')

    def test_priority_queue_overflow_is_fault(self):
        for _ in range(9):
            self.d._ack(True, 0)
        self.assertTrue(self.d._fault)
        self.c.tick()
        self.assertEqual(self.d.stats()['fault'], 'protocol')
        self.assertIsNone(self.d._conn)

    def test_scheduler_full_timer_fallback(self):
        self.c.queue_size = 0
        self.c.rx(b'\x03', drain=False)
        self.c.tick(2)
        self.assertEqual(self.c.interrupts, 1)
        self.assertGreater(self.d._sf, 0)
        self.assertFalse(self.d._ip)

    def test_notify_retry_keeps_staged_bytes_and_new_chunk(self):
        self.d.write(b'abcdef' * 40)
        for code in (errno.ENOMEM, errno.EAGAIN, errno.EBUSY):
            self.c.ble.notify_error = code
            self.c.tick()
        self.assertEqual(self.d.stats()['tx_queued'], 240)
        self.assertEqual(self.c.output(), b'')
        self.c.event(21, (1, 23))
        self.c.ble.notify_error = None
        self.c.tick(2)
        out = self.c.output().replace(b'BLE Config mtu=23 chunk=20\n', b'')
        self.assertEqual(out, b'abcdef' * 40)
        self.assertTrue(all(len(n[2]) <= 20 for n in self.c.ble.notifications))

    def test_notify_deadline_precedes_backoff_and_permanent_error(self):
        self.d.write(b'data')
        self.c.ble.notify_error = errno.ENOMEM
        self.c.tick()
        self.d._back = (self.c.now + 3000) % self.c.MODULUS
        self.c.tick(101)
        self.assertEqual(self.d.stats()['fault'], 'notify_timeout')
        self.c.ble.notify_error = None
        self.c.connect()
        self.c.rx(b'\r')
        self.d.write(b'data')
        self.c.ble.notify_error = errno.EIO
        self.c.tick(2)
        self.assertEqual(self.d.stats()['fault'], 'notify')

    def test_reentrant_notify_success_and_exception(self):
        for code in (None, errno.ENOMEM):
            with self.subTest(code=code):
                self.c.rx(b'\r')
                self.d.write(b'old')
                def hook():
                    self.c.ble.notify_hook = None
                    self.reconnect()
                    self.d._ready = 1
                    self.d.write(b'new')
                self.c.ble.notify_hook = hook
                self.c.ble.notify_error = code
                self.c.tick()
                self.c.ble.notify_error = None
                self.c.tick(3)
                self.assertTrue(self.c.output().endswith(b'new'))
                self.assertEqual(self.d.stats()['tx_queued'], 0)
                self.c.clear_output()

    def test_filemanager_rate_budget_at_20_and_244(self):
        # Simulated stdout producer with the real FM's pause, not a browser/FM run.
        for mtu in (23, 247):
            with self.subTest(mtu=mtu):
                self.c.event(21, (1, mtu))
                self.c.tick()
                self.c.clear_output()
                before = self.d._drop
                expected = bytearray()
                for i in range(69):
                    row = (b'@@FM_DATA@@' + bytes([65 + i % 26]) * 160 + b'\n')
                    expected.extend(row)
                    self.d.write(row)
                    self.c.tick(3)
                self.assertEqual(self.c.output(), expected)
                self.assertEqual(self.d._drop, before)

    def test_tx_budget_at_20_bytes(self):
        self.c.event(21, (1, 23))
        self.c.tick()
        self.c.clear_output()
        self.d.write(b'x' * 900)
        self.c.tick()
        self.assertEqual(len(self.c.output()), 488)
        self.assertEqual(len(self.c.ble.notifications), 25)

    def test_statistics_saturate(self):
        self.d._drop = self.c.mod._LIMIT - 1
        self.d.write(b'x' * 3000)
        self.assertEqual(self.d._drop, self.c.mod._LIMIT)
        self.assertEqual(self.c.mod._inc(self.c.mod._LIMIT), self.c.mod._LIMIT)

    def test_joystick_during_upload_and_in_place_reset(self):
        original = self.c.mod.JOY_POS
        self.start_file('/joy.bin', 6)
        self.c.rx(bytes([156, 100, 50, 206]), attr=12)
        self.assertEqual(original, [-100, 100, 50, -50])
        self.assertEqual(self.c.mod.Joystick(10).get_joyX(), 50)
        self.assertTrue(self.c.mod.Joystick(20).joy_check(1))
        self.c.rx(b'bad', attr=12)
        self.c.rx(b'\x01\x01\x01\x01', handle=2, attr=12)
        self.assertEqual(original, [-100, 100, 50, -50])
        self.c.rx(packet(0, b'abcdef'))
        self.c.tick(151)
        self.assertEqual(self.c.mod.Joystick(10).get_joyX(), 0)
        self.reconnect()
        self.assertIs(self.c.mod.JOY_POS, original)
        self.assertEqual(original, [0, 0, 0, 0])

    def test_joystick_age_rollover(self):
        self.c.now = self.c.MODULUS - 10
        self.c.rx(bytes([50, 50, 60, 60]), attr=12)
        self.c.now = 10
        self.assertEqual(self.c.mod.joy_read()[1], 20)

    def test_legacy_module_alias(self):
        self.assertIs(sys.modules['ble_repl_bletime'], self.c.mod)
        self.assertIs(self.c.mod.get_active(), self.d)

    def test_disconnect_closes_partial_without_irq_io(self):
        self.start_file('/partial.bin', 6)
        self.c.rx(packet(0, b'abc'))
        f = self.d._f
        self.c.event(2, (1, 0, b''))
        self.assertIs(self.d._f, f)
        self.c.tick()
        self.assertIsNone(self.d._f)
        self.assertEqual(self.c.resolve('/partial.bin').read_bytes(), b'abc')


class StartupMTUTests(unittest.TestCase):
    def setUp(self):
        self.c = Context().install()

    def tearDown(self):
        self.c.ble.active_error = None
        self.c.close_failures = 0
        if self.c.timer:
            self.c.timer.fail = False
        self.c.restore()

    def test_constructor_is_inert(self):
        d = self.c.mod.BLENUSRepl()
        self.assertFalse(self.c.ble.enabled)
        self.assertIsNone(self.c.ble.handler)
        self.assertIsNone(self.c.slot)
        self.assertIsNone(self.c.mod.get_active())
        self.assertIsNone(d.readinto(bytearray(1)))

    def test_owner_radio_and_dupterm_guards(self):
        self.c.ble.enabled = True
        with self.assertRaises(OSError):
            self.c.start()
        self.assertTrue(self.c.ble.enabled)
        self.c.ble.enabled = False
        other = object()
        self.c.slot = other
        with self.assertRaises(OSError):
            self.c.start()
        self.assertIs(self.c.slot, other)
        self.c.slot = None
        d = self.c.start()
        with self.assertRaises(OSError):
            self.c.mod.BLENUSRepl().start()
        self.assertIs(self.c.slot, d)

    def test_failed_start_rolls_back(self):
        self.c.ble.start_error = errno.ENOMEM
        with self.assertRaises(OSError) as e:
            self.c.start()
        self.assertEqual(e.exception.args[0], errno.ENOMEM)
        self.assertIsNone(self.c.slot)
        self.assertFalse(self.c.ble.enabled)
        self.assertIsNone(self.c.mod.get_active())

    def test_close_retries_owned_resources_preserves_foreign_slot(self):
        d = self.c.start()
        other = object()
        self.c.slot = other
        self.c.timer.fail = True
        self.c.ble.active_error = errno.EIO
        with self.assertRaises(OSError):
            d.close()
        self.assertIs(self.c.slot, other)
        self.assertIs(self.c.mod.get_active(), d)
        self.assertIsNotNone(d._tm)
        self.assertTrue(d._radio)
        self.c.timer.fail = False
        self.c.ble.active_error = None
        d.close()
        d.close()
        self.assertIs(self.c.slot, other)
        self.assertIsNone(self.c.mod.get_active())
        with self.assertRaises(ValueError):
            d.start()

    def test_name_utf8_and_advertising_uuid(self):
        for name in (b'', b'x' * 30, b'\xff'):
            with self.subTest(name=name), self.assertRaises((ValueError, UnicodeError)):
                self.c.mod.BLENUSRepl(name=name)
        d = self.c.start(name='Žluťoučký ESP32')
        advert = self.c.ble.adverts[-1][1]
        self.assertLessEqual(len(advert['adv_data']), 31)
        self.assertLessEqual(len(advert['resp_data']), 31)
        self.assertIn(bytes(self.c.mod._NUS), advert['adv_data'])
        self.assertEqual(advert['resp_data'][2:].decode(), 'Žluťoučký ESP32')

    def test_preference_does_not_confirm_mtu_and_cap_without_rx(self):
        d = self.c.start()
        self.c.connect(mtu=None)
        self.assertEqual(self.c.ble.configs['mtu'], 247)
        self.assertEqual((d._mtu, d._chunk, d._mc), (23, 20, 0))
        self.assertIn(b'BLE Config mtu=23 chunk=20\n', self.c.output())
        self.assertFalse(d._ready)
        before = len(self.c.output())
        self.c.tick(25)
        self.assertGreater(len(self.c.output()), before)
        self.c.rx(b'\r')
        self.c.tick(26)
        before = len(self.c.output())
        self.c.tick(26)
        self.assertEqual(len(self.c.output()), before)

    def test_mtu_early_matching_foreign_disconnect_and_reused_handle(self):
        d = self.c.start()
        self.c.event(21, (1, 247))
        self.c.connect(1, None)
        self.assertEqual((d._mtu, d._chunk, d._mc, d._ma), (247, 244, 1, 0))
        self.c.event(2, (1, 0, b''))
        self.c.connect(1, None)
        self.assertEqual((d._mtu, d._chunk, d._mc), (23, 20, 0))
        self.c.event(2, (1, 0, b''))
        self.c.event(21, (3, 247))
        self.c.connect(2, None)
        self.assertEqual(d._mc, 0)
        self.c.event(2, (2, 0, b''))
        self.c.event(21, (1, 247))
        self.c.event(2, (1, 0, b''))
        self.c.connect(1, None)
        self.assertEqual(d._mc, 0)

    def test_early_confirmed_23_and_peer_first_cancel_local_request(self):
        d = self.c.start()
        self.c.event(21, (1, 23))
        self.c.connect(1, None)
        self.c.tick(6)
        self.assertEqual((d._mtu, d._chunk, d._mc, d._ma), (23, 20, 1, 0))
        self.c.event(2, (1, 0, b''))
        self.c.connect(1, None)
        self.c.event(21, (1, 247))
        self.c.tick(6)
        self.assertEqual(d._ma, 0)

    def test_exchange_ealready_and_bounded_busy_enomem(self):
        d = self.c.start()
        for error, count in ((errno.EALREADY, 1), (errno.EBUSY, 3), (errno.ENOMEM, 3), (errno.EIO, 1)):
            with self.subTest(error=error):
                self.c.ble.exchange_error = error
                self.c.connect(1, None)
                self.c.tick(25)
                self.assertEqual(d._ma, count)
                self.assertEqual((d._mtu, d._chunk, d._mc), (23, 20, 0))
                self.c.event(2, (1, 0, b''))

    def test_reentrant_exchange_event_wins_over_api_error(self):
        d = self.c.start()
        self.c.ble.exchange_hook = lambda: self.c.event(21, (1, 247))
        self.c.ble.exchange_error = errno.EBUSY
        self.c.connect(1, None)
        self.c.tick(6)
        self.assertEqual((d._mc, d._ms, d._me), (1, 'exchanged', 0))

    def test_exchange_api_absent_is_safe(self):
        d = self.c.start()
        d._mx = None
        self.c.connect(1, None)
        self.c.tick(6)
        self.assertEqual((d._chunk, d._ms), (20, 'unavailable'))

    def test_early_rx_matching_and_foreign_session(self):
        d = self.c.start()
        self.c.rx(b'early\r', drain=False)
        self.assertIsNone(d.readinto(bytearray(10)))
        self.c.connect(1, None)
        b = bytearray(10)
        n = d.readinto(b)
        self.assertEqual(b[:n], b'early\r')
        self.c.event(2, (1, 0, b''))
        self.c.rx(b'foreign\r', handle=2, drain=False)
        self.c.connect(1, None)
        self.assertEqual(d._r.any(), 0)

    def test_early_rx_overflow_is_fault_before_execution(self):
        d = self.c.start()
        self.c.rx(b'x' * 512, drain=False)
        self.c.connect(1, None)
        self.c.tick(2)
        self.assertIsNone(d._conn)
        self.assertEqual(d._r.any(), 0)

    def test_foreign_attribute_is_drained(self):
        d = self.c.start()
        self.c.connect()
        self.c.rx(b'foreign', handle=2)
        self.c.rx(b'ours')
        b = bytearray(10)
        n = d.readinto(b)
        self.assertEqual(b[:n], b'ours')

    def test_explicit_config_and_attempt_limit(self):
        d = self.c.start()
        self.c.connect()
        self.c.tick(1600)
        self.assertEqual(d._hc, 60)
        before = len(self.c.output())
        self.c.tick(26)
        self.assertEqual(len(self.c.output()), before)
        self.c.clear_output()
        self.c.stream(control(255), (1,))
        self.assertEqual(self.c.output(), b'BLE Config mtu=247 chunk=244\n')

    def test_bletime_exports_real_functions(self):
        spec = importlib.util.spec_from_file_location('bletime', ROOT / 'bletime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(module.sleep_ms, self.c.modules['utime'].sleep_ms)
        self.assertIs(module.install_as_utime(), self.c.modules['utime'])
        self.assertIs(sys.modules['utime'], self.c.modules['utime'])


class ArtifactTests(unittest.TestCase):
    def test_driver_source_budget_and_license(self):
        p = ROOT / 'ble_repl.py'
        ast.parse(p.read_text())
        self.assertLessEqual(p.stat().st_size, 40000)
        self.assertTrue(p.read_text().startswith('# SPDX-License-Identifier: MIT'))


if __name__ == '__main__':
    unittest.main()
