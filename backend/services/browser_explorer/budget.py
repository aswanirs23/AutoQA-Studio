"""Hard budget enforcement for the AI explorer.

Counts actions, pages visited, wall-clock seconds, and LLM tokens. Raises
``BudgetExceeded`` the moment any cap is hit so the orchestrator can stop the
loop and finalize whatever evidence has been gathered. Caps are read once at
construction and never relaxed at runtime — the LLM cannot influence them.
"""

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when any budget cap is hit. ``reason`` names which one."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Budget:
    max_actions: int
    max_pages: int
    max_seconds: int
    max_tokens: int = 200_000

    actions: int = 0
    pages: int = 0
    tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def record_action(self) -> None:
        self.actions += 1
        self._check()

    def record_page(self) -> None:
        self.pages += 1
        self._check()

    def record_tokens(self, n: int) -> None:
        self.tokens += max(0, int(n))
        self._check()

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def _check(self) -> None:
        if self.actions >= self.max_actions:
            raise BudgetExceeded(f"max_actions ({self.max_actions})")
        if self.pages >= self.max_pages:
            raise BudgetExceeded(f"max_pages ({self.max_pages})")
        if self.elapsed_seconds() >= self.max_seconds:
            raise BudgetExceeded(f"max_seconds ({self.max_seconds})")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"max_tokens ({self.max_tokens})")

    def snapshot(self) -> dict:
        return {
            "actions": self.actions,
            "max_actions": self.max_actions,
            "pages": self.pages,
            "max_pages": self.max_pages,
            "tokens": self.tokens,
            "max_tokens": self.max_tokens,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "max_seconds": self.max_seconds,
        }
