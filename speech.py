"""Speech recognition adapters and backward-compatible alias for chat.speech."""

import sys
from pathlib import Path

# Ensure 'src' is in sys.path so 'chat' package is always resolvable
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import chat.speech as _csp  # noqa: E402

sys.modules[__name__] = _csp
