// SPDX-License-Identifier: MIT. Stream-level tests, not claims about a real radio/VM.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BleReplClient, header, packet, control, UUID } from '../web/ble-client.js';
import { highlightPython } from '../web/editor.js';
const enc = new TextEncoder();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

class Peer {
  constructor(chunk, options = {}) {
    this.chunk = chunk; this.options = options; this.pending = [];
    this.phase = 'repl'; this.files = new Map(); this.next = 0; this.writes = [];
    this.headerCount = 0; this.ackDrops = 0; this.notify = null; this.raw = false;
    this.rawSource = ''; this.commands = []; this.axes = null; this.concurrent = 0;
    this.rx = { properties: { write: true }, writeValueWithResponse: bytes => this.write(bytes) };
    this.joy = { properties: { writeWithoutResponse: true }, writeValueWithoutResponse: async bytes => { this.axes = Array.from(new Int8Array(bytes.buffer, bytes.byteOffset, 4)); } };
    this.tx = {
      addEventListener: (_type, fn) => { this.notify = fn; },
      removeEventListener: () => { this.notify = null; }, startNotifications: async () => {},
    };
  }
  send(bytes) {
    // Deliberately fragment every byte, including UTF-8 and all three ACK bytes.
    for (const b of bytes) {
      const v = Uint8Array.of(b);
      this.notify?.({ target: { value: new DataView(v.buffer) } });
    }
  }
  text(text) { this.send(enc.encode(text)); }
  ack(yes, sequence) { this.send(Uint8Array.of(yes ? 6 : 21, sequence & 255, sequence >> 8)); }
  async write(bytes) {
    assert.equal(++this.concurrent, 1, 'GATT writes must never overlap');
    assert.ok(bytes.length <= this.chunk);
    this.writes.push(Array.from(bytes));
    try {
      if (this.options.slow) await sleep(2);
      this.pending.push(...bytes);
      this.parse();
    } finally { this.concurrent--; }
  }
  parse() {
    while (this.pending.length) {
      const p = this.pending;
      if (p[0] === 250 && p.length < 4) return;
      if (p.slice(0, 4).every((value, index) => value === [250,206,176,12][index]) && p.length >= 4) {
        if (p.length < 8) return;
        assert.deepEqual(p.slice(0, 4), [250, 206, 176, 12]);
        const op = p[4];
        if (op >= 252) {
          p.splice(0, 8);
          if (op === 255) this.text(`BLE Config mtu=${this.chunk + 3} chunk=${this.chunk}\n`);
          else if (op === 253) this.ack(!this.options.fileError, this.next);
          else { this.phase = 'repl'; this.ack(false, 65535); }
          continue;
        }
        if (p.length < 8 + op) return;
        const start = p.splice(0, 8 + op);
        const path = new TextDecoder().decode(Uint8Array.from(start.slice(8)));
        const size = start[5] | start[6] << 8 | start[7] << 16;
        if (!(this.path === path && this.next === 0 && this.size === size)) {
          this.path = path; this.size = size; this.next = 0; this.files.set(path, []);
          this.headerCount++;
        }
        this.phase = size ? 'data' : 'repl';
        if (this.options.dropHeader && !this.droppedHeader) this.droppedHeader = true;
        else this.ack(true, 65535);
        continue;
      }
      if (this.phase === 'data') {
        if (p.length < 3 || p.length < p[2] + 4) return;
        const frame = p.splice(0, p[2] + 4);
        const seq = frame[0] | frame[1] << 8;
        if (this.options.rejectOne && seq === 1 && !this.rejected) { this.rejected = true; this.ack(false, this.next); continue; }
        const sum = frame.slice(0, -1).reduce((a, b) => (a + b) & 255, 0);
        if (seq !== this.next || sum !== frame.at(-1)) { this.ack(false, this.next); continue; }
        this.files.get(this.path).push(...frame.slice(3, -1)); this.next++;
        const final = this.files.get(this.path).length === this.size;
        if (final) this.phase = 'repl';
        if ((seq & 3) === 3 || final) {
          if ((this.options.dropFinal && final) || (this.options.dropWindow && seq === 3 && !this.ackDrops)) this.ackDrops++;
          else this.ack(true, seq);
        }
        continue;
      }
      const byte = p.shift();
      if (byte === 3) { this.rawSource = ''; if (this.running) { this.running = false; this.text('\x04KeyboardInterrupt\n\x04>'); } }
      else if (byte === 2) { this.raw = false; this.text('\r\n>>> '); }
      else if (byte === 1) { this.raw = true; this.text('raw REPL; CTRL-B to exit\r\n>'); }
      else if (byte === 4 && this.raw) {
        assert.match(this.rawSource, /with open\(__file__/);
        assert.match(this.rawSource, /exec\(_ble_demo_code,globals\(\)\)/);
        this.rawSource = ''; this.text('OK');
        if (this.options.busy) this.running = true;
        else this.text('Hello žluťoučký!\n\x04' + (this.options.stderr ? 'ValueError: test\n' : '') + '\x04>');
      } else if (byte === 13 && !this.raw) { if (this.line) { this.commands.push(this.line); this.text(`${this.line}\r\n42\r\n>>> `); this.line = ''; } }
      else if (byte >= 32 || byte === 10) { if (this.raw) this.rawSource += String.fromCharCode(byte); else this.line = (this.line || '') + String.fromCharCode(byte); }
    }
  }
}
async function setup(chunk, options) {
  const peer = new Peer(chunk, options), client = new BleReplClient();
  // Real production deadlines are covered by the opt-in hardware test.
  const reply = client._reply.bind(client);
  client._reply = (_timeout, signal) => reply(40, signal);
  await client.attach(peer.rx, peer.tx, peer.joy);
  return { peer, client };
}
const data = Uint8Array.from({ length: 4103 }, (_, i) => i * 73 & 255);
for (const chunk of [20, 244]) {
  for (const [label, options] of Object.entries({ ordinary:{}, headerRetry:{dropHeader:true}, lostWindow:{dropWindow:true}, lostFinal:{dropFinal:true}, nakRecovery:{rejectOne:true} })) {
    test(`binary upload, chunk ${chunk}, ${label}`, async () => {
      const { peer, client } = await setup(chunk, options);
      try {
        const progress = [];
        await client.upload('/dir/čísla.bin', data, value => progress.push(value));
        assert.deepEqual(Uint8Array.from(peer.files.get('/dir/čísla.bin')), data);
        assert.equal(peer.headerCount, 1, 'Recovery must not reopen the target');
        assert.equal(progress.at(-1).sent, data.length);
        assert.equal(client.state, 'idle');
      } finally { client.disconnect(); }
    });
  }
}
test('empty file + single REPL line + signed joystick values', async () => {
  const { peer, client } = await setup(20);
  await client.upload('/empty.bin', new Uint8Array());
  assert.deepEqual(peer.files.get('/empty.bin'), []);
  await client.command('print(42)'); assert.deepEqual(peer.commands, ['print(42)']);
  await client.joystick([-100, 99, 250, -250]); assert.deepEqual(peer.axes, [-100, 99, 100, -100]);
  assert.equal(UUID.axes, '23f10012-5f90-11ee-8c99-0242ac120002'); client.disconnect();
});
test('run captures UTF-8 stdout, stderr and returns to friendly REPL', async () => {
  const { peer, client } = await setup(244, { stderr:true });
  const result = await client.run('print("žluťoučký")');
  assert.equal(new TextDecoder().decode(Uint8Array.from(peer.files.get('/ble_demo_run.py'))), 'print("žluťoučký")');
  assert.equal(result.stdout, 'Hello žluťoučký!\n'); assert.equal(result.stderr, 'ValueError: test\n');
  assert.equal(peer.raw, false); client.disconnect();
});
test('Stop aborts running raw REPL code without disconnecting', async () => {
  const { peer, client } = await setup(20, { busy:true });
  const run = client.run('while True: pass'); const rejected = assert.rejects(run, { name:'AbortError' });
  for (let i = 0; i < 100 && !peer.running; i++) await sleep(5);
  assert.ok(peer.running); await client.stop(); await rejected;
  assert.equal(peer.running, false); assert.equal(client.connected, true); await client.command('print(42)'); client.disconnect();
});
test('Stop during a fragmented header closes upload before returning to REPL', async () => {
  const { peer, client } = await setup(20, { slow:true });
  const job = client.upload('/' + 'a'.repeat(45), data); const rejected = assert.rejects(job, { name:'AbortError' });
  await sleep(1); await client.stop(); await rejected;
  assert.equal(peer.pending.length, 0); assert.equal(peer.phase, 'repl');
  assert.equal(client._waiters.size, 0); assert.equal(client.state, 'idle'); client.disconnect();
});
test('file error STATUS cannot be mistaken for successful final completion', async () => {
  const { client } = await setup(244, { dropFinal:true, fileError:true });
  await assert.rejects(client.upload('/bad.bin', Uint8Array.of(1)), /file error/); client.disconnect();
});
test('disconnect rejects pending waits and never reuses queued old-session writes', async () => {
  const { client } = await setup(20, { busy:true });
  const job = client.run('while True: pass'); const rejected = assert.rejects(job);
  await sleep(10); client.disconnect(); await rejected;
  assert.equal(client._waiters.size, 0);
});
test('strict wire bounds and escaping for the lightweight Python highlighter', () => {
  assert.throws(() => header('/' + 'č'.repeat(24), 0), /UTF-8/);
  assert.throws(() => header('/bad\0', 0)); assert.throws(() => header('/x', 0x1000000));
  assert.throws(() => packet(65536, Uint8Array.of(1))); assert.deepEqual(Array.from(control(253)), [250,206,176,12,253,0,0,0]);
  const html = highlightPython('import time\n# <img onerror="bad">\ns = r"""hello\nworld"""\nx=0xFF + .25\n');
  assert.ok(!html.includes('<img')); assert.match(html, /tok-keyword/); assert.match(html, /tok-comment/);
  assert.match(html, /tok-string/); assert.match(html, /tok-number/); assert.match(html, /&lt;img/);
});

for (const chunk of [20, 244]) test(`interactive input preserves UTF-8/control bytes and order, MTU payload ${chunk}`, async () => {
  const { client, peer } = await setup(chunk, { slow:true });
  await client.resumeRepl(); peer.writes = [];
  const parts = ['print("žluťoučký")', '\x1b[D', '\x08', '\x1b[A', '\t', '\x1b[H', '\x1b[F', '\r'];
  await Promise.all(parts.map(part => client.writeTerminal(part)));
  assert.deepEqual(peer.writes.flat(), Array.from(enc.encode(parts.join(''))));
  assert.ok(!peer.writes.flat().includes(3), 'Typing must not synchronize/interrupt per key');
  assert.equal(client._terminalInput, null); client.disconnect();
});

test('interactive queue is bounded in UTF-8 bytes, including in-flight input', async () => {
  const { client, peer } = await setup(20, { slow:true });
  peer.writes = [];
  await assert.rejects(client.writeTerminal('č'.repeat(2049)), /4096/);
  assert.deepEqual(peer.writes, []);
  const accepted = client.writeTerminal('x'.repeat(4096));
  const rejected = assert.rejects(accepted, { name:'AbortError' });
  await assert.rejects(client.writeTerminal('y'), /queue is full/);
  client.disconnect(); await rejected;
});

test('starting upload invalidates queued terminal keys and blocks input without corrupting DATA', async () => {
  const { client, peer } = await setup(20, { slow:true });
  const queued = client.writeTerminal('OLD-KEYS'.repeat(50));
  const rejected = assert.rejects(queued, { name:'AbortError' });
  const upload = client.upload('/terminal.bin', data);
  await assert.rejects(client.writeTerminal('must not become DATA'), /unavailable/);
  await upload; await rejected;
  assert.deepEqual(Uint8Array.from(peer.files.get('/terminal.bin')), data);
  assert.ok(!peer.commands.some(line => line.includes('OLD-KEYS')));
  await client.writeTerminal('print(42)\r');
  assert.deepEqual(peer.commands, ['print(42)']); client.disconnect();
});

test('terminal Ctrl+C cancels upload, then accepts the next command', async () => {
  const { client, peer } = await setup(20, { slow:true });
  const job = client.upload('/cancel.bin', data);
  const rejected = assert.rejects(job, { name:'AbortError' });
  await sleep(1); await client.writeTerminal('\x03'); await rejected;
  assert.equal(peer.phase, 'repl'); assert.equal(client.state, 'idle');
  await client.writeTerminal('print(42)\r'); assert.deepEqual(peer.commands, ['print(42)']); client.disconnect();
});

test('managed Run accepts stdin only during execution and rejects mode controls', async () => {
  const { client, peer } = await setup(244, { busy:true });
  const job = client.run('while True: pass');
  const rejected = assert.rejects(job, { name:'AbortError' });
  await assert.rejects(client.writeTerminal('too early'), /unavailable/);
  for (let i = 0; i < 100 && !peer.running; i++) await sleep(5);
  assert.ok(client.terminalReady); peer.writes = [];
  await client.writeTerminal('answer\r');
  assert.deepEqual(peer.writes.flat(), Array.from(enc.encode('answer\r')));
  for (const key of ['\x01','\x02','\x04','\x05']) await assert.rejects(client.writeTerminal(key), /mode controls/);
  await client.writeTerminal('\x03'); await rejected;
  assert.equal(peer.running, false); assert.equal(client.state, 'idle'); client.disconnect();
});

test('disconnect/reconnect never transmits old terminal input into a new session', async () => {
  const { client, peer } = await setup(20, { slow:true });
  const queued = client.writeTerminal('STALE'.repeat(100));
  const rejected = assert.rejects(queued, { name:'AbortError' });
  client.disconnect();
  const fresh = new Peer(20);
  await client.attach(fresh.rx, fresh.tx, fresh.joy); await rejected;
  fresh.writes = []; await client.writeTerminal('new\r');
  assert.deepEqual(fresh.writes.flat(), Array.from(enc.encode('new\r')));
  assert.equal(peer.commands.length, 0); client.disconnect();
});

test('manual Ctrl+C bypasses queued paste without leaving raw REPL mode', async () => {
  const { client, peer } = await setup(20, { slow:true });
  await client.writeTerminal('\x01'); assert.equal(peer.raw, true); peer.writes = [];
  const queued = client.writeTerminal('BACKLOG'.repeat(400));
  const rejected = assert.rejects(queued, { name:'AbortError' });
  await sleep(1); await client.writeTerminal('\x03'); await rejected;
  assert.ok(peer.writes.flat().length <= 21, 'Only an already in-flight GATT chunk may precede Ctrl+C');
  assert.equal(peer.writes.flat().at(-1), 3);
  assert.equal(peer.raw, true, 'A manual interrupt must preserve the current mode');
  await client.writeTerminal('\x02'); assert.equal(peer.raw, false); client.disconnect();
});
