"""Registry for typed deterministic query handlers."""

from collections.abc import Callable
from typing import Any


QueryHandler = Callable[[str, Any, str], str]


class QueryHandlerRegistry:
    """Map planner intents to deterministic handler callables."""

    def __init__(self):
        self._handlers: dict[str, QueryHandler] = {}

    def register(self, intent: str, handler: QueryHandler) -> None:
        if intent in self._handlers:
            raise ValueError(f"Query handler already registered: {intent}")
        self._handlers[intent] = handler

    def get(self, intent: str) -> QueryHandler | None:
        return self._handlers.get(intent)

    def intents(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
