"""Socket.IO server entrypoint and backward-compatible alias for chat.server."""

import sys

import chat.server as _cs

sys.modules[__name__] = _cs

if __name__ == "__main__":
    _cs.main()
