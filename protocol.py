"""Protocol contracts and validators and backward-compatible alias for chat.protocol."""

import sys

import chat.protocol as _cp

sys.modules[__name__] = _cp
