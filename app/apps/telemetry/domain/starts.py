from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, kw_only=True)
class TeploStart:
    boot_id: str | None
    last_activity: datetime | None
    router_uptime: timedelta | None


@dataclass(frozen=True, kw_only=True)
class BootOpening:
    start: TeploStart
    previous_boot_id: str | None


def boot_opening(current: TeploStart, earlier: Iterable[TeploStart]) -> BootOpening:
    opening = current
    for start in earlier:
        if current.boot_id is None or start.boot_id != current.boot_id:
            return BootOpening(start=opening, previous_boot_id=start.boot_id)

        opening = start
    return BootOpening(start=opening, previous_boot_id=None)
