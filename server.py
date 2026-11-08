"""Socket.IO server entrypoint and backward-compatible alias for chat.server."""

import sys
from pathlib import Path

# Ensure 'src' is in sys.path so 'chat' package is always resolvable
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import chat.server as _cs  # noqa: E402

sys.modules[__name__] = _cs

if __name__ == "__main__":
    _cs.main()
