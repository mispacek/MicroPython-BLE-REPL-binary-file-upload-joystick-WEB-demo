// SPDX-License-Identifier: MIT
// UI wiring lives here; projects can import ble-client.js without any of this DOM.
import { BleReplClient } from './ble-client.js?v=1.2.0';
import { bindEditor } from './editor.js';
import { createReplTerminal } from './terminal.js';
const $ = id => document.getElementById(id);
const client = new BleReplClient();
const examples = {
  hello: `# This file is uploaded, then executed in the board's REPL globals.\nimport sys\nimport time\n\nprint("Hello over Bluetooth!")\nprint(sys.implementation)\nfor i in range(5):\n    print("Tick", i)\n    time.sleep_ms(250)\n`,
  joystick: `# Keep the existing BLE driver alive: do NOT start a second instance.\nfrom ble_repl import Joystick, joy_read\nimport time\n\nleft = Joystick(0)\nright = Joystick(10)\nprint("Move either joystick. Press Stop to finish.")\nwhile True:\n    axes, age_ms = joy_read()\n    print("L:", left.get_joyX(), left.get_joyY(),\n          "R:", right.get_joyX(), right.get_joyY(),\n          "age:", age_ms)\n    time.sleep_ms(250)\n`,
  diagnostics: `from ble_repl import get_active\n\nble = get_active()\nif ble is None:\n    print("No active BLE REPL driver")\nelse:\n    for key, value in ble.stats().items():\n        print(key, "=", value)\n    print("GC memory:", ble.mem_usage())\n`,
  loop: `# Stop interrupts user Python code; it leaves the BLE transport running.\nimport time\n\ntry:\n    count = 0\n    while True:\n        print("Running", count)\n        count += 1\n        time.sleep_ms(500)\nexcept KeyboardInterrupt:\n    print("Stopped. The board is ready for the next command.")\n`,
};
const refreshEditor = bindEditor($('code'), $('highlight'), $('editor-count'));
$('code').value = examples.hello; refreshEditor();
function notice(message, error = false) {
  $('notice').textContent = message; $('notice').classList.toggle('error', error);
}
function handle(error) {
  if (error.name !== 'AbortError') notice(error.message || String(error), true);
}

const terminal = createReplTerminal($('terminal'), data => client.writeTerminal(data), handle);
terminal.write('BLE REPL ready.\r\nConnect a board, then click here to type.\r\n\r\n↑ ↓ history · Tab completion\r\nCtrl+C interrupts · Shift+Tab exits\r\n');
client.on('text', terminal.write);
client.on('stderr', terminal.write);
function updateTerminal() {
  terminal.setWritable(client.terminalReady);
  $('terminal-state').textContent = !client.connected ? 'Connect to type here' :
    client.terminalReady ? (client.state === 'running' ? 'Program input · click to type' : 'Click here to type') : 'Input paused during transfer';
  for (const button of document.querySelectorAll('[data-repl]'))
    button.disabled = !client.connected || client.state === 'stopping' ||
      client.uploadActive ||
      (button.dataset.repl !== '3' && client.state !== 'idle');
}
client.on('terminalReady', updateTerminal);
updateTerminal();
client.on('notice', message => notice(message));
client.on('device', name => { $('device').textContent = name; });
client.on('config', ({ mtu, chunk }) => { $('mtu').textContent = `MTU ${mtu} · payload ${chunk} B`; });
client.on('state', state => {
  const online = client.connected;
  const idle = online && state === 'idle';
  $('status').textContent = ({ idle:'Connected', uploading:'Uploading…', running:'Running…',
    command:'Sending…', stopping:'Stopping…', connecting:'Connecting…', disconnected:'Disconnected' })[state] || state;
  $('status').classList.toggle('online', online);
  $('connect').disabled = online || state === 'connecting';
  $('disconnect').disabled = !online;
  $('run').disabled = !idle; $('upload').disabled = !idle;
  $('stop').disabled = !online || state === 'stopping' || client.uploadActive; updateTerminal();
  for (const id of ['left-stick', 'right-stick']) $(id).setAttribute('aria-disabled', String(!online || !client.joy || state === 'uploading' || state === 'stopping'));
  if (!online) { $('device').textContent = 'No board selected'; $('mtu').textContent = 'MTU 23 · payload 20 B'; center(); }
});
function progress({ sent, total }) {
  $('progress').value = total ? sent / total * 100 : 100;
  $('progress-label').textContent = `${sent.toLocaleString()} / ${total.toLocaleString()} bytes`;
}
$('connect').addEventListener('click', async () => {
  try {
    await client.connect(); await client.resumeRepl();
    terminal.focus(); notice('Connected. Type directly in the terminal or run the editor example.');
  } catch (error) { handle(error); }
});
$('disconnect').addEventListener('click', () => { center(); client.disconnect(); notice('Disconnected.'); });
$('stop').addEventListener('click', () => { center(); client.stop().then(stopped => {
  if (stopped !== false) notice('Stopped. The REPL is ready.');
}).catch(handle); });
$('run').addEventListener('click', async () => {
  center();
  try {
    notice('Sending the editor file and running it…');
    const result = await client.run($('code').value, $('run-path').value, progress);
    notice(result.stderr ? 'Program finished with an error. See the terminal.' : 'Program finished.', Boolean(result.stderr));
  } catch (error) { handle(error); }
});
$('code').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (!$('run').disabled) $('run').click(); }
});
for (const button of document.querySelectorAll('[data-repl]')) button.addEventListener('click', () => {
  client.writeTerminal(String.fromCharCode(Number(button.dataset.repl))).catch(handle);
  terminal.focus();
});
$('clear').addEventListener('click', () => { terminal.clear(); terminal.focus(); });
$('example').addEventListener('change', () => { $('code').value = examples[$('example').value]; refreshEditor(); });
$('load').addEventListener('click', () => $('load-file').click());
$('load-file').addEventListener('change', async () => {
  const file = $('load-file').files[0]; if (!file) return;
  if (file.size > 1024 * 1024) { notice('Editor files are limited to 1 MiB.', true); return; }
  try { $('code').value = await file.text(); refreshEditor(); }
  catch (error) { handle(error); }
});
$('save').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([$('code').value], { type:'text/x-python;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = 'ble_demo_run.py'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('upload-file').addEventListener('change', () => {
  const file = $('upload-file').files[0]; if (file) $('upload-path').value = '/' + file.name;
});
$('upload').addEventListener('click', async () => {
  const file = $('upload-file').files[0];
  if (!file) { notice('Choose a file first.', true); return; }
  if (file.size > 0xffffff) { notice('The wire format supports at most 16,777,215 bytes.', true); return; }
  center();
  try {
    notice(`Uploading ${file.name}…`);
    // arrayBuffer is essential: text() would corrupt .mpy and other binary files.
    await client.upload($('upload-path').value, new Uint8Array(await file.arrayBuffer()), progress);
    notice(`Uploaded to ${$('upload-path').value}.`);
  } catch (error) { handle(error); }
});

// Latest-state joystick sender: at most ONE queued/in-flight update. Held pads
// refresh every 80 ms; the board's 3-second stale-input protection remains active.
const axes = [0, 0, 0, 0];
let dirty = false, sending = false;
function setAxes(index, x, y) {
  axes[index] = Math.round(Math.max(-100, Math.min(100, x)));
  axes[index + 1] = Math.round(Math.max(-100, Math.min(100, y)));
  const side = index ? 'right' : 'left';
  $(`${side}-axes`).textContent = `X ${axes[index]} · Y ${axes[index + 1]}`;
  $(`${side}-stick`).querySelector('i').style.transform = `translate(${axes[index] * .38}px,${-axes[index + 1] * .38}px)`;
  dirty = true;
}
function center() { setAxes(0, 0, 0); setAxes(2, 0, 0); }
function bindStick(element, index) {
  let pointer = null;
  const keys = new Set();
  const move = event => {
    const r = element.getBoundingClientRect(); const radius = r.width / 2 - 22;
    let x = (event.clientX - r.left - r.width / 2) / radius;
    let y = (r.top + r.height / 2 - event.clientY) / radius;
    const length = Math.hypot(x, y); if (length > 1) { x /= length; y /= length; }
    setAxes(index, x * 100, y * 100);
  };
  element.addEventListener('pointerdown', event => {
    if (element.getAttribute('aria-disabled') === 'true') return;
    pointer = event.pointerId; element.setPointerCapture(pointer); element.focus(); move(event);
  });
  element.addEventListener('pointermove', event => { if (pointer === event.pointerId) move(event); });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) element.addEventListener(type, event => {
    if (pointer === event.pointerId) { pointer = null; setAxes(index, 0, 0); }
  });
  const updateKeys = () => setAxes(index, (Number(keys.has('ArrowRight')) - Number(keys.has('ArrowLeft'))) * 100,
    (Number(keys.has('ArrowUp')) - Number(keys.has('ArrowDown'))) * 100);
  element.addEventListener('keydown', event => {
    if (!event.key.startsWith('Arrow') || element.getAttribute('aria-disabled') === 'true') return;
    event.preventDefault(); keys.add(event.key); updateKeys();
  });
  element.addEventListener('keyup', event => { if (keys.delete(event.key)) { event.preventDefault(); updateKeys(); } });
  element.addEventListener('blur', () => { keys.clear(); pointer = null; setAxes(index, 0, 0); });
}
bindStick($('left-stick'), 0); bindStick($('right-stick'), 2);
window.addEventListener('blur', center);
document.addEventListener('visibilitychange', () => { if (document.hidden) center(); });
setInterval(async () => {
  if (sending || !client.connected || !client.joy || !['idle','running','command'].includes(client.state)) return;
  if (!dirty && !axes.some(Boolean)) return;
  sending = true; dirty = false;
  try { await client.joystick(axes.slice()); }
  catch (error) { handle(error); }
  finally { sending = false; }
}, 80);
if (!globalThis.isSecureContext || !navigator.bluetooth) {
  $('connect').disabled = true;
  notice('Web Bluetooth is unavailable. Open this page on localhost or HTTPS in a supported browser.', true);
}
