// SPDX-License-Identifier: MIT. Node adapter for the SAME client shipped in web/.
// Python owns Windows BLE and USB; JSON lines keep hardware plumbing out of the library.
import readline from 'node:readline';
import assert from 'node:assert/strict';
import { BleReplClient, UUID } from '../web/ble-client.js';
let serial = 0;
const requests = new Map();
const client = new BleReplClient();
let gotConfig = false;
client.on('config', () => { gotConfig = true; });
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', line => {
  const message = JSON.parse(line);
  if (message.notification) client.receive(new Uint8Array(Buffer.from(message.notification, 'base64')));
  else {
    const request = requests.get(message.id);
    if (request) { requests.delete(message.id); message.error ? request.reject(new Error(message.error)) : request.resolve(message.value); }
  }
});
function request(operation, args = {}) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    requests.set(id, { resolve, reject });
    process.stdout.write(JSON.stringify({ id, operation, ...args }) + '\n');
  });
}
const characteristic = uuid => ({ properties: { write: true }, writeValueWithResponse: bytes => request('write', { uuid, bytes: Buffer.from(bytes).toString('base64') }) });
const tx = { addEventListener() {}, removeEventListener() {}, startNotifications: () => request('notify') };
const root = '/__ble_demo_test';
const results = [];
async function group(name, work) {
  const begin = Date.now(); const detail = await work();
  results.push({ name, passed: true, ms: Date.now() - begin, detail });
}
try {
  await client.attach(characteristic(UUID.rx), tx, characteristic(UUID.axes));
  await group('CONFIG capability and negotiated MTU', async () => {
    await client._wait(() => gotConfig ? true : undefined, 2500);
    assert.equal(client.mtu, Number(process.argv[2] || 247));
    return { mtu:client.mtu, chunk:client.chunk };
  });
  await group('interactive REPL editing / history / completion / paste / raw modes', async () => {
    let output = '';
    const off = client.on('text', text => { output += text; });
    const exchange = async (keys, pattern) => {
      output = '';
      await client.writeTerminal(keys);
      await client._wait(() => pattern.test(output) ? true : undefined, 5000);
      return output;
    };
    try {
      await client.resumeRepl(); output = '';
      // Type without Enter: the VM echoes the unfinished line, without executing it.
      await Promise.all([... 'print(41)'].map(key => client.writeTerminal(key)));
      await client._wait(() => output.includes('print(41)') ? true : undefined, 2500);
      assert.ok(!output.includes('\r\n41\r\n'));
      const edit = await exchange('\x1b[D\x082\x1b[F\r', /\r\n42\r\n>>> /);
      const history = await exchange('\x1b[A\r', /\r\n42\r\n>>> /);
      assert.match(history, /print\(42\)/);
      // Home inserts a comment at the actual beginning; End moves back to the end.
      const home = await exchange('print(6*7)\x1b[H#\x1b[F\r', /\r\n>>> /);
      assert.ok(!/\r\n42\r\n/.test(home));
      await exchange('import sys\r', /\r\n>>> /);
      const completion = await exchange('sys.imple\t\r', /name='micropython'/);
      assert.match(completion, /implementation/);
      // Explicit paste mode prevents friendly REPL auto-indent from altering a block.
      await exchange('\x05', /paste mode/);
      await client.writeTerminal('for _demo_i in range(2):\r    print("PASTE", _demo_i)\r');
      const paste = await exchange('\x04', /PASTE 0\r\nPASTE 1\r\n>>> /);
      await exchange('\x01', /raw REPL; CTRL-B to exit/);
      await client.writeTerminal('print("RAW-DEMO")\n');
      const raw = await exchange('\x04', /OKRAW-DEMO\r\n\x04\x04>/);
      await exchange('\x02', />>> /);
      return { edit, history, completion, paste, raw };
    } finally { off(); }
  });
  await group('binary upload and independent USB SHA-256 readback', async () => {
    const bytes = Uint8Array.from({ length:4097 }, (_, i) => i * 73 & 255);
    await client.upload(root + '/nested/payload.bin', bytes);
    const digest = await request('digest', { path:root + '/nested/payload.bin' });
    const { createHash } = await import('node:crypto');
    assert.equal(digest, createHash('sha256').update(bytes).digest('hex'));
    return { bytes:bytes.length, sha256:digest };
  });
  await group('empty file', async () => {
    await client.upload(root + '/empty.bin', new Uint8Array());
    assert.equal(await request('size', { path:root + '/empty.bin' }), 0);
  });
  await group('editor Run / stdout / Python exception / next Run', async () => {
    const r = await client.run("print('Hello žluťoučký!')\nraise ValueError('demo')", root + '/run.py');
    assert.match(r.stdout, /Hello žluťoučký!/); assert.match(r.stderr, /ValueError: demo/);
    const next = await client.run('print(6 * 7)', root + '/run.py');
    assert.equal(next.stdout.trim(), '42');
    return { stdout:r.stdout, stderr:r.stderr, next:next.stdout };
  });
  await group('interactive stdin input() during editor Run', async () => {
    const job = client.run('print("INPUT-READY")\n_demo_answer = input("Name: ")\nprint("INPUT-ANSWER:", _demo_answer)', root + '/run.py');
    // Attach rejection handling immediately; a failed readiness assertion still cleans up.
    job.catch(() => {});
    await client._wait(() => client._received.includes('Name: ') && client.terminalReady ? true : undefined, 10000);
    await client.writeTerminal('Ada\r');
    const r = await job;
    assert.match(r.stdout, /INPUT-ANSWER: Ada/); assert.equal(r.stderr, '');
    return { stdout:r.stdout, stderr:r.stderr };
  });
  await group('both signed joystick pairs', async () => {
    const job = client.run('from ble_repl import Joystick\nimport time\nprint("JOY-READY")\ntime.sleep_ms(1000)\nprint(Joystick(0).get_joyX(),Joystick(0).get_joyY(),Joystick(10).get_joyX(),Joystick(10).get_joyY())', root + '/run.py');
    await client._wait(() => client._received.includes('JOY-READY') ? true : undefined, 10000);
    // Send AFTER the slow MTU-23 upload/REPL setup: older input correctly expires at 3 s.
    await client.joystick([-70, 80, 90, -100]);
    const r = await job;
    assert.match(r.stdout.trim(), /-70 80 90 -100$/); return r.stdout.trim();
  });
  await group('Stop busy Python / BLE remains usable', async () => {
    const job = client.run("print('BUSY-DEMO')\nwhile True: pass", root + '/run.py');
    const rejected = assert.rejects(job, { name:'AbortError' });
    await client._wait(() => client._received.includes('BUSY-DEMO') ? true : undefined, 5000);
    const begin = Date.now(); await client.writeTerminal('\x03'); await rejected;
    const r = await client.run('print("AFTER-STOP")', root + '/run.py');
    assert.match(r.stdout, /AFTER-STOP/); return { stopAndNextRunMs:Date.now() - begin };
  });
  await group('Stop upload / partial file / successful replacement', async () => {
    const job = client.upload(root + '/partial.bin', new Uint8Array(32768));
    const rejected = assert.rejects(job, { name:'AbortError' });
    await new Promise(resolve => setTimeout(resolve, 180));
    await client.stop(); await rejected;
    await client.upload(root + '/partial.bin', Uint8Array.of(10,20,30));
    assert.equal(await request('size', { path:root + '/partial.bin' }), 3);
  });
  await request('done', { results });
} catch (error) {
  process.stderr.write(error.stack + '\n');
  await request('failed', { error:error.stack, results }); process.exitCode = 1;
} finally { client.disconnect(); rl.close(); }
