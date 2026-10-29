from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ClockStatusCode(StrEnum):
    NTP_SYNCHRONIZED = 'ntp_synchronized'
    NTP_NOT_SYNCHRONIZED = 'ntp_not_synchronized'
    NTP_STATUS_MISSING = 'ntp_status_missing'
    NTP_STATUS_FAILED = 'ntp_status_failed'
    TRUSTED_BY_CONFIGURATION = 'trusted_by_configuration'


@dataclass(frozen=True, kw_only=True)
class ClockStatus:
    trusted: bool
    code: ClockStatusCode
    reason: str


class ClockTrustSource(Protocol):
    def status(self) -> ClockStatus: ...
