"""Speech recognition adapters and backward-compatible alias for chat.speech."""

import sys

import chat.speech as _csp

sys.modules[__name__] = _csp
