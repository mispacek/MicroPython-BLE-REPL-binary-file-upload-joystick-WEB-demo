# SPDX-License-Identifier: MIT
# Legacy Blockly imports use the real utime functions; no sleep polling or stop flag.
import utime as _t

for _n in dir(_t):
    if not _n.startswith('_'):
        globals()[_n] = getattr(_t, _n)
del _n


def install_as_utime():
    return _t
