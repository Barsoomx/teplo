from apps.system.domain.clock import ClockStatus, ClockStatusCode


class ClockStub:
    def __init__(self, trusted: bool) -> None:
        self._trusted = trusted

    def status(self) -> ClockStatus:
        return ClockStatus(
            trusted=self._trusted,
            code=ClockStatusCode.NTP_SYNCHRONIZED if self._trusted else ClockStatusCode.NTP_NOT_SYNCHRONIZED,
            reason='stub',
        )
