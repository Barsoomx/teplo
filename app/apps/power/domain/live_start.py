import math
from dataclasses import dataclass, replace

PENDING_START_LIMIT = 60.0


@dataclass(frozen=True, kw_only=True)
class LiveStart:
    permitted_at: float
    sent_at: float | None

    @classmethod
    def sent(cls, at: float) -> 'LiveStart':
        return cls(permitted_at=at, sent_at=at)

    def sent_by(self, at: float) -> 'LiveStart':
        if at < self.permitted_at or (self.sent_at is not None and at < self.sent_at):
            return self

        return replace(self, sent_at=at)

    def counted_until_read_after(self, at: float) -> float | None:
        if self.sent_at is not None:
            return self.sent_at

        if at - self.permitted_at < PENDING_START_LIMIT:
            return math.inf

        return None
