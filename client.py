"""Client session entrypoint and backward-compatible alias for chat.client."""

import sys

import chat.client as _cc

sys.modules[__name__] = _cc

if __name__ == "__main__":
    _cc.main()
