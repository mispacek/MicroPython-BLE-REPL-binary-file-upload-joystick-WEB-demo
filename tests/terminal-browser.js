// SPDX-License-Identifier: MIT. Tests the same renderer used in web/app.js.
import { createReplTerminal } from '../web/terminal.js';
const $ = id => document.getElementById(id), keys = [];
const terminal = createReplTerminal($('terminal'), text => {
  keys.push(text); $('keys').textContent = JSON.stringify(keys);
}, error => { $('result').textContent = 'FAIL: ' + error.message; });
terminal.setWritable(true); terminal.focus();
$('terminal').addEventListener('keydown', event => {
  $('events').textContent = ($('events').textContent + JSON.stringify({key:event.key,code:event.code,ctrl:event.ctrlKey,meta:event.metaKey}) + '\n').slice(-4000);
}, true);
$('pause').onclick = () => terminal.setWritable(false);
$('resume').onclick = () => { terminal.setWritable(true); terminal.focus(); };
$('interrupt-check').onclick = async () => {
  // Synthetic handler regression only, separate from real browser key delivery.
  // Some browser automation providers reserve Ctrl+C for their clipboard API.
  const count = keys.length;
  const textarea = $('terminal').querySelector('textarea');
  textarea.dispatchEvent(new KeyboardEvent('keydown', {key:'c',code:'KeyC',ctrlKey:true,bubbles:true,cancelable:true}));
  await Promise.resolve(); await Promise.resolve();
  $('result').textContent = keys.length === count + 1 && keys.at(-1) === '\x03' ? 'PASS: synthetic Ctrl+C handler sends exactly one interrupt byte' : 'FAIL: interrupt handler';
  terminal.focus();
};
$('paste-check').onclick = async () => {
  // Synthetic ClipboardEvent tests xterm's real paste path and newline conversion.
  // It does not assert OS clipboard permission or an actual Bluetooth connection.
  const count = keys.length, data = new DataTransfer();
  data.setData('text/plain', 'for i in range(2):\r\n    print(i)\n');
  $('terminal').querySelector('textarea').dispatchEvent(new ClipboardEvent('paste', {clipboardData:data,bubbles:true,cancelable:true}));
  await Promise.resolve(); await Promise.resolve();
  $('result').textContent = keys.length === count + 1 && keys.at(-1) === 'for i in range(2):\r    print(i)\r' ? 'PASS: synthetic paste preserves indentation and converts CRLF/LF to CR' : 'FAIL: paste handler';
  terminal.focus();
};
$('checks').onclick = async () => {
  // Split CSI across calls exactly as BLE notification boundaries may split it.
  terminal.write('\x1b[2J\x1b[H>>> print(41)\x1b['); terminal.write('2D2)\x1b[K');
  terminal.write('\r\n42\r\n>>> wrong\r>>> right\x1b[K');
  terminal.write('\r\n>>> abc\b \bZ');
  terminal.write('\r\n\x1b[31mValueError\x1b[0m\r\n>>> ');
  const expected = ['>>> print(42)', '42', '>>> right', '>>> abZ', 'ValueError', '>>>'];
  // Parsing and DOM painting use different scheduled tasks. Wait for the actual
  // screen, with a deadline, rather than assuming two animation frames are enough.
  let rows = [], deadline = performance.now() + 2000;
  do {
    await new Promise(resolve => setTimeout(resolve, 25));
    rows = [...$('terminal').querySelectorAll('.xterm-rows > div')].map(row => row.textContent.trimEnd());
  } while (JSON.stringify(rows.slice(0, 6)) !== JSON.stringify(expected) && performance.now() < deadline);
  $('result').textContent = JSON.stringify(rows.slice(0, 6)) === JSON.stringify(expected) ? 'PASS: fragmented ANSI, cursor replacement, CR, backspace and color' : 'FAIL: ' + JSON.stringify(rows);
  terminal.focus();
};
