"""Check implementations and their shared result type."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Kind = Literal["cert", "domain"]


@dataclass
class CheckResult:
    """Outcome of one check, or the stored latest result for a domain and kind.

    - ``expires_at``: aware UTC datetime, ``None`` if unknown or error.
    - ``error``: ``None`` on success (including a domain-kind "unknown" result),
      otherwise a short message.
    - ``details``: see ARCHITECTURE.md §3 (``checks.details``) for the keys per kind.
    """

    kind: Kind
    expires_at: datetime | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime | None = None
