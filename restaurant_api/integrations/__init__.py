"""External-service integration adapters.

Each sub-package exposes an abstract interface + a concrete implementation,
so business code depends on the contract rather than the SDK. Switching
LINE Messaging API → Telegram or LINE → CHATBOT_X is then a one-package
swap.
"""

from __future__ import annotations
