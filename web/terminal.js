// SPDX-License-Identifier: MIT
// xterm owns the screen/cursor/selection/IME; MicroPython owns line editing,
// history and completion. No local echo: display ONLY output received from the board.
import { Terminal } from './vendor/xterm/lib/xterm.mjs';
import { FitAddon } from './vendor/addon-fit/lib/addon-fit.mjs';

export function createReplTerminal(element, send, onError) {
  const term = new Terminal({
    fontFamily:'"Cascadia Code", Consolas, "Liberation Mono", monospace',
    fontSize:12, lineHeight:1.55, cursorStyle:'bar', cursorBlink:true,
    scrollback:2000, screenReaderMode:true, disableStdin:true,
    // BLE output contains CR/LF; preserve ANSI cursor controls across notifications.
    convertEol:false,
    theme:{ background:'#f8fbfe', foreground:'#3c566a', cursor:'#287cc4',
      cursorAccent:'#f8fbfe', selectionBackground:'#b8d8f080',
      black:'#30465b', red:'#b85555', green:'#3b785d', yellow:'#9b6924',
      blue:'#287cc4', magenta:'#7a4da1', cyan:'#328a94', white:'#8b9aa7',
      brightBlack:'#738598', brightRed:'#c96262', brightGreen:'#4c9371',
      brightYellow:'#b98532', brightBlue:'#398fd4', brightMagenta:'#9462b8',
      brightCyan:'#44a3ae', brightWhite:'#c6d3de' },
  });
  const fit = new FitAddon();
  term.loadAddon(fit); term.open(element);
  term.textarea.setAttribute('aria-label', 'MicroPython terminal input');
  term.textarea.setAttribute('autocapitalize', 'off');
  term.textarea.setAttribute('autocorrect', 'off');
  const input = data => Promise.resolve().then(() => send(data)).catch(onError);
  term.onData(input);
  term.attachCustomKeyEventHandler(event => {
    // Shift+Tab leaves the terminal; ordinary Tab belongs to board completion.
    if (event.key === 'Tab' && event.shiftKey) return false;
    // Keep native copy of selected output and native paste (Ctrl/⌘+V).
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c' && term.hasSelection()) return false;
    const clear = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
    const keys = { Backspace:'\x08', Home:'\x1b[H', End:'\x1b[F' };
    const interrupt = event.ctrlKey && event.key.toLowerCase() === 'c';
    if (clear || interrupt || (!event.altKey && !event.ctrlKey && !event.metaKey && keys[event.key])) {
      event.preventDefault();
      if (event.type === 'keydown') {
        if (clear) term.clear();
        else if (interrupt) input('\x03');
        else if (!term.options.disableStdin) input(keys[event.key]);
      }
      return false;
    }
    return true;
  });
  // Fit only after the host changes. Renderer row changes are contained by CSS
  // and must never change the host's size or grow the entire page on each frame.
  let lastWidth = -1, lastHeight = -1;
  const observer = new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect;
    if (width > 0 && height > 0 && (width !== lastWidth || height !== lastHeight)) {
      lastWidth = width; lastHeight = height; fit.fit();
    }
  });
  observer.observe(element); fit.fit();
  document.fonts.ready.then(() => fit.fit());
  return {
    write: text => term.write(text),
    clear: () => term.clear(), // Retains current prompt/line; sends no control byte.
    focus: () => term.focus(),
    setWritable: enabled => {
      term.options.disableStdin = !enabled;
      term.textarea.setAttribute('aria-disabled', String(!enabled));
      element.classList.toggle('input-paused', !enabled);
    },
    dispose: () => { observer.disconnect(); term.dispose(); },
  };
}
