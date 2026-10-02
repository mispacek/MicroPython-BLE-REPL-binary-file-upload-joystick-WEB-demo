"""MIT. Deterministic package checks; never opens BLE/USB or writes bytecode."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
for path in root.rglob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
for path in (root / "web").glob("*.js"):
    subprocess.run(["node", "--check", str(path)], check=True)
for path in root.glob("*.md"):
    for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("#"):
            continue
        local = target.split("#", 1)[0]
        assert (path.parent / local).exists(), f"Broken local documentation link: {path}: {target}"
html = (root / "web/index.html").read_text(encoding="utf-8")
for target in re.findall(r'(?:href|src)="([^"]+)"', html):
    assert (root / "web" / target).exists(), f"Broken demo asset link: {target}"
assert json.loads((root / "package.json").read_text())["license"] == "MIT"
assert (root / "firmware/ble_repl.py").stat().st_size <= 40000
for name in ("hardware-results.json", "hardware-results-mtu23.json"):
    path = root / "tests" / name
    if path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
        assert report["passed"] and len(report["results"]) == 9
        assert report["restored"]["root"] == report["baseline"]["root"]
        assert report["restored"]["file_sha256"] == report["baseline"]["file_sha256"]
        assert report["restored"]["ble_active"] == report["baseline"]["ble_active"]
        assert report["restored"]["radio_settings"] == report["baseline"]["radio_settings"]
        for key, source in (("source_sha256", "firmware/ble_repl.py"), ("client_sha256", "web/ble-client.js")):
            assert report[key] == hashlib.sha256((root / source).read_bytes()).hexdigest(), f"Stale hardware report: {name} / {source}"
print("Python/JS syntax, relative links, license, source budget and hardware hashes OK")
