// SPDX-License-Identifier: MIT
// The packaged Python driver and browser client exchange real protocol bytes.
// GATT and the prompt are host models: this is never a physical-device test.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import readline from 'node:readline';
import assert from 'node:assert/strict';
import test from 'node:test';
import {BleReplClient} from '../web/ble-client.js';

for (const mtu of [23, 247]) test(`packaged client + packaged driver recovery, MTU ${mtu}`, {timeout:15000}, async t => {
  const child = spawn('python', ['-B', fileURLToPath(new URL('driver-peer.py', import.meta.url))]);
  const client = new BleReplClient();
  t.after(() => { client.disconnect(); if (child.exitCode === null) child.kill(); });
  let pending, diagnostics = '', notify, dropFinal = false, stopped = false;
  child.stderr.on('data', data => { diagnostics += data; });
  child.on('exit', code => { pending?.reject(new Error(`Driver peer exited ${code}: ${diagnostics}`)); });
  readline.createInterface({input:child.stdout}).on('line', line => {
    const reply = JSON.parse(line);
    for (const bytes of reply.notifications) for (const byte of bytes) {
      const data = Uint8Array.of(byte);
      notify?.({target:{value:new DataView(data.buffer)}});
    }
    const request = pending; pending = null; request.resolve(reply);
  });
  const request = data => new Promise((resolve, reject) => {
    assert.ok(!pending, 'one GATT/observation request at a time');
    pending = {resolve, reject}; child.stdin.write(JSON.stringify(data)+'\n');
  });
  const rx = {properties:{write:true}, writeValueWithResponse:async bytes => {
    const reply = await request({op:'write', data:Array.from(bytes), drop_final:dropFinal});
    if (reply.stats.phase === 2 && !stopped) {
      stopped = true;
      assert.equal(await client.stop(), false);
      assert.equal(await client.writeTerminal('\x03'), false);
      const ignored = await request({op:'stop'});
      assert.equal(ignored.stats.phase, 2);
      assert.equal(ignored.stats.stop_ignored, 1);
    }
  }};
  const tx = {addEventListener:(_name, fn) => {notify=fn;}, removeEventListener:() => {notify=null;}, startNotifications:async () => {}};
  await request({op:'connect', mtu});
  await client.attach(rx, tx);
  assert.equal(client.mtu, mtu);
  const original = client._reply.bind(client);
  client._reply = (_timeout, signal) => original(150, signal);
  const bytes = Uint8Array.from({length:4097}, (_, i)=>(i*53+3)&255);
  await client.upload('/fixture.bin', bytes);
  assert.deepEqual(Uint8Array.from((await request({op:'check', path:'/fixture.bin'})).bytes), bytes);
  dropFinal = true;
  await client.upload('/final.bin', Uint8Array.of(0,3,255));
  assert.equal((await request({op:'stats'})).dropped_final, true);
  assert.deepEqual((await request({op:'check', path:'/final.bin'})).bytes, [0,3,255]);
  dropFinal = false;
  await assert.rejects(client.upload('/fixture.bin/child.bin', Uint8Array.of(1)), /BLE Error reason=file errno=20/);
  assert.equal((await request({op:'stats'})).stats.connected, true);
  await client.upload('/recovered.bin', Uint8Array.of(9,8,7));
  assert.deepEqual((await request({op:'check', path:'/recovered.bin'})).bytes, [9,8,7]);
  await client.command('print(42)');
  assert.match(client._received, /42/);
});
