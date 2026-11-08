"""Desktop GUI entrypoint and backward-compatible alias for chat.gui."""

import sys

import chat.gui as _cg

# Alias this module to chat.gui so all attributes and monkeypatches target the same module.
sys.modules[__name__] = _cg

if __name__ == "__main__":
    _cg.main()
