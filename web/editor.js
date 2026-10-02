// SPDX-License-Identifier: MIT
// Small, deliberately conservative Python lexer: display highlighting, not validation.
// It escapes HTML FIRST at token rendering, so pasted source never becomes markup.
const keywords = new Set('False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield'.split(' '));
const builtins = new Set('print range len str int float bool bytes bytearray list dict tuple set open exec compile enumerate zip min max sum abs isinstance super'.split(' '));
const escape = text => text.replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function highlightPython(source) {
  let i = 0, html = '';
  const render = (text, type) => type ? `<span class="tok-${type}">${escape(text)}</span>` : escape(text);
  while (i < source.length) {
    const start = i;
    let type = '';
    if (source[i] === '#') {
      type = 'comment'; while (i < source.length && source[i] !== '\n') i++;
    } else {
      // Prefixes b/r/u/f and triple-quoted strings. F-string expressions are kept
      // as one string token; a complete Python parser would be much less educational.
      const string = /^(?:[rRuUbBfF]{0,2})("""|'''|"|')/.exec(source.slice(i));
      if (string) {
        type = 'string'; const quote = string[1]; i += string[0].length;
        while (i < source.length) {
          if (source[i] === '\\') { i = Math.min(source.length, i + 2); continue; }
          if (source.startsWith(quote, i)) { i += quote.length; break; }
          if (quote.length === 1 && source[i] === '\n') break;
          i++;
        }
      } else {
        const word = /^[A-Za-z_]\w*/.exec(source.slice(i));
        const number = /^(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?[jJ]?)/.exec(source.slice(i));
        if (word) { i += word[0].length; type = keywords.has(word[0]) ? 'keyword' : builtins.has(word[0]) ? 'builtin' : ''; }
        else if (number) { i += number[0].length; type = 'number'; }
        else i++;
      }
    }
    html += render(source.slice(start, i), type);
  }
  return html + '\n'; // Preserve the last empty visual line beneath the textarea.
}
export function bindEditor(textarea, preview, counter) {
  const update = () => {
    preview.innerHTML = highlightPython(textarea.value);
    counter.textContent = `${textarea.value.split('\n').length} lines · ${new TextEncoder().encode(textarea.value).length} bytes`;
    preview.scrollTop = textarea.scrollTop; preview.scrollLeft = textarea.scrollLeft;
  };
  textarea.addEventListener('input', update);
  textarea.addEventListener('scroll', () => { preview.scrollTop = textarea.scrollTop; preview.scrollLeft = textarea.scrollLeft; });
  textarea.addEventListener('keydown', event => {
    if (event.key === 'Tab') {
      event.preventDefault();
      textarea.setRangeText('    ', textarea.selectionStart, textarea.selectionEnd, 'end'); update();
    }
  });
  update(); return update;
}
