from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class ClockStatus:
    trusted: bool
    reason: str


class ClockTrustSource(Protocol):
    def status(self) -> ClockStatus: ...
