"""MIT. Copy exact pinned upstream assets; never rewrite minified vendor code.

First npm pack @xterm/xterm@6.0.0 and @xterm/addon-fit@0.11.0 into
%TEMP%/espide-xterm-6. Integrity below comes from npm registry metadata.
"""
import base64
import hashlib
import os
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1] / "web/vendor"
specs = [
    ("xterm-xterm-6.0.0.tgz", "xterm", "TQwDdQGtwwDt+2cgKDLn0IRaSxYu1tSUjgKarSDkUM0ZNiSRXFpjxEsvc/Zgc5kq5omJ+V0a8/kIM2WD3sMOYg==",
     ["LICENSE", "lib/xterm.mjs", "lib/xterm.mjs.map", "css/xterm.css"]),
    ("xterm-addon-fit-0.11.0.tgz", "addon-fit", "jYcgT6xtVYhnhgxh3QgYDnnNMYTcf8ElbxxFzX0IZo+vabQqSPAjC3c1wJrKB5E19VwQei89QCiZZP86DCPF7g==",
     ["LICENSE", "lib/addon-fit.mjs", "lib/addon-fit.mjs.map"]),
]
for archive, folder, integrity, members in specs:
    source = Path(os.environ["TEMP"]) / "espide-xterm-6" / archive
    assert base64.b64encode(hashlib.sha512(source.read_bytes()).digest()).decode() == integrity
    with tarfile.open(source) as package:
        for name in members:
            target = root / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.extractfile("package/" + name).read())
            print(str(target.relative_to(root)), hashlib.sha256(target.read_bytes()).hexdigest())
