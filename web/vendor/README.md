# Terminal assets

Unmodified MIT distributions from [xterm.js](https://github.com/xtermjs/xterm.js):

- `@xterm/xterm` **6.0.0**: ES module, CSS, source map and [license](xterm/LICENSE).
- `@xterm/addon-fit` **0.11.0**: ES module, source map and [license](addon-fit/LICENSE).

These local assets require no CDN or npm installation to run the demo. Keep both
copyright notices when redistributing. The readable integration is `../terminal.js`;
edit that file instead of vendor distributions.

To reproduce, run `npm pack @xterm/xterm@6.0.0` and
`npm pack @xterm/addon-fit@0.11.0` with `--pack-destination %TEMP%/espide-xterm-6`,
then `python -B tools/vendor-xterm.py` from the repository root. The script verifies
each archive's pinned SHA-512 integrity before copying exact bytes.
