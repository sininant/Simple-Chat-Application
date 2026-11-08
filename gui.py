"""Desktop GUI entrypoint and backward-compatible alias for chat.gui."""

import sys
from pathlib import Path

# Ensure 'src' is in sys.path so 'chat' package is always resolvable
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import chat.gui as _cg  # noqa: E402

# Alias this module to chat.gui so all attributes and monkeypatches target the same module.
sys.modules[__name__] = _cg

if __name__ == "__main__":
    _cg.main()
