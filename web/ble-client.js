// SPDX-License-Identifier: MIT
// Browser-independent protocol core. Only connect() uses navigator.bluetooth;
// attach() allows the same implementation to be exercised by a GATT test adapter.
export const UUID = Object.freeze({
  nus: '6e400001-b5a3-f393-e0a9-e50e24dcca9e',
  rx: '6e400002-b5a3-f393-e0a9-e50e24dcca9e',
  tx: '6e400003-b5a3-f393-e0a9-e50e24dcca9e',
  joystick: '23f10010-5f90-11ee-8c99-0242ac120002',
  axes: '23f10012-5f90-11ee-8c99-0242ac120002',
});
const encoder = new TextEncoder();
const MAGIC = [0xfa, 0xce, 0xb0, 0x0c];
const CAP = encoder.encode('BLE Config ');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const cancelled = () => new DOMException('Operation cancelled', 'AbortError');
function check(signal) { if (signal?.aborted) throw cancelled(); }
export function control(opcode) { return Uint8Array.of(...MAGIC, opcode, 0, 0, 0); }
export function header(path, size) {
  const name = encoder.encode(path);
  if (!name.length || name.length > 48 || path.includes('\0'))
    throw new Error('The destination must contain 1–48 UTF-8 bytes, without NUL.');
  if (!Number.isInteger(size) || size < 0 || size > 0xffffff)
    throw new Error('File size exceeds the 24-bit wire format.');
  return Uint8Array.of(...MAGIC, name.length, size & 255, size >> 8 & 255,
    size >> 16 & 255, ...name);
}
export function packet(sequence, bytes) {
  if (sequence < 0 || sequence > 65535 || !bytes.length || bytes.length > 255)
    throw new Error('Invalid DATA packet.');
  const out = Uint8Array.of(sequence & 255, sequence >> 8, bytes.length, ...bytes, 0);
  out[out.length - 1] = out.subarray(0, -1).reduce((sum, byte) => (sum + byte) & 255, 0);
  return out;
}

export class BleReplClient {
  constructor() {
    this.listeners = new Map();
    this.device = this.rx = this.tx = this.joy = null;
    this.connected = false;
    this.chunk = 20;                  // ATT MTU 23 is the safe initial assumption.
    this.mtu = 23;
    this.state = 'disconnected';
    this._tail = Promise.resolve();   // ONE GATT operation in flight, across both services.
    this._pending = new Uint8Array();
    this._decoder = new TextDecoder();
    this._replies = [];
    this._waiters = new Set();
    this._received = '';
    this._operation = null;
    this._uploading = false;
    this._stopping = null;
    this._raw = null;
    this._epoch = 0;
    this._terminalInput = null;
    this._notify = event => {
      const v = event.target.value;
      this.receive(new Uint8Array(v.buffer, v.byteOffset, v.byteLength));
    };
    this._disconnected = () => this._lost();
  }
  on(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
    return () => this.listeners.get(type)?.delete(callback);
  }
  _emit(type, value) { for (const fn of this.listeners.get(type) || []) fn(value); }
  _state(value) { this.state = value; this._emit('state', value); }

  async connect() {
    if (this.connected || this.state === 'connecting') throw new Error('Already connecting/connected.');
    if (!globalThis.isSecureContext || !navigator.bluetooth)
      throw new Error('Use a Web Bluetooth browser with HTTPS or localhost.');
    this._state('connecting');
    try {
      // Invoke from a click handler. Optional joystick permission MUST be requested here.
      this.device = await navigator.bluetooth.requestDevice({
        filters: [{ services: [UUID.nus] }], optionalServices: [UUID.joystick],
      });
      this.device.addEventListener('gattserverdisconnected', this._disconnected);
      const server = await this.device.gatt.connect();
      const nus = await server.getPrimaryService(UUID.nus);
      const rx = await nus.getCharacteristic(UUID.rx);
      const tx = await nus.getCharacteristic(UUID.tx);
      let joy = null;
      try { joy = await (await server.getPrimaryService(UUID.joystick)).getCharacteristic(UUID.axes); }
      catch { this._emit('notice', 'Joystick service is unavailable; REPL remains usable.'); }
      await this.attach(rx, tx, joy);
      this._emit('device', this.device.name || 'MicroPython');
    } catch (error) { this.disconnect(); throw error; }
  }
  async attach(rx, tx, joy = null) {
    this._discardTerminalInput();
    this._epoch++;
    this.rx = rx; this.tx = tx; this.joy = joy;
    this._pending = new Uint8Array(); this._decoder = new TextDecoder();
    this._replies = []; this._received = ''; this.chunk = 20; this.mtu = 23;
    // Register BEFORE enabling notifications so an early capability line cannot be missed.
    tx.addEventListener('characteristicvaluechanged', this._notify);
    this.connected = true;
    try {
      await tx.startNotifications();
      await this._write(control(0xff)); // Explicit CONFIG also works after reconnect.
      this._state('idle');
    } catch (error) { this._lost(); throw error; }
  }
  disconnect() {
    const device = this.device;
    this._lost();
    if (device?.gatt.connected) device.gatt.disconnect();
  }
  _lost() {
    this._discardTerminalInput();
    this._epoch++;
    this._operation?.controller.abort();
    this.tx?.removeEventListener('characteristicvaluechanged', this._notify);
    this.device?.removeEventListener('gattserverdisconnected', this._disconnected);
    this.connected = false; this.rx = this.tx = this.joy = null;
    this._raw = null; this._uploading = false;
    this._emit('wake'); this._state('disconnected');
  }

  // Notifications are a BYTE STREAM: a three-byte ACK, a UTF-8 codepoint, or
  // a capability line may span notifications, or share one with other content.
  receive(bytes) {
    const all = new Uint8Array(this._pending.length + bytes.length);
    all.set(this._pending); all.set(bytes, this._pending.length);
    let i = 0;
    while (i < all.length) {
      const byte = all[i];
      if (this._uploading && (byte === 6 || byte === 21)) {
        if (all.length - i < 3) break;
        this._replies.push({ ack: byte === 6, sequence: all[i + 1] | all[i + 2] << 8 });
        if (this._replies.length > 64) this._replies.shift();
        i += 3; this._emit('wake'); continue;
      }
      const n = Math.min(CAP.length, all.length - i);
      if (all.subarray(i, i + n).every((b, k) => b === CAP[k])) {
        if (n < CAP.length) break;
        const end = all.indexOf(10, i);
        if (end < 0 && all.length - i < 96) break;
        if (end >= 0 && end - i < 96) {
          const line = new TextDecoder().decode(all.subarray(i, end + 1));
          const match = /^BLE Config mtu=(\d+) chunk=(\d+)\r?\n$/.exec(line);
          if (match && +match[1] >= 23 && +match[2] >= 20 &&
              +match[2] <= Math.min(244, +match[1] - 3)) {
            this.mtu = +match[1]; this.chunk = +match[2];
            this._emit('config', { mtu: this.mtu, chunk: this.chunk });
            i = end + 1; this._emit('wake'); continue;
          }
        }
      }
      // Decode ordinary output in blocks. Stop before a potential capability/ACK
      // prefix; TextDecoder retains incomplete UTF-8 codepoints across notifications.
      let end = i + 1;
      while (end < all.length && all[end] !== CAP[0] &&
             !(this._uploading && (all[end] === 6 || all[end] === 21))) end++;
      const text = this._decoder.decode(all.subarray(i, end), { stream: true });
      if (text) this._text(text);
      i = end;
    }
    this._pending = all.slice(i);
  }
  _text(text) {
    this._received = (this._received + text).slice(-65536);
    if (!this._raw) this._emit('text', text);
    else this._rawText(text);
    this._emit('wake');
  }
  _rawText(text) {
    const raw = this._raw;
    raw.buffer += text;
    if (raw.phase === 0) {
      if (raw.buffer.length < 2) return;
      if (!raw.buffer.startsWith('OK')) { raw.error = new Error('Raw REPL did not accept code.'); return; }
      raw.buffer = raw.buffer.slice(2); raw.phase = 1;
      this._emit('terminalReady', true); // input() can now read from the same stdin.
    }
    while (raw.phase === 1 || raw.phase === 2) {
      const end = raw.buffer.indexOf('\x04');
      const part = end < 0 ? raw.buffer : raw.buffer.slice(0, end);
      const kind = raw.phase === 1 ? 'stdout' : 'stderr';
      raw[kind] = (raw[kind] + part).slice(-65536);
      if (part) this._emit(kind === 'stdout' ? 'text' : 'stderr', part);
      raw.buffer = end < 0 ? '' : raw.buffer.slice(end + 1);
      if (end < 0) return;
      raw.phase++;
      this._emit('terminalReady', this.terminalReady);
    }
    if (raw.phase === 3 && raw.buffer.includes('>')) raw.done = true;
    this._emit('terminalReady', this.terminalReady);
  }

  _wait(predicate, timeout, signal) {
    return new Promise((resolve, reject) => {
      let timer;
      const finish = (error, value) => {
        clearTimeout(timer); off(); signal?.removeEventListener('abort', wake);
        this._waiters.delete(wake); error ? reject(error) : resolve(value);
      };
      const wake = () => {
        if (signal?.aborted) return finish(cancelled());
        if (!this.connected) return finish(new Error('Bluetooth disconnected.'));
        try { const value = predicate(); if (value !== undefined) finish(null, value); }
        catch (error) { finish(error); }
      };
      const off = this.on('wake', wake);
      this._waiters.add(wake); signal?.addEventListener('abort', wake, { once: true });
      if (timeout) timer = setTimeout(() => finish(new Error('Device response timed out.')), timeout);
      wake();
    });
  }
  _reply(timeout, signal) {
    return this._wait(() => this._replies.length ? this._replies.shift() : undefined, timeout, signal);
  }
  _prompt(prompt, timeout, signal) {
    return this._wait(() => this._received.includes(prompt) ? true : undefined, timeout, signal);
  }
  _write(bytes, characteristic = this.rx, signal) {
    const data = Uint8Array.from(bytes);
    const epoch = this._epoch;
    // A whole protocol frame owns the lane. Abort between frames, NEVER midway
    // through a split header/DATA frame: CANCEL would otherwise become its payload.
    const job = this._tail.catch(() => {}).then(async () => {
      check(signal);
      if (!this.connected || !characteristic || epoch !== this._epoch)
        throw new Error('Bluetooth session is no longer connected.');
      const chunk = characteristic === this.joy ? 4 : this.chunk;
      for (let i = 0; i < data.length; i += chunk) {
        const part = data.slice(i, i + chunk);
        // Prefer acknowledged GATT writes. Protocol ACKs separately confirm FLASH writes.
        if (characteristic.properties.write) await characteristic.writeValueWithResponse(part);
        else if (characteristic.properties.writeWithoutResponse) await characteristic.writeValueWithoutResponse(part);
        else throw new Error('Characteristic does not support writing.');
      }
    });
    this._tail = job;
    return job;
  }
  async _exclusive(kind, work) {
    if (!this.connected) throw new Error('Connect the device first.');
    if (this._operation || this._stopping) throw new Error('Another operation is in progress.');
    this._discardTerminalInput(); // Old keystrokes must never become file DATA.
    const operation = { kind, controller: new AbortController() };
    this._operation = operation; this._state(kind);
    try { return await work(operation.controller.signal); }
    finally {
      if (this._operation === operation) {
        this._operation = null;
        if (this.connected && !this._stopping) this._state('idle');
      }
    }
  }
  async _friendly(signal) {
    this._discardTerminalInput();
    this._received = '';
    // The combination also escapes the post-EOF file recovery state.
    await this._write(Uint8Array.of(3, 13, 2), this.rx, signal);
    await this._prompt('>>> ', 2500, signal);
  }
  // Synchronize once on connect/recovery, never before every interactive key.
  resumeRepl() { return this._exclusive('command', signal => this._friendly(signal)); }

  get terminalReady() {
    return this.connected && !this._stopping && (!this._operation ||
      (this._operation.kind === 'running' && this._raw?.phase === 1));
  }
  _discardTerminalInput() {
    this._terminalInput?.controller.abort();
    this._terminalInput = null;
  }
  writeTerminal(text) {
    // Managed Run/upload: use Stop cancellation. Manual REPL: send the actual
    // Ctrl+C byte so MicroPython keeps its current friendly/raw/paste mode.
    if (text === '\x03' && this._operation) return this.stop();
    if (!this.terminalReady) return Promise.reject(new Error('Terminal input is unavailable during upload or REPL setup.'));
    if (text === '\x03') this._discardTerminalInput(); // Interrupt must bypass a clipboard backlog.
    if (this._operation && /[\x01\x02\x04\x05]/.test(text))
      return Promise.reject(new Error('REPL mode controls are unavailable during editor Run; use Ctrl+C to stop.'));
    // Reject obviously oversized clipboard data before allocating its UTF-8 copy.
    if (text.length > 4096) return Promise.reject(new Error('Terminal input queue is full (4096 bytes). Use the editor for larger code.'));
    const bytes = encoder.encode(text);
    let session = this._terminalInput;
    if (!session) this._terminalInput = session = { controller:new AbortController(), bytes:new Uint8Array(), size:0, pump:null };
    if (bytes.length + session.size > 4096)
      return Promise.reject(new Error('Terminal input queue is full (4096 bytes). Use the editor for larger code.'));
    const joined = new Uint8Array(session.bytes.length + bytes.length);
    joined.set(session.bytes); joined.set(bytes, session.bytes.length);
    session.bytes = joined; session.size += bytes.length;
    if (!session.pump) {
      // Coalesce rapid typing; pace clipboard input so the board can drain its
      // REPL ring. The existing GATT lane also serializes joystick writes.
      session.pump = Promise.resolve().then(async () => {
        try {
          while (session.bytes.length) {
            check(session.controller.signal);
            const part = session.bytes.slice(0, Math.min(this.chunk, 32));
            session.bytes = session.bytes.slice(part.length);
            await this._write(part, this.rx, session.controller.signal);
            session.size -= part.length;
            await delay(10);
          }
        } finally {
          if (this._terminalInput === session) this._terminalInput = null;
        }
      });
    }
    return session.pump;
  }
  command(line) {
    if (/[\r\n\x00-\x1f]/.test(line)) return Promise.reject(new Error('Send one REPL line at a time.'));
    if (encoder.encode(line).length > 1024) return Promise.reject(new Error('Use the editor for longer code.'));
    return this._exclusive('command', async signal => {
      await this._friendly(signal);
      this._received = '';
      await this._write(encoder.encode(line + '\r'), this.rx, signal);
      // Friendly REPL can start an infinite loop. Return after send; Stop remains available.
    });
  }
  upload(path, bytes, onProgress = () => {}) {
    return this._exclusive('uploading', signal => this._upload(path, bytes, onProgress, signal));
  }
  async _upload(path, input, progress, signal) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    const start = header(path, bytes.length); // Validate BEFORE altering the target.
    // Freeze DATA size for this transfer: STATUS recovery uses sequence * dataSize.
    const dataSize = Math.min(240, this.chunk - 4);
    const count = Math.ceil(bytes.length / dataSize);
    if (count >= 65536) throw new Error('File needs too many DATA packets for this MTU.');
    this._uploading = true; this._replies = [];
    let finished = false;
    try {
      let accepted = false;
      for (let attempt = 0; attempt < 3 && !accepted; attempt++) {
        check(signal);
        await this._write(start, this.rx, signal);
        try {
          const r = await this._reply(2200, signal);
          if (r.ack && r.sequence === 65535) accepted = true;
          else throw new Error('Device rejected the file header.');
        } catch (error) {
          check(signal);
          if (!error.message.includes('timed out') || attempt === 2) throw error;
          // Header retry is idempotent ONLY before the first DATA packet.
        }
      }
      progress({ sent: 0, total: bytes.length });
      let next = 0, failures = 0;
      while (next < count) {
        check(signal);
        this._replies = [];
        // ACK windows are ABSOLUTE sequence numbers 3,7,11,..., plus final.
        const end = Math.min(count, (Math.floor(next / 4) + 1) * 4);
        for (let seq = next; seq < end; seq++) {
          await this._write(packet(seq, bytes.subarray(seq * dataSize,
            Math.min(bytes.length, (seq + 1) * dataSize))), this.rx, signal);
          await delay(10); check(signal);
        }
        let response, recovery = false;
        try { response = await this._reply(1600, signal); }
        catch (error) {
          check(signal);
          if (!error.message.includes('timed out')) throw error;
          // Never reopen/truncate after DATA. Ask how many packets reached the file.
          recovery = true;
        }
        if (response && !response.ack) recovery = true;
        if (recovery) {
          // Let queued NAKs drain before querying STATUS. The old wire format has
          // no request IDs, so keep just one query in flight and never pipeline windows.
          await delay(60); check(signal); this._replies = [];
          await this._write(control(0xfd), this.rx, signal);
          response = await this._reply(1600, signal);
          if (!response.ack || response.sequence === 65535)
            throw new Error('Device file error or cancelled transfer.');
          next = response.sequence; // STATUS ACK is NEXT, not LAST (even at EOF).
        } else {
          if (!response.ack || response.sequence !== end - 1) throw new Error('Unexpected file ACK.');
          next = end;
        }
        if (next < 0 || next > end) throw new Error('Invalid recovery sequence.');
        if (next < end && ++failures > 6) throw new Error('Too many file retries.');
        if (next === end) failures = 0;
        progress({ sent: Math.min(bytes.length, next * dataSize), total: bytes.length });
      }
      finished = true; // Empty file completes with the accepted header ACK.
    } finally {
      // Stop owns cancellation when its AbortController is responsible.
      if (!finished && !signal.aborted && this.connected) {
        try { await this._write(control(0xfe)); await delay(50); } catch { /* Link may already be lost. */ }
      }
      this._uploading = false; this._replies = [];
      if (!signal.aborted && this.connected) await this._friendly(signal);
    }
    progress({ sent: bytes.length, total: bytes.length });
  }
  run(source, path = '/ble_demo_run.py', onProgress = () => {}) {
    return this._exclusive('running', async signal => {
      this._state('uploading');
      await this._upload(path, encoder.encode(source), onProgress, signal);
      this._state('running');
      this._received = '';
      await this._write(Uint8Array.of(1), this.rx, signal);
      await this._prompt('raw REPL; CTRL-B to exit', 2500, signal);
      // Upload first: large editor contents never have to fit in the REPL input ring.
      // repr-like JSON quoting handles spaces, quotes, and UTF-8 in the destination.
      const wrapper = `__name__='__main__'\n__file__=${JSON.stringify(path)}\nwith open(__file__,'r') as _ble_demo_file:\n _ble_demo_code=compile(_ble_demo_file.read(),__file__,'exec')\ntry:\n exec(_ble_demo_code,globals())\nfinally:\n del _ble_demo_code,_ble_demo_file\n`;
      this._raw = { phase: 0, buffer: '', stdout: '', stderr: '', done: false, error: null };
      try {
        await this._write(encoder.encode(wrapper), this.rx, signal);
        await this._write(Uint8Array.of(4), this.rx, signal);
        const result = await this._wait(() => {
          if (this._raw?.error) throw this._raw.error;
          return this._raw?.done ? { stdout: this._raw.stdout, stderr: this._raw.stderr } : undefined;
        }, 0, signal); // User code has no artificial runtime deadline; Stop aborts this wait.
        this._raw = null;
        await this._friendly(signal);
        return result;
      } finally { this._discardTerminalInput(); this._raw = null; }
    });
  }
  stop() {
    if (this._stopping) return this._stopping;
    if (!this.connected) return Promise.resolve();
    this._discardTerminalInput();
    const wasUpload = this._uploading;
    this._operation?.controller.abort(); // Invalidates queued old writes before scheduling Stop.
    this._state('stopping');
    this._stopping = (async () => {
      try {
        if (wasUpload) {
          this._uploading = true;
          await this._write(control(0xfe)); // Close any partial target BEFORE Ctrl+C enters REPL.
          await delay(60);
        }
        this._uploading = false; this._replies = []; this._raw = null;
        await this._friendly();
      } finally {
        this._stopping = null;
        if (this.connected) this._state('idle');
      }
    })();
    return this._stopping;
  }
  joystick(axes) {
    if (!this.joy) return Promise.reject(new Error('Joystick service is unavailable.'));
    if (axes.length !== 4) return Promise.reject(new Error('Expected Lx, Ly, Rx, Ry.'));
    const signed = Int8Array.from(axes, value => Math.max(-100, Math.min(100, Math.round(value))));
    return this._write(new Uint8Array(signed.buffer), this.joy);
  }
}
